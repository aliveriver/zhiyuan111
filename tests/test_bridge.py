import asyncio

import pytest

from x2_ps5_teleop.bridge.controller import BridgeController, BridgeError
from x2_ps5_teleop.core import HandAction, Mode
from x2_ps5_teleop.robot.motion import MockRobot


class RecordingRobot:
    def __init__(self):
        self.calls = []

    def move(self, x, y, z):
        self.calls.append(("move", x, y, z))

    def stop(self):
        self.calls.append(("stop",))

    def set_mode(self, mode):
        self.calls.append(("mode", mode))

    def hand_action(self, action):
        self.calls.append(("hand", action))

    def hand_target(self, side, joints):
        self.calls.append(("hand_target", side, joints))

    def close(self):
        self.calls.append(("close",))


def run(coro):
    return asyncio.run(coro)


def test_only_one_client_owns_bridge_and_same_client_replaces_old_session():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        assert await controller.register("s1", "phone") is None
        with pytest.raises(BridgeError, match="另一台设备"):
            await controller.register("s2", "other-phone")
        assert await controller.register("s2", "phone") == "s1"
        with pytest.raises(BridgeError, match="失去控制权"):
            await controller.handle("s1", {"type": "heartbeat", "sequence": 1})

    run(scenario())


def test_sequence_limits_and_velocity_are_safe():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=10)
        state = await controller.handle(
            "s1", {"type": "velocity", "forward": 100, "lateral": -100, "angular": 100, "sequence": 2}, now=10.1
        )
        assert state["state"] == "TELEOP"
        assert robot.calls[-1] == ("move", 0.12, -0.08, 0.15)
        with pytest.raises(BridgeError, match="严格递增"):
            await controller.handle("s1", {"type": "heartbeat", "sequence": 2}, now=10.2)

    run(scenario())


def test_watchdog_stops_and_estop_requires_clear_then_rearm():
    robot = RecordingRobot()
    controller = BridgeController(robot, timeout=0.4)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=10)
        assert await controller.watchdog(now=10.41)
        snapshot = await controller.snapshot(now=10.41)
        assert snapshot.state.value == "TIMEOUT"
        assert not snapshot.armed
        await controller.handle("s1", {"type": "estop", "sequence": 2}, now=10.42)
        with pytest.raises(BridgeError, match="锁存"):
            await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 3}, now=10.43)
        await controller.handle("s1", {"type": "clear_estop", "sequence": 4}, now=10.44)
        state = await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 5}, now=10.45)
        assert state["state"] == "TELEOP"

    run(scenario())


def test_preset_stops_and_returns_to_idle():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        state = await controller.handle("s1", {"type": "preset", "action": "right_wave", "sequence": 2}, now=1.1)
        assert state["state"] == "IDLE"
        assert not state["armed"]
        assert robot.calls[-2:] == [("stop",), ("hand", HandAction.RT)]

    run(scenario())


def test_mobile_preset_aliases_are_accepted():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        state = await controller.handle("s1", {"type": "preset", "action": "握紧", "sequence": 2}, now=1.1)
        assert state["state"] == "IDLE"
        assert robot.calls[-1] == ("hand", HandAction.R1)

    run(scenario())


def test_hand_target_is_validated_and_returns_to_idle():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        joints = [{"index": index, "position": 0.1, "velocity": 0.2, "acceleration": 0.3, "deceleration": 0.4, "effort": 0.0} for index in range(10)]
        state = await controller.handle("s1", {"type": "hand_target", "side": "left", "joints": joints, "sequence": 2}, now=1.1)
        assert state["state"] == "IDLE"
        assert not state["armed"]
        assert robot.calls[-2][0] == "stop"
        assert robot.calls[-1][0:2] == ("hand_target", "left")

    run(scenario())
