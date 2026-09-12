"""AgiBot X2 + PS5 最小遥操作演示。

示例：
    uv run python demo/teleop_demo.py --robot mock
    uv run python demo/teleop_demo.py --robot x2
"""

from __future__ import annotations

import argparse
import sys
import time

from x2_ps5_teleop.controller.dualsense import DualSenseReader
from x2_ps5_teleop.controller.mapping import DEFAULT_MAPPING
from x2_ps5_teleop.core import JoySample
from x2_ps5_teleop.robot.motion import MockRobot, X2RosRobot
from x2_ps5_teleop.teleop.state_machine import TeleopOutput, TeleopState, TeleopStateMachine


def _dispatch(robot, teleop: TeleopStateMachine, output: TeleopOutput, previous_state: TeleopState | None) -> TeleopState:
    if output.state != previous_state:
        print(f"STATE={output.state.value}", flush=True)
    if output.state in (TeleopState.IDLE, TeleopState.ESTOP, TeleopState.DISCONNECTED, TeleopState.TIMEOUT):
        robot.stop()
    else:
        robot.move(output.linear_x, output.linear_y, output.angular_z)
    if output.mode is not None:
        robot.set_mode(output.mode)
    for action in output.hand_actions:
        robot.stop()
        robot.hand_action(action)
    return output.state


def _sample_from_line(line: str, now: float) -> JoySample | None:
    words = line.strip().split()
    if not words:
        return None
    command = words[0].lower()
    if command == "teleop":
        return JoySample(buttons=(0,) * DEFAULT_MAPPING.teleop_toggle + (1,), stamp=now)
    if command == "idle":
        return JoySample(stamp=now)
    if command == "estop":
        return JoySample(buttons=(0,) * DEFAULT_MAPPING.emergency_stop + (1,), stamp=now)
    if command == "disconnect":
        return JoySample(connected=False, stamp=now)
    if command == "clear":
        return JoySample(stamp=now)
    try:
        values = [float(value) for value in words]
    except ValueError as exc:
        raise ValueError("请输入六个轴值和整数按钮值，或输入 teleop/idle/estop/disconnect/clear") from exc
    axes = tuple(values[:6])
    buttons = tuple(int(value) for value in values[6:])
    return JoySample(axes=axes, buttons=buttons, stamp=now)


def run_mock() -> None:
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    robot = MockRobot(sys.stdout)
    previous_state = None
    print("STATE=IDLE")
    print("Mock 输入：六个轴值加按钮值；命令为 teleop、idle、estop、disconnect、clear、quit")
    try:
        for line in sys.stdin:
            if line.strip().lower() in {"quit", "exit"}:
                break
            try:
                sample = _sample_from_line(line, time.monotonic())
            except ValueError as exc:
                print(f"错误：{exc}", file=sys.stderr)
                continue
            if sample is None:
                continue
            if line.strip().lower() == "estop":
                output = teleop.update(sample)
            elif line.strip().lower() == "clear":
                teleop.clear_estop()
                output = teleop.tick()
            elif line.strip().lower() == "idle" and teleop.state == TeleopState.TELEOP:
                output = teleop.update(JoySample(buttons=(0,) * DEFAULT_MAPPING.teleop_toggle + (1,), stamp=time.monotonic()))
            elif line.strip().lower() == "teleop" and teleop.state == TeleopState.TELEOP:
                output = teleop.tick()
            else:
                output = teleop.update(sample)
            previous_state = _dispatch(robot, teleop, output, previous_state)
    except KeyboardInterrupt:
        pass
    finally:
        robot.close()


def run_x2() -> None:
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    robot = X2RosRobot()
    reader = DualSenseReader(DEFAULT_MAPPING)
    previous_state = None
    try:
        reader.start()
        while True:
            sample = reader.poll().joy_sample()
            previous_state = _dispatch(robot, teleop, teleop.update(sample), previous_state)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        robot.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgiBot X2 的最小 PS5 DualSense 遥操作演示")
    parser.add_argument("--robot", choices=("mock", "x2"), default="mock")
    args = parser.parse_args()
    if args.robot == "mock":
        run_mock()
    else:
        run_x2()


if __name__ == "__main__":
    main()
