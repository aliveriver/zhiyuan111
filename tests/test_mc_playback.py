"""Offline only: injected transport, no ROS imports, SSH, or robot backend."""
import asyncio
import copy
import csv
import io
import json
import threading
import time

import pytest

from x2_ps5_teleop.robot.mc_animation import ARM_NAMES, positions
from x2_ps5_teleop.robot.mc_playback import (
    CHECKS, CONFIG_FILES, LIBRARIES, MCPlayback, ObservedSequence, load_profile,
    remaining_frames, runtime_test_profile,
)
from x2_ps5_teleop.bridge.controller import BridgeController, BridgeError
from x2_ps5_teleop.robot.motion import MockRobot


def frame(t=0, delta=0):
    return {'type': 'upper_body', 't_ms': t,
            'arm': [{'name': n, 'position': delta} for n in ARM_NAMES],
            'left_hand': [{'name': '', 'position': 0} for _ in range(10)],
            'right_hand': [{'name': '', 'position': 0} for _ in range(10)]}


def profile():
    # Simulation sequences and values are deliberately not a commissioning report.
    return dict(max_speed=.25, max_clip_seconds=60, start_tolerance_rad=.01,
                hold_tolerance_rad=.02, stable_tolerance_rad=.003,
                hold_snapshot_max_age_s=.25, max_joint_speed_rad_s=.4,
                transition_timeout_s=.15, play_sequence=['playing', 'idle'],
                hold_sequence=['playing', 'idle'])


def test_runtime_test_profile_opens_conservative_uncommissioned_backend():
    runtime = runtime_test_profile()
    assert runtime['commissioned'] is False
    assert runtime['max_speed'] == .25
    assert runtime['max_joint_speed_rad_s'] == .1
    assert runtime['waist_policy'] == 'mc_balanced'
    assert runtime['play_sequence'] is None and runtime['hold_sequence'] is None

    sequence = ObservedSequence(None)
    assert not sequence.update({'player': 'idle', 'events': []})
    assert not sequence.update({'player': 'pre_playing', 'events': []})
    assert not sequence.update({'player': 'playing', 'events': []})
    assert sequence.update({'player': 'idle', 'events': []})

    with pytest.raises(RuntimeError, match='PLAYING'):
        ObservedSequence(None).update({
            'player': 'idle',
            'events': [{'player': 'pre_playing', 'at': time.monotonic()},
                       {'player': 'idle', 'at': time.monotonic()}],
        })


class SimIO:
    def __init__(self):
        self.pose = frame()
        self.clips = {}
        self.plays = []
        self.events = []
        self.active = None
        self.upload_entered = threading.Event()
        self.upload_release = threading.Event()
        self.upload_release.set()
        self.timeout_play = False
        self.timeout_hold = False
        self.no_hold_feedback = False
        self.drift_on_hold_upload = False
        self.slow_hold_upload = False
        self.bad_state = False
        self.threads = set()

    def verify(self):
        self.threads.add(threading.get_ident())

    def upload(self, animation):
        self.threads.add(threading.get_ident())
        self.upload_entered.set()
        assert self.upload_release.wait(3), 'test upload gate timeout'
        rows = list(csv.DictReader(io.StringIO(animation.content.decode())))
        self.clips[str(len(self.clips))] = rows
        if self.plays and self.drift_on_hold_upload:
            self.pose = frame(delta=.2)
            self.active = None
        if self.plays and self.slow_hold_upload:
            time.sleep(.28)
        return str(len(self.clips) - 1)

    def play(self, path, interrupt=False):
        self.threads.add(threading.get_ident())
        rows = self.clips[path]
        self.plays.append((path, interrupt))
        self.events = []
        if interrupt and self.no_hold_feedback:
            self.active = None
        else:
            duration = .04 if interrupt else int(rows[-1]['timeMS']) / 1000
            target = frame(delta=float(rows[-1]['command_pos::left_elbow_joint']))
            self.active = (time.monotonic(), duration, copy.deepcopy(self.pose), target)
            self.events = [{'player': 'playing', 'at': time.monotonic()}]
        if (interrupt and self.timeout_hold) or (not interrupt and self.timeout_play):
            raise TimeoutError('simulated lost RPC response')

    def state(self):
        self.threads.add(threading.get_ident())
        if self.bad_state:
            raise RuntimeError('stale feedback')
        player = 'idle'
        if self.active:
            start, duration, first, last = self.active
            ratio = min(1, (time.monotonic() - start) / duration)
            for a, b, out in zip(first['arm'], last['arm'], self.pose['arm']):
                out['position'] = a['position'] + ratio * (b['position'] - a['position'])
            if ratio == 1:
                self.active = None
            else:
                player = 'playing'
        events, self.events = self.events, []
        return {'player': player, 'frame': copy.deepcopy(self.pose), 'events': events}

    def close(self):
        self.threads.add(threading.get_ident())


async def until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, 'simulation timed out'
        await asyncio.sleep(.005)


def actor(sim, **kwargs):
    return MCPlayback(profile(), lambda _: sim, **kwargs)


def test_complete_repeat_and_thread_ownership(tmp_path):
    async def run():
        sim = SimIO()
        mc = actor(sim, interlock_path=tmp_path / 'latch')
        for _ in range(2):
            mc.start([frame(), frame(40)], .25)
            await mc.task
            assert mc.state == 'completed'
            assert mc.progress_ms == 40
            assert not (tmp_path / 'latch').exists()
        await mc.close()
        assert len(sim.threads) == 1
        assert threading.get_ident() not in sim.threads
    asyncio.run(run())


@pytest.mark.parametrize('intent', ['stop', 'cancel'])
def test_upload_cancellation_never_launches(intent):
    async def run():
        sim = SimIO()
        sim.upload_release.clear()
        mc = actor(sim)
        mc.start([frame(), frame(100)], .25)
        await until(sim.upload_entered.is_set)
        if intent == 'stop':
            mc.stop()
        else:
            mc.task.cancel()
        sim.upload_release.set()
        await mc.task
        assert mc.state == 'idle'
        assert sim.plays == []
        await mc.close()
    asyncio.run(run())


@pytest.mark.parametrize('hold_timeout', [False, True])
def test_rpc_timeout_still_stops_and_uses_feedback(hold_timeout):
    async def run():
        sim = SimIO()
        sim.timeout_play = True
        sim.timeout_hold = hold_timeout
        mc = actor(sim)
        mc.start([frame(), frame(300)], .25)
        await mc.task
        assert mc.state == 'error'
        assert [interrupt for _, interrupt in sim.plays] == [False, True]
        assert not mc.possibly_moving
        await mc.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['no_hold_feedback', 'drift_on_hold_upload', 'slow_hold_upload', 'bad_state'])
def test_stop_failure_stays_locked_and_retry_requires_feedback(tmp_path, fault):
    async def run():
        sim = SimIO()
        latch = tmp_path / 'latch'
        mc = actor(sim, interlock_path=latch)
        mc.start([frame(), frame(300)], .25)
        await until(lambda: mc.state == 'playing')
        setattr(sim, fault, True)
        mc.stop()
        await mc.task
        assert mc.state == 'stop_failed'
        assert mc.busy and latch.exists()
        with pytest.raises(ValueError):
            mc.start([frame(), frame(100)], .25)
        restored = actor(SimIO(), interlock_path=latch)
        assert restored.state == 'stop_failed' and restored.busy
        restored.pool.shutdown()
        setattr(sim, fault, False)
        mc.stop()
        await mc.task
        assert mc.state == 'idle' and not mc.busy and not latch.exists()
        await mc.close()
    asyncio.run(run())


def test_pause_resume_uploads_only_remaining_clip_and_stop_during_pause():
    async def run():
        sim = SimIO()
        mc = actor(sim)
        mc.start([frame(), frame(300, .02)], .25)
        await until(lambda: mc.progress_ms > 20)
        mc.pause()
        await until(lambda: mc.state == 'paused')
        saved = mc.progress_ms
        assert saved < 300
        mc.resume()
        await until(lambda: len(sim.plays) == 3)
        resumed = sim.clips[sim.plays[-1][0]]
        assert int(resumed[-1]['timeMS']) < 1200
        assert float(resumed[0]['command_pos::left_elbow_joint']) > 0
        mc.stop()
        await mc.task
        assert mc.state == 'idle'
        await mc.close()
    asyncio.run(run())


def test_resume_rejects_estimated_progress_pose_mismatch():
    async def run():
        sim = SimIO()
        mc = actor(sim)
        mc.start([frame(), frame(300, .02)], .25)
        await until(lambda: mc.state == 'playing')
        mc.pause()
        await until(lambda: mc.state == 'paused')
        # Simulate the estimate differing from the measured held position.
        mc.progress_ms = 290
        mc.resume()
        await mc.task
        assert mc.state == 'error'
        assert '起点' in mc.error
        assert len(sim.plays) == 2
        await mc.close()
    asyncio.run(run())


def test_remaining_frames_normalizes_named_arm_order_without_mutation():
    a, b = frame(), frame(100, .02)
    b['arm'].reverse()
    frames = [a, b]
    original = copy.deepcopy(frames)
    result = remaining_frames(frames, 25)
    assert result[0]['t_ms'] == 0 and result[-1]['t_ms'] == 75
    assert positions(result[0])['left_elbow_joint'] == pytest.approx(.005)
    assert frames == original


@pytest.mark.parametrize('fault', ['start', 'velocity', 'duration', 'speed', 'nan', 'sequence'])
def test_rejects_invalid_playback_before_motion_or_stops_on_bad_sequence(fault):
    async def run():
        sim = SimIO()
        mc = actor(sim)
        frames = [frame(), frame(100)]
        speed = .25
        if fault == 'start':
            sim.pose = frame(delta=.2)
        elif fault == 'velocity':
            frames[-1] = frame(100, .5)
        elif fault == 'duration':
            frames[-1]['t_ms'] = 100000
        elif fault == 'speed':
            speed = 1
        elif fault == 'nan':
            frames[-1]['arm'][0]['position'] = float('nan')
        elif fault == 'sequence':
            mc.profile['play_sequence'] = ['pre_playing', 'playing', 'idle']
        try:
            mc.start(frames, speed)
        except ValueError:
            assert fault in ('duration', 'speed')
        else:
            await mc.task
            assert mc.state == 'error'
        assert len(sim.plays) == (2 if fault == 'sequence' else 0)
        await mc.close()
    asyncio.run(run())


@pytest.mark.parametrize('trigger', ['stop', 'disconnect', 'disarm', 'timeout', 'estop', 'close', 'replace'])
def test_bridge_safety_triggers_stop_mc_without_blocking_heartbeat(trigger):
    async def run():
        sim = SimIO()
        sim.upload_release.clear()
        mc = actor(sim)
        controller = BridgeController(MockRobot(io.StringIO()), mc_playback=mc)
        await controller.register('s', 'c')
        await controller.handle('s', {'type': 'arm', 'enabled': True, 'sequence': 1}, now=1)
        controller.trajectories['clip'] = [frame(), frame(300)]
        await controller.handle('s', {'type': 'trajectory_play', 'name': 'clip', 'speed': .25, 'sequence': 2}, now=1)
        await until(sim.upload_entered.is_set)
        state = await asyncio.wait_for(controller.handle('s', {'type': 'heartbeat', 'sequence': 3}, now=1.1), .1)
        assert state['playback_state'] == 'preparing'
        sim.upload_release.set()
        await until(lambda: mc.state == 'playing')
        if trigger == 'disconnect':
            await controller.disconnect('s')
        elif trigger == 'replace':
            await controller.register('new', 'c')
        elif trigger == 'timeout':
            assert await controller.watchdog(now=2)
        elif trigger == 'close':
            await controller.close()
        else:
            payload = {'stop': {'type': 'trajectory_play', 'command': 'stop'},
                       'disarm': {'type': 'arm', 'enabled': False},
                       'estop': {'type': 'estop'}}[trigger]
            await controller.handle('s', {**payload, 'sequence': 4}, now=1.2)
        await mc.task
        assert mc.state == 'idle'
        assert [interrupt for _, interrupt in sim.plays] == [False, True]
        if trigger != 'close':
            await controller.close()
    asyncio.run(run())


def test_stop_failed_interlocks_all_motion_even_after_clear_estop():
    async def run():
        sim = SimIO()
        mc = actor(sim)
        controller = BridgeController(MockRobot(io.StringIO()), mc_playback=mc)
        await controller.register('s', 'c')
        await controller.handle('s', {'type': 'arm', 'enabled': True, 'sequence': 0})
        mc.state = 'stop_failed'
        mc.possibly_moving = True
        commands = [dict(type='mode', mode='walk'), dict(type='preset', action='grip'),
                    dict(type='hand_positions', side='right', positions=[0]*10),
                    dict(type='trajectory_record_start', name='x'),
                    dict(type='trajectory_play', name='x'), dict(type='arm', enabled=True)]
        for seq, payload in enumerate(commands, 1):
            with pytest.raises(BridgeError, match='MC|停止|播放'):
                await controller.handle('s', {**payload, 'sequence': seq})
        snapshot = await controller.snapshot()
        assert snapshot.playback_state == 'stop_failed'
        await controller.close()
    asyncio.run(run())


def test_profile_requires_review_and_complete_field_evidence(tmp_path):
    import hashlib
    report = tmp_path / 'report.md'
    report.write_text('SIMULATION ONLY')
    data = {**profile(), 'firmware': 'Agi v0.9.7', 'reviewed_by': 'test',
            'report_file': 'report.md', 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
            'remote_sha256': {**LIBRARIES, **{p: '0'*64 for p in CONFIG_FILES}},
            'checks': {key: True for key in CHECKS}, 'ssh_host': 'run@simulator',
            'player_enum_values': dict(IDLE=0, PRE_PLAYING=1, PLAYING=2, INTERRUPTING=3, ERROR=4)}
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(data))
    assert load_profile(path)['max_speed'] == .25
    for key in ['checks', 'report_sha256', 'remote_sha256', 'player_enum_values', 'hold_sequence']:
        broken = {**data, key: {} if key in ('checks', 'remote_sha256', 'player_enum_values') else None}
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
            load_profile(path)


def test_stop_during_inflight_rpc_keeps_actor_alive_until_hold():
    async def run():
        sim = SimIO()
        entered, release = threading.Event(), threading.Event()
        original = sim.play
        def blocked_play(path, interrupt=False):
            if not interrupt:
                entered.set()
                assert release.wait(3)
            return original(path, interrupt)
        sim.play = blocked_play
        mc = actor(sim)
        mc.start([frame(), frame(300)], .25)
        await until(entered.is_set)
        mc.stop()
        assert mc.state == 'stopping' and not mc.task.done()
        release.set()
        await mc.task
        assert mc.state == 'idle' and sim.plays[-1][1]
        await mc.close()
    asyncio.run(run())


def test_stop_overrides_pause_while_hold_uploads():
    async def run():
        sim = SimIO()
        mc = actor(sim)
        mc.start([frame(), frame(300)], .25)
        await until(lambda: mc.state == 'playing')
        sim.upload_entered.clear()
        sim.upload_release.clear()
        mc.pause()
        await until(sim.upload_entered.is_set)
        mc.stop()
        sim.upload_release.set()
        await mc.task
        assert mc.state == 'idle'
        assert len(sim.plays) == 2
        await mc.close()
    asyncio.run(run())


def test_paused_feedback_loss_persists_interlock_and_close_reports_failure(tmp_path):
    async def run():
        sim = SimIO()
        latch = tmp_path / 'latch'
        mc = actor(sim, interlock_path=latch)
        mc.start([frame(), frame(300)], .25)
        await until(lambda: mc.state == 'playing')
        mc.pause()
        await until(lambda: mc.state == 'paused')
        sim.bad_state = True
        await mc.task
        assert mc.state == 'stop_failed' and latch.exists()
        with pytest.raises(RuntimeError, match='stale'):
            await mc.close()
        assert latch.exists()
    asyncio.run(run())


def test_server_uses_runtime_profile_without_commissioning_and_preserves_interlock(tmp_path):
    from x2_ps5_teleop.bridge import server
    path = tmp_path / 'profile.json'
    path.write_text('{}')
    with pytest.raises(ValueError):
        server.resolve_playback_profile('x2', path)

    runtime = server.resolve_playback_profile('x2', None)
    assert runtime is not None and runtime['commissioned'] is False
    with pytest.raises(ValueError, match='Mock'):
        server.resolve_playback_profile('mock', path)

    interlock = tmp_path / 'mc-motion-unconfirmed.lock'
    interlock.touch()
    playback = MCPlayback(runtime, lambda _: SimIO(), interlock_path=interlock)
    assert playback.state == 'stop_failed'
    assert playback.busy
    assert interlock.exists()
    asyncio.run(playback.close())
    assert not interlock.exists()


def test_trial_default_only_generates_local_preview(tmp_path, monkeypatch):
    import sys
    from x2_ps5_teleop.robot import mc_playback_trial as trial
    path = tmp_path / 'trajectories.json'
    original = json.dumps({'clip': [frame(), frame(300)]})
    path.write_text(original)
    output = tmp_path / 'preview'
    def forbidden(*args, **kwargs):
        pytest.fail('default trial must not execute')
    monkeypatch.setattr(trial, 'execute_trial', forbidden)
    monkeypatch.setattr(sys, 'argv', ['trial', '--trajectory-file', str(path), '--name', 'clip', '--output', str(output)])
    trial.main()
    report = json.loads((output / 'report.json').read_text())
    assert not report['execution_requested'] and not report['commissioned']
    assert path.read_text() == original


@pytest.mark.parametrize('mode', ['complete', 'stop', 'pause-resume', 'disconnect', 'timeout'])
def test_attended_trial_runner_with_simulated_io(tmp_path, monkeypatch, mode):
    from argparse import Namespace
    from x2_ps5_teleop.robot import mc_playback_trial as trial
    sim = SimIO()
    mc = actor(sim)
    monkeypatch.setattr(trial, 'MCPlayback', lambda *a, **kw: mc)
    args = Namespace(mode=mode, after=.1, output=tmp_path, interlock=tmp_path / 'latch')
    report = {'states': []}
    asyncio.run(trial.execute_trial(args, [frame(), frame(300)], profile(), report))
    assert report['execution_requested']
    assert report['result'] == ('completed' if mode in ('complete', 'pause-resume') else 'idle')
    assert not mc.possibly_moving
