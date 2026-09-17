"""Commissioning-gated MC playback actor; blocking I/O never owns the bridge lock.

The actor alone owns its transport and ROS executor. Cancellation requests are
flags, not Task.cancel(): a timed-out RPC may already have started robot motion.
"""
from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
from pathlib import Path
import time
from typing import Any
import uuid

from .mc_animation import all_positions, compile_animation, hold_animation, positions, finite

BUSY = frozenset({'preparing', 'playing', 'pausing', 'paused', 'stopping', 'stop_failed'})
LIBRARIES = {
    '/agibot/software/mc/lib/libmc_module_main_service.so':
        'a708839613a7bd4da70683e3ade185d5c5baec7bd5eeb29b58b3d5ca66d093a4',
    '/agibot/software/mc/lib/libmc_runner_animation_player.so.0.0.0':
        'cb71131a23039d20af2e346e004064a3a780aed7cd3000dfbec8705643e6b477',
}
CONFIG_FILES = tuple('/agibot/software/mc_param/robot/lx2501_3_t2d5/' + p for p in (
    'mc.yaml', 'action_setting.yaml', 'classic/animation_player.yaml'))
CHECKS = ('single_joint', 'hold_stop', 'pause_resume', 'repeat_playback',
          'disconnect_stop', 'legs_waist_unchanged', 'status_sequence', 'full_clip_025x')
WAIST_POLICIES = frozenset({'unchanged', 'mc_balanced'})


def load_profile(path: Path, *, commissioning_trial: bool = False) -> dict[str, Any]:
    """A reviewed field report is required; no --force/boolean enable switch."""
    profile = json.loads(path.read_text())
    if profile.get('firmware') != 'Agi v0.9.7' or not profile.get('reviewed_by'):
        raise ValueError('MC 配置需要固件版本及现场验收人')
    waist_policy = profile.get('waist_policy', 'unchanged')
    if waist_policy not in WAIST_POLICIES:
        raise ValueError('waist_policy 必须为 unchanged 或 mc_balanced')
    required = ('single_joint', 'hold_stop', 'status_sequence')
    required += ('waist_bounded' if waist_policy == 'mc_balanced' else 'legs_waist_unchanged',)
    if not commissioning_trial:
        required = CHECKS if waist_policy == 'unchanged' else tuple(k for k in CHECKS if k != 'legs_waist_unchanged') + ('waist_bounded',)
    if any(profile.get('checks', {}).get(key) is not True for key in required):
        raise ValueError('MC 现场验收未完成；离线测试不能替代实机停止验证')
    report = path.parent / profile['report_file']
    if hashlib.sha256(report.read_bytes()).hexdigest() != profile.get('report_sha256'):
        raise ValueError('MC 现场报告校验失败')
    hashes = profile.get('remote_sha256', {})
    if any(hashes.get(p) != digest for p, digest in LIBRARIES.items()):
        raise ValueError('MC 库版本与已调查版本不匹配')
    for name in CONFIG_FILES:
        digest = hashes.get(name, '')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('MC 配置文件哈希不完整')
    for name, maximum in (('start_tolerance_rad', .05), ('hold_tolerance_rad', .03),
                          ('max_speed', .25), ('max_joint_speed_rad_s', .4),
                          ('transition_timeout_s', 10), ('max_clip_seconds', 60),
                          ('stable_tolerance_rad', .003), ('hold_snapshot_max_age_s', .25)):
        value = finite(profile.get(name))
        if not 0 < value <= maximum:
            raise ValueError(f'MC 验收参数 {name} 必须在 (0, {maximum}] 内')
    if waist_policy == 'mc_balanced':
        bound = finite(profile.get('waist_bound_rad'))
        if not 0 < bound <= .5:
            raise ValueError('mc_balanced 策略需要 0 < waist_bound_rad <= 0.5')
    host = profile.get('ssh_host', '')
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9_.@:-]+', host) or host.startswith('-'):
        raise ValueError('MC 配置需要有效的 SSH 主机')
    enums = profile.get('player_enum_values', {})
    if set(enums) != {'IDLE', 'PRE_PLAYING', 'PLAYING', 'INTERRUPTING', 'ERROR'} or any(
            type(v) is not int for v in enums.values()) or len(set(enums.values())) != len(enums):
        raise ValueError('需要现场核对的 McPlayerState 枚举值')
    for key in ('play_sequence', 'hold_sequence'):
        seq = profile.get(key)
        if (not isinstance(seq, list) or len(seq) < 2 or seq[-1] != 'idle'
                or seq[0] == 'idle' or 'playing' not in seq
                or any(x not in ('idle', 'pre_playing', 'playing', 'interrupting') for x in seq)
                or 'idle' in seq[:-1] or any(a == b for a, b in zip(seq, seq[1:]))):
            raise ValueError(f'{key} 必须填写实际观测的去重状态序列，以 idle 结束')
    return profile


def remaining_frames(frames, progress_ms):
    """Interpolate a new first frame; never restart the original full clip."""
    result = []
    for index, frame in enumerate(frames):
        if frame['t_ms'] > progress_ms:
            left = frames[max(0, index - 1)]
            first = copy.deepcopy(left)
            span = frame['t_ms'] - left['t_ms']
            ratio = max(0, (progress_ms - left['t_ms']) / span) if span else 0
            include_waist = "waist" in left or "waist" in frame
            left_positions, right_positions = all_positions(left, include_waist=include_waist), all_positions(frame, include_waist=include_waist)
            # Normalize arm order by name; hands remain their official slot order.
            for part in ('arm', 'left_hand', 'right_hand'):
                for j, item in enumerate(first[part]):
                    if part == 'arm':
                        name = item['name']
                        item['position'] = left_positions[name] + ratio * (right_positions[name] - left_positions[name])
                    else:
                        item['position'] += ratio * (frame[part][j]['position'] - item['position'])
            if include_waist:
                waist = {item['name']: item for item in first['waist']}
                for name in right_positions:
                    if name in waist:
                        waist[name]['position'] = left_positions[name] + ratio * (right_positions[name] - left_positions[name])
            first['t_ms'] = 0
            result = [first] + [{**copy.deepcopy(f), 't_ms': f['t_ms'] - progress_ms} for f in frames[index:]]
            break
    return result


class RosAnimationIO:
    """Constructed and used exclusively on one dedicated worker thread."""
    def __init__(self, profile):
        from .mc_animation_probe import Probe
        self.profile = profile
        self.probe = Probe()
        from aimdk_msgs.msg import McPlayerState
        if any(getattr(McPlayerState, name, None) != value
               for name, value in profile['player_enum_values'].items()):
            self.probe.close()
            raise RuntimeError('安装的 MC 枚举与现场验收不一致')
        self.events = []
        self.include_waist = False
        self.probe.mc_observer = self._observe
        self.directory = '/tmp/x2-mc-playback/' + uuid.uuid4().hex

    def _observe(self, msg):
        player = self._player(msg)
        if not self.events or self.events[-1]['player'] != player:
            if len(self.events) >= 128:
                raise RuntimeError('MC 状态变化过多')
            self.events.append({'player': player, 'at': time.monotonic()})

    def _player(self, msg):
        labels = {v: k.lower() for k, v in self.profile['player_enum_values'].items()}
        player = labels.get(msg.motion_status.player_state.value)
        if player is None or player == 'error':
            raise RuntimeError('MC 动画状态未知或错误')
        return player

    def verify(self):
        import shlex
        import subprocess
        host = self.profile['ssh_host']
        import re
        if not re.fullmatch(r'[a-zA-Z0-9_.@:-]+', host) or host.startswith('-'):
            raise ValueError('SSH 主机格式无效')
        expected = {p: self.profile['remote_sha256'][p] for p in (*LIBRARIES, *CONFIG_FILES)}
        code = ('import hashlib,json; from pathlib import Path; '
                f'print(json.dumps({{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in {list(expected)!r}}}))')
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=2',
                                 host, 'python3 -c ' + shlex.quote(code)],
                                capture_output=True, check=True, timeout=4)
        if json.loads(result.stdout) != expected:
            raise RuntimeError('MC 实机库或配置已变化，必须重新验收')
        self.probe.require_standing()

    def upload(self, animation):
        from .mc_animation_probe import upload
        path = self.directory + '/' + uuid.uuid4().hex + '.csv'
        upload(animation.content, self.profile['ssh_host'], None, path)
        return path

    def state(self):
        from aimdk_msgs.msg import McActionStatus, McControlArea
        p = self.probe
        p.wait(.02)
        mc = p.latest.get('mc')
        if mc is None or time.monotonic() - p.received.get('mc', 0) > .25:
            raise RuntimeError('MC 状态超过 250 ms，不能确认停止')
        if mc.action_info.action_desc != 'STAND_DEFAULT' or mc.action_info.status.value != McActionStatus.RUNNING:
            raise RuntimeError('MC 已离开稳定站立动作')
        player = self._player(mc)
        area = McControlArea.LEFT_HAND | McControlArea.RIGHT_HAND
        if player != 'idle' and mc.motion_status.control_area.value != area:
            raise RuntimeError('MC 动画控制区域与已验收链路不一致')
        events, self.events = self.events, []
        frame = p.frame(include_waist=self.include_waist) if self.include_waist else p.frame()
        return {'player': player, 'frame': frame, 'events': events}

    def play(self, path, interrupt=False):
        self.events.clear()
        return self.probe.play(path, interrupt)

    def close(self):
        self.probe.close()


class StopUnconfirmed(RuntimeError):
    """A failed hold attempt must remain latched until an explicit retry."""


class ObservedSequence:
    """Match the reviewed, de-duplicated sequence, including events during RPC."""
    def __init__(self, expected):
        self.expected = expected
        self.seen = []
        self.playing_at = None

    def update(self, status):
        events = status.get('events', []) + [{'player': status['player'], 'at': time.monotonic()}]
        for event in events:
            player = event['player']
            if not self.seen and player == 'idle':
                continue
            if self.seen and self.seen[-1] == player:
                continue
            if len(self.seen) >= len(self.expected) or player != self.expected[len(self.seen)]:
                raise RuntimeError('MC 状态序列与现场验收不符')
            self.seen.append(player)
            if player == 'playing' and self.playing_at is None:
                self.playing_at = event['at']
        return self.seen == self.expected


class MCPlayback:
    def __init__(self, profile, io_factory=RosAnimationIO, *, interlock_path=None):
        self.profile = profile
        self.io_factory = io_factory
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='x2-mc')
        self.io = None
        self.task = None
        self.state = 'idle'
        self.error = None
        self.progress_ms = 0
        self.duration_ms = 0
        self.intent = 'play'
        self.possibly_moving = False
        self.frames = []
        self.speed = .25
        self.interlock_path = Path(interlock_path) if interlock_path else None
        self.closed = False
        if self.interlock_path and self.interlock_path.exists():
            self.possibly_moving = True
            self.state = 'stop_failed'
            self.error = '上次 MC 执行未确认停止；禁止其他动作，请现场处理并重试停止'

    def _mark_motion(self):
        if self.interlock_path:
            self.interlock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.interlock_path.open('w') as stream:
                stream.write('MC motion may still be running; require feedback-confirmed stop.\n')
                stream.flush()
                os.fsync(stream.fileno())
        self.possibly_moving = True

    def _confirmed_stop(self):
        if self.interlock_path:
            self.interlock_path.unlink(missing_ok=True)
        self.possibly_moving = False

    @property
    def busy(self):
        return self.state in BUSY or (self.task is not None and not self.task.done())

    def start(self, frames, speed):
        if self.closed:
            raise ValueError('MC 后端已关闭')
        if self.busy:
            raise ValueError('MC 尚未确认停止')
        speed = finite(speed)
        if not .01 <= speed <= self.profile['max_speed']:
            raise ValueError(f"当前验收最高速度 {self.profile['max_speed']}x")
        # Heavy CSV work is done by the worker; bounded validation happens here.
        if len(frames) < 2:
            raise ValueError('动画需要至少两个状态帧')
        self.frames = copy.deepcopy(frames)
        origin = self.frames[0]['t_ms']
        for frame in self.frames:
            frame['t_ms'] -= origin
        if len(self.frames) < 2 or self.frames[-1]['t_ms'] <= 0:
            raise ValueError('动画需要至少两个不同时刻的状态')
        if self.frames[-1]['t_ms'] / speed > self.profile['max_clip_seconds'] * 1000:
            raise ValueError('轨迹超过本次验收的时长限制')
        self.speed = speed
        self.progress_ms = 0
        self.duration_ms = self.frames[-1]['t_ms']
        self.error = None
        self.intent = 'play'
        self.state = 'preparing'
        self.task = asyncio.create_task(self._run())

    def pause(self):
        if self.state != 'playing':
            raise ValueError('当前不能暂停')
        self.intent = 'pause'
        self.state = 'pausing'

    def resume(self):
        if self.state != 'paused':
            raise ValueError('当前不能继续')
        self.intent = 'play'
        self.state = 'preparing'

    def stop(self):
        if not self.busy:
            return
        self.intent = 'stop'
        self.state = 'stopping'
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._retry_stop())

    async def _call(self, fn, *args):
        # Wrapping the concurrent future explicitly keeps completion wakeups
        # reliable on Python 3.14 when no other asyncio task is runnable.
        future = asyncio.wrap_future(self.pool.submit(fn, *args))
        try:
            while not future.done():
                try:
                    return await asyncio.wait_for(asyncio.shield(future), .05)
                except asyncio.TimeoutError:
                    continue
            return future.result()
        except asyncio.CancelledError:
            # A thread/RPC cannot be cancelled. Finish it, then handle stop.
            self.intent = 'stop'
            self.state = 'stopping'
            while not future.done():
                try:
                    return await asyncio.wait_for(asyncio.shield(future), .05)
                except asyncio.TimeoutError:
                    continue
            return future.result()

    async def _ensure_io(self):
        if self.io is None:
            self.io = await self._call(self.io_factory, self.profile)

    def _validate_motion(self, frames):
        include_waist = "waist" in frames[0]
        previous = None
        for frame in frames:
            if ("waist" in frame) != include_waist:
                raise ValueError('腰部通道必须在每一帧同时存在或同时缺失')
            values = all_positions(frame, include_waist=include_waist)
            if any(abs(v) > 3.141593 for v in values.values()):
                raise ValueError('关节位置超出导出边界 ±π；此边界不代表机械限位')
            if previous:
                dt = (frame['t_ms'] - previous[0]) / 1000 / self.speed
                delta = max(abs(values[k] - previous[1][k]) for k in values)
                if dt < 0 or (dt == 0 and delta) or (dt > 0 and delta / dt > self.profile['max_joint_speed_rad_s']):
                    raise ValueError('轨迹相邻采样速度超过已验收范围')
            previous = (frame['t_ms'], values)
        return compile_animation(frames, self.speed, include_waist=include_waist)

    @staticmethod
    def _distance(first, second):
        include_waist = "waist" in first or "waist" in second
        a, b = all_positions(first, include_waist=include_waist), all_positions(second, include_waist=include_waist)
        return max(abs(a[k] - b[k]) for k in a)

    async def _launch(self, frames):
        self.io.include_waist = "waist" in frames[0]
        animation = await self._call(self._validate_motion, frames)
        if self.intent != 'play':
            return False
        await self._call(self.io.verify)
        if self.intent != 'play':
            return False
        path = await self._call(self.io.upload, animation)
        status = await self._call(self.io.state)
        if self.intent != 'play':
            return False
        if status['player'] != 'idle' or self._distance(status['frame'], frames[0]) > self.profile['start_tolerance_rad']:
            raise RuntimeError('MC 非空闲或当前位置距起点过远；请用官方工具准备起始姿态')
        await self._call(self._mark_motion)
        if self.intent != 'play':
            await self._call(self._confirmed_stop)
            return False
        # Set before RPC; even timeout needs a stop attempt.
        await self._call(self.io.play, path, False)
        return True

    async def _hold(self):
        try:
            return await self._hold_impl()
        except Exception as exc:
            raise StopUnconfirmed(str(exc)) from exc

    async def _hold_impl(self):
        """Hold replacement + observed idle and stable pose, not RPC success alone."""
        before = await self._call(self.io.state)
        pose = before['frame']
        captured = time.monotonic()
        animation = await self._call(hold_animation, pose)
        path = await self._call(self.io.upload, animation)
        current = await self._call(self.io.state)
        if (time.monotonic() - captured > self.profile['hold_snapshot_max_age_s']
                or self._distance(pose, current['frame']) > self.profile['hold_tolerance_rad']):
            raise RuntimeError('保持动画姿态已过期或上传期间位移过大，停止未确认；使用物理急停')
        await self._call(self._mark_motion)
        rpc_error = None
        try:
            await self._call(self.io.play, path, True)
        except Exception as exc:
            rpc_error = exc  # Observe feedback even when the replacement RPC times out.
        deadline = time.monotonic() + self.profile['transition_timeout_s'] + 1
        sequence = ObservedSequence(self.profile['hold_sequence'])
        stable_since = None
        stable_pose = None
        while time.monotonic() < deadline:
            status = await self._call(self.io.state)
            complete = sequence.update(status)
            if complete and status['player'] == 'idle' and self._distance(pose, status['frame']) <= self.profile['hold_tolerance_rad']:
                if stable_since is None or self._distance(stable_pose, status['frame']) > self.profile['stable_tolerance_rad']:
                    stable_since = time.monotonic()
                    stable_pose = status['frame']
                if time.monotonic() - stable_since >= .3:
                    await self._call(self._confirmed_stop)
                    return status['frame']
            else:
                stable_since = None
            await asyncio.sleep(.02)
        raise RuntimeError(f'保持动画未得到停止反馈确认；使用物理急停；RPC: {rpc_error}')

    async def _retry_stop(self):
        try:
            await self._ensure_io()
            await self._hold()
            self.state = 'idle'
            self.error = None
        except Exception as exc:
            self.state = 'stop_failed'
            self.error = str(exc)

    async def _run(self):
        try:
            await self._ensure_io()
            frames = self.frames
            while True:
                if self.intent == 'stop':
                    if self.possibly_moving:
                        await self._hold()
                    self.state = 'idle'
                    return
                if not await self._launch(frames):
                    continue
                base = self.progress_ms
                start = None
                deadline = time.monotonic() + self.profile['transition_timeout_s']
                sequence = ObservedSequence(self.profile['play_sequence'])
                while self.intent == 'play':
                    status = await self._call(self.io.state)
                    now = time.monotonic()
                    complete = sequence.update(status)
                    if start is None and sequence.playing_at is not None:
                        start = sequence.playing_at
                        if self.intent == 'play':
                            self.state = 'playing'
                    if status['player'] == 'playing':
                        self.progress_ms = min(self.duration_ms - 1, base + (now - start) * 1000 * self.speed)
                    elif complete and status['player'] == 'idle' and start is not None:
                        expected = (self.duration_ms - base) / 1000 / self.speed
                        if now - start < max(0, expected - .2):
                            raise RuntimeError('MC 提前结束，不能判定轨迹播放完成')
                        if self._distance(status['frame'], frames[-1]) > self.profile['start_tolerance_rad']:
                            raise RuntimeError('MC 空闲但末端姿态未到达')
                        await self._call(self._confirmed_stop)
                        self.progress_ms = self.duration_ms
                        self.state = 'completed'
                        return
                    if start is None and now > deadline:
                        raise RuntimeError('MC 未在验收时限内开始播放')
                    if start is not None and now - start > (self.duration_ms - base) / 1000 / self.speed + self.profile['transition_timeout_s']:
                        raise RuntimeError('MC 动画结束反馈超时')
                    await asyncio.sleep(.02)
                paused_pose = await self._hold()
                if self.intent == 'stop':
                    self.state = 'idle'
                    return
                self.state = 'paused'
                while self.intent == 'pause':
                    try:
                        status = await self._call(self.io.state)
                        if (status['player'] != 'idle' or
                                self._distance(paused_pose, status['frame']) > self.profile['hold_tolerance_rad']):
                            raise RuntimeError('暂停时状态或姿态变化，停止需重新确认')
                    except Exception:
                        await self._call(self._mark_motion)
                        raise
                    await asyncio.sleep(.05)
                frames = remaining_frames(self.frames, self.progress_ms)
                if not frames:
                    raise RuntimeError('无可继续的剩余轨迹')
        except (Exception, asyncio.CancelledError) as exc:
            self.error = str(exc) or 'MC 工作任务被取消'
            if isinstance(exc, StopUnconfirmed):
                self.state = 'stop_failed'
                return
            if self.possibly_moving:
                self.state = 'stopping'
                try:
                    await self._hold()
                except Exception as stop_exc:
                    self.error += '; 停止未确认: ' + str(stop_exc)
                    self.state = 'stop_failed'
                    return
            self.state = 'error'

    async def close(self):
        self.stop()
        if self.task:
            await asyncio.shield(self.task)
        if self.io:
            await self._call(self.io.close)
        self.closed = True
        self.pool.shutdown(wait=False)
        if self.state == 'stop_failed':
            raise RuntimeError(self.error)
