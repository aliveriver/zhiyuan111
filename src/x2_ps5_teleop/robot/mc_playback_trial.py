"""Attended acceptance of the gated MC backend; default is local CSV only.

A reviewed single-joint/hold-stop report is required before execution. This
one-shot runner cannot enable normal App playback or edit the acceptance report.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

from .mc_animation import compile_animation
from .mc_playback import MCPlayback, RosAnimationIO, load_profile


class TrialRobot:
    """Controller lease adapter only: no ROS construction or HAL publishing."""
    def stop(self):
        pass  # Official standing was prepared manually; MC actor handles upper body.

    def close(self):
        pass

    def control_capabilities(self):
        return {'backend': 'attended_mc_trial', 'hand_position': False,
                'upper_body_playback': False, 'teaching': False, 'reason': '现场单次验收'}


async def execute_trial(args, frames, profile, report):
    from ..bridge.controller import BridgeController

    class TraceIO(RosAnimationIO):
        def state(self):
            state = super().state()
            self.probe.sample()
            return state

        def close(self):
            try:
                (args.output / 'trace.json').write_text(json.dumps(self.probe.trace))
            finally:
                super().close()

    actor = MCPlayback(profile, TraceIO, interlock_path=args.interlock)
    controller = BridgeController(TrialRobot(), mc_playback=actor)
    controller.trajectories['trial'] = frames
    sequence = 0

    async def command(**payload):
        nonlocal sequence
        sequence += 1
        return await controller.handle('trial', {**payload, 'sequence': sequence})

    async def heartbeat():
        while True:
            await command(type='heartbeat')
            await asyncio.sleep(.05)

    async def wait_for(state):
        deadline = time.monotonic() + profile['max_clip_seconds'] + 2 * profile['transition_timeout_s'] + 5
        while actor.state != state:
            if actor.state in ('error', 'stop_failed') or time.monotonic() > deadline or (
                    actor.task is not None and actor.task.done()):
                raise RuntimeError(f'等待 {state} 失败: {actor.state}; {actor.error}')
            report['states'].append({'at': time.monotonic(), 'state': actor.state,
                                     'progress_ms_estimated': actor.progress_ms})
            await asyncio.sleep(.02)

    await controller.register('trial', 'attended-trial')
    await command(type='arm', enabled=True)
    lease = asyncio.create_task(heartbeat())
    try:
        await command(type='trajectory_play', name='trial', speed=.25)
        report['execution_requested'] = True
        await wait_for('playing')
        if args.mode != 'complete':
            await asyncio.sleep(args.after)
            if actor.state != 'playing':
                raise RuntimeError('轨迹已结束，未完成中途操作验收')
            if args.mode == 'pause-resume':
                await command(type='trajectory_play', command='pause')
                await wait_for('paused')
                await asyncio.sleep(.3)
                await command(type='trajectory_play', command='resume')
                await wait_for('completed')
            elif args.mode == 'stop':
                await command(type='trajectory_play', command='stop')
                await wait_for('idle')
            elif args.mode == 'disconnect':
                lease.cancel()
                await controller.disconnect('trial')
                await wait_for('idle')
            elif args.mode == 'timeout':
                lease.cancel()
                await asyncio.sleep(controller.timeout + .05)
                await controller.watchdog()
                await wait_for('idle')
        else:
            await wait_for('completed')
        report['result'] = actor.state
    finally:
        lease.cancel()
        await asyncio.gather(lease, return_exceptions=True)
        try:
            await controller.close()
        finally:
            report['final_state'] = actor.state
            report['error'] = actor.error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trajectory-file', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--mode', choices=('complete', 'stop', 'pause-resume', 'disconnect', 'timeout'), default='stop')
    parser.add_argument('--after', type=float, default=1, help='开始 playing 后等待秒数，范围 0.1–10')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--attended-estop', action='store_true')
    parser.add_argument('--unloaded', action='store_true')
    parser.add_argument('--interlock', type=Path, default=Path.home() / '.x2_ps5_teleop/mc-motion-unconfirmed.lock')
    args = parser.parse_args()
    if not .1 <= args.after <= 10:
        parser.error('--after 需在 0.1–10 秒内')
    if args.execute and not (args.profile and args.attended_estop and args.unloaded):
        parser.error('执行需已审阅的首轮验收配置、现场物理急停值守和空载确认')
    profile = load_profile(args.profile, commissioning_trial=True) if args.execute else None
    frames = json.loads(args.trajectory_file.read_text())[args.name]
    animation = compile_animation(frames, .25)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'preview.csv').write_bytes(animation.content)
    report = {'execution_requested': False, 'commissioned': False, 'mode': args.mode,
              'csv_sha256': animation.sha256, 'states': [],
              'review_required': '审阅 MC 状态、反馈、停止延迟和身体隔离；不自动改写验收勾选'}
    try:
        if args.execute:
            asyncio.run(execute_trial(args, frames, profile, report))
    except Exception as exc:
        report['failure'] = str(exc)
        raise
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
