import asyncio
import io
import json

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


class UpperBodyRobot(RecordingRobot):
    def __init__(self):
        super().__init__()
        self.sample_count = 0
        self.prepare_error = None

    def upper_body_state(self):
        self.sample_count += 1
        return {
            "arm": [{"name": f"arm_{index}", "position": self.sample_count / 100} for index in range(14)],
            "left_hand": [{"name": f"left_{index}", "position": 0.1} for index in range(10)],
            "right_hand": [{"name": f"right_{index}", "position": 0.2} for index in range(10)],
        }

    def prepare_upper_body_teaching(self):
        if self.prepare_error:
            raise RuntimeError(self.prepare_error)
        self.calls.append(("prepare_teaching",))

    def upper_body_teaching_step(self):
        self.calls.append(("teach",))

    def end_upper_body_teaching(self):
        self.calls.append(("end_teaching",))

    def prepare_upper_body_playback(self, frame):
        if self.prepare_error:
            raise RuntimeError(self.prepare_error)
        self.calls.append(("prepare_playback", len(frame["arm"])))

    def upper_body_target(self, frame):
        self.calls.append(("upper_body", frame["t_ms"]))


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


def test_voice_presets_are_dynamic_and_do_not_stop_motion(tmp_path):
    stream = io.StringIO()
    robot = MockRobot(stream)
    voice_config = tmp_path / "voice_presets.json"
    voice_config.write_text(json.dumps({"presets": [
        {"id": "one", "label": "第一段", "mode": "file",
         "file_path": "/var/tmp/voice", "file_name": "one.wav"},
        {"id": "two", "label": "第二段", "mode": "file",
         "file_path": "/var/tmp/voice", "file_name": "two.wav"},
    ]}), encoding="utf-8")
    controller = BridgeController(robot, voice_preset_path=voice_config)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        assert [item["id"] for item in controller.voice_preset_list()] == ["one", "two"]
        state = await controller.handle("s1", {"type": "voice_play", "preset": "one", "sequence": 2}, now=1.1)
        assert state["voice_state"] == "sent"
        await controller.handle("s1", {
            "type": "velocity", "forward": 0.12, "lateral": 0.0, "angular": 0.0, "sequence": 3,
        }, now=1.2)
        assert "VOICE FILE path=/var/tmp/voice/one.wav" in stream.getvalue()
        assert "MOVE vx=0.120 vy=0.000 wz=0.000" in stream.getvalue()

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


def test_preset_keeps_teleop_armed_for_next_action():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        state = await controller.handle("s1", {"type": "preset", "action": "right_wave", "sequence": 2}, now=1.1)
        assert state["state"] == "TELEOP"
        assert state["armed"]
        assert robot.calls[-2:] == [("stop",), ("hand", HandAction.RT)]

    run(scenario())


def test_mobile_preset_aliases_are_accepted():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        state = await controller.handle("s1", {"type": "preset", "action": "握紧", "sequence": 2}, now=1.1)
        assert state["state"] == "TELEOP"
        assert state["armed"]
        assert robot.calls[-1] == ("hand", HandAction.R1)

    run(scenario())


def test_hand_target_is_validated_and_keeps_teleop_armed():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        joints = [{"index": index, "position": 0.1, "velocity": 0.2, "acceleration": 0.3, "deceleration": 0.4, "effort": 0.0} for index in range(10)]
        state = await controller.handle("s1", {"type": "hand_target", "side": "left", "joints": joints, "sequence": 2}, now=1.1)
        assert state["state"] == "TELEOP"
        assert state["armed"]
        assert robot.calls[-2][0] == "stop"
        assert robot.calls[-1][0:2] == ("hand_target", "left")

    run(scenario())


def test_trajectory_record_list_rename_delete_and_playback():
    robot = RecordingRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1}, now=1)
        await controller.handle("s1", {"type": "trajectory_record_start", "name": "抓取", "sequence": 2}, now=1)
        await controller.handle("s1", {"type": "preset", "action": "open", "sequence": 3}, now=1.1)
        await controller.handle("s1", {"type": "trajectory_record_stop", "sequence": 4}, now=1.2)
        listing = await controller.handle("s1", {"type": "trajectory_list", "sequence": 5}, now=1.2)
        assert listing["trajectories"] == [{
            "name": "抓取",
            "frames": 1,
            "duration_ms": 100,
            "arm_joints": 0,
            "left_hand_joints": 0,
            "right_hand_joints": 0,
        }]
        await controller.handle("s1", {"type": "trajectory_rename", "old_name": "抓取", "new_name": "抓取2", "sequence": 6}, now=1.2)
        await controller.handle("s1", {"type": "trajectory_play", "name": "抓取2", "sequence": 7}, now=1.2)
        assert robot.calls[-1] == ("hand", HandAction.RT)
        await controller.handle("s1", {"type": "trajectory_delete", "name": "抓取2", "sequence": 8}, now=1.2)
        assert (await controller.handle("s1", {"type": "trajectory_list", "sequence": 9}, now=1.2))["trajectories"] == []

    run(scenario())


def test_upper_body_recording_respects_rate_and_keeps_multiple_names():
    robot = UpperBodyRobot()
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1})
        await controller.handle("s1", {
            "type": "trajectory_record_start", "name": "动作一", "sample_rate_hz": 20, "sequence": 2,
        })
        await asyncio.sleep(0.17)
        await controller.handle("s1", {"type": "trajectory_record_stop", "sequence": 3})
        first_frames = controller.trajectories["动作一"]
        assert 3 <= len(first_frames) <= 5
        deltas = [b["t_ms"] - a["t_ms"] for a, b in zip(first_frames, first_frames[1:])]
        assert all(35 <= delta <= 80 for delta in deltas)

        with pytest.raises(BridgeError, match="已存在"):
            await controller.handle("s1", {
                "type": "trajectory_record_start", "name": "动作一", "sample_rate_hz": 20, "sequence": 4,
            })
        await controller.handle("s1", {
            "type": "trajectory_record_start", "name": "动作二", "sample_rate_hz": 10, "sequence": 5,
        })
        await controller.handle("s1", {"type": "trajectory_record_stop", "sequence": 6})
        listing = await controller.handle("s1", {"type": "trajectory_list", "sequence": 7})
        assert [item["name"] for item in listing["trajectories"]] == ["动作一", "动作二"]
        assert all(item["arm_joints"] == 14 for item in listing["trajectories"])

    run(scenario())


def test_upper_body_playback_reports_pause_progress_and_completion():
    robot = UpperBodyRobot()
    controller = BridgeController(robot, timeout=1.0)
    frame = robot.upper_body_state()
    controller.trajectories["递出"] = [
        {"type": "upper_body", "t_ms": 0, **frame},
        {"type": "upper_body", "t_ms": 80, **frame},
    ]

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1})
        state = await controller.handle("s1", {"type": "trajectory_play", "name": "递出", "sequence": 2})
        assert state["playback_state"] == "playing"
        assert not await controller.watchdog()
        with pytest.raises(BridgeError, match="正在播放"):
            await controller.handle("s1", {"type": "trajectory_play", "name": "递出", "sequence": 3})
        paused = await controller.handle("s1", {"type": "trajectory_play", "command": "pause", "sequence": 4})
        assert paused["playback_state"] == "paused"
        await asyncio.sleep(0.03)
        resumed = await controller.handle("s1", {"type": "trajectory_play", "command": "resume", "sequence": 5})
        assert resumed["playback_state"] == "playing"
        await asyncio.wait_for(controller.playback_task, timeout=0.5)
        snapshot = await controller.snapshot()
        assert snapshot.playback_state == "completed"
        assert snapshot.playback_progress_ms == 80
        assert ("upper_body", 80) in robot.calls

    run(scenario())


def test_upper_body_operation_explains_wrong_system_state():
    robot = UpperBodyRobot()
    robot.prepare_error = "当前系统状态为 Business；上肢示教录制需要 Develop_MC"
    controller = BridgeController(robot)

    async def scenario():
        await controller.register("s1", "phone")
        await controller.handle("s1", {"type": "arm", "enabled": True, "sequence": 1})
        with pytest.raises(BridgeError, match="Business") as error:
            await controller.handle("s1", {
                "type": "trajectory_record_start", "name": "测试", "sample_rate_hz": 20, "teach_mode": True, "sequence": 2,
            })
        assert error.value.code == "upper_body_unavailable"
        assert controller.recording_name is None

    run(scenario())


def test_recording_defaults_to_read_only_and_replays_repeatedly(tmp_path):
    robot = UpperBodyRobot()
    robot.prepare_error = "Develop_MC unavailable"
    path = tmp_path / "trajectories.json"
    path.write_text('{"旧轨迹": []}', encoding="utf-8")
    controller = BridgeController(robot, trajectory_path=path)

    async def scenario():
        await controller.register("s", "phone")
        await controller.handle("s", {"type": "arm", "enabled": True, "sequence": 1})
        await controller.handle("s", {"type": "trajectory_record_start", "name": "新轨迹", "sequence": 2})
        await controller.handle("s", {"type": "trajectory_record_stop", "sequence": 3})
        assert not any(call[0] in ("teach", "prepare_teaching", "end_teaching", "upper_body") for call in robot.calls)
        loaded = BridgeController(robot, trajectory_path=path)
        assert "旧轨迹" in loaded.trajectories
        assert len(loaded.trajectories["新轨迹"][0]["arm"]) == 14
        robot.prepare_error = None
        for seq in (4, 5):
            await controller.handle("s", {"type": "trajectory_play", "name": "新轨迹", "sequence": seq})
            await controller.playback_task
            assert (await controller.snapshot()).playback_state == "completed"
        assert sum(call[0] == "upper_body" for call in robot.calls) == 2
        await controller.close()
    run(scenario())


@pytest.mark.parametrize("activity", ["record", "play"])
def test_watchdog_cancels_activities_without_opening_hands(activity):
    robot = UpperBodyRobot()
    controller = BridgeController(robot)
    controller.trajectories["test"] = [{"type": "upper_body", "t_ms": 10000, **robot.upper_body_state()}]

    async def scenario():
        await controller.register("s", "phone")
        await controller.handle("s", {"type": "arm", "enabled": True, "sequence": 1}, now=10)
        message = {"type": "trajectory_record_start" if activity == "record" else "trajectory_play",
                   "name": "test2" if activity == "record" else "test", "sequence": 2}
        await controller.handle("s", message, now=10)
        assert await controller.watchdog(now=11)
        await asyncio.sleep(0)
        assert not controller.armed
        assert controller.recording_name is None
        assert controller.playback_task is None
        assert robot.calls[-1] == ("stop",)
        assert not any(call[0] in ("hand", "hand_target", "upper_body") for call in robot.calls)
    run(scenario())


def test_hand_release_requires_explicit_confirmation_and_preserves_other_hand(tmp_path):
    robot = MockRobot(io.StringIO())
    controller = BridgeController(robot, hand_pose_path=tmp_path / "hand_poses.json")

    async def scenario():
        await controller.register("s", "phone")
        await controller.handle("s", {"type": "hand_pose_save", "name": "松手", "side": "right",
                                      "positions": [0.1] * 10, "requires_confirmation": True, "sequence": 1})
        await controller.handle("s", {"type": "arm", "enabled": True, "sequence": 2})
        with pytest.raises(BridgeError) as error:
            await controller.handle("s", {"type": "hand_pose_apply", "name": "松手", "sequence": 3})
        assert error.value.code == "confirmation_required"
        assert robot._positions["right"] == [0] * 10
        await controller.handle("s", {"type": "hand_pose_apply", "name": "松手", "confirmed": True, "sequence": 4})
        assert robot._positions["right"] == [0.1] * 10
        assert robot._positions["left"] == [0] * 10
        await controller.disconnect("s")
        assert robot._positions["right"] == [0.1] * 10
    run(scenario())


def test_blocked_backend_rejects_new_and_legacy_hand_paths_before_commands():
    class Blocked(UpperBodyRobot):
        def control_capabilities(self):
            return {"backend": "x2", "hand_position": False, "upper_body_playback": False,
                    "teaching": False, "reason": "MC ownership not confirmed"}
    robot = Blocked()
    controller = BridgeController(robot)
    controller.trajectories["test"] = [{"type": "upper_body", "t_ms": 0, **robot.upper_body_state()}]

    async def scenario():
        await controller.register("s", "phone")
        await controller.handle("s", {"type": "arm", "enabled": True, "sequence": 1})
        for seq, message in enumerate([
            {"type": "preset", "action": "grip"},
            {"type": "hand_target", "side": "left", "joints": []},
            {"type": "hand_params", "hand": "left", "parameters": []},
            {"type": "hand_positions", "side": "left", "positions": [0.1] * 10},
            {"type": "trajectory_play", "name": "test"},
        ], 2):
            with pytest.raises(BridgeError, match="ownership"):
                await controller.handle("s", {**message, "sequence": seq})
        assert robot.calls == []
    run(scenario())


def test_recording_rejects_partial_feedback_without_sticking_in_recording():
    robot = UpperBodyRobot()
    state = robot.upper_body_state()
    state["right_hand"] = []
    robot.upper_body_state = lambda: state
    controller = BridgeController(robot)
    async def scenario():
        await controller.register("s", "phone")
        await controller.handle("s", {"type": "arm", "enabled": True, "sequence": 1})
        with pytest.raises(BridgeError) as error:
            await controller.handle("s", {"type": "trajectory_record_start", "name": "test", "sequence": 2})
        assert error.value.code == "state_unavailable"
        assert controller.recording_name is None
        assert not controller.trajectories
    run(scenario())
