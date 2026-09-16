"""Mock 和官方 AimDK ROS 2 机器人后端。"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
import time
import sys
from typing import Any, TextIO

from ..core import HandAction, Mode


PRESET_ACTIONS = {
    HandAction.RT: (1002, 2, "right_wave"),
    HandAction.LT: (1002, 1, "left_wave"),
    HandAction.R1: (1001, 2, "right_raise"),
    HandAction.L1: (1001, 1, "left_raise"),
}

# Official OmniHand examples use ten command slots per hand.  The first three
# slots are thumb motors; left-thumb positions are mirrored by the firmware.
HAND_SLOT_COUNT = 10
ARM_POSITION_STIFFNESS = 20.0
ARM_POSITION_DAMPING = 2.0
ARM_TEACH_DAMPING = 5.0
HAND_PRESETS = {
    # The four mobile presets are intentionally explicit so they remain easy
    # to calibrate when the installed hand firmware exposes different limits.
    HandAction.R1: ("both", (0.8,) * HAND_SLOT_COUNT),       # 握紧
    HandAction.RT: ("both", (0.0,) * HAND_SLOT_COUNT),       # 张开
    HandAction.L1: ("both", (0.0, 0.0, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8)),  # 比个耶
    HandAction.LT: ("both", (0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8)),  # 点个赞
}


class RobotInterface:
    def move(self, linear_x: float, linear_y: float, angular_z: float) -> None: ...
    def stop(self) -> None: ...
    def set_mode(self, mode: Mode) -> None: ...
    def hand_action(self, action: HandAction) -> None: ...
    def hand_target(self, side: str, joints: list[tuple[int, float, float, float, float, float]]) -> None: ...
    def arm_target(self, joints: list[dict[str, Any]]) -> None: ...
    def upper_body_target(self, frame: dict[str, Any]) -> None: ...
    def upper_body_state(self) -> dict[str, Any] | None: ...
    def prepare_upper_body_teaching(self) -> None: ...
    def upper_body_teaching_step(self) -> None: ...
    def end_upper_body_teaching(self) -> None: ...
    def prepare_upper_body_playback(self, frame: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


@dataclass
class MockRobot(RobotInterface):
    stream: TextIO
    _positions: dict[str, list[float]] = field(default_factory=lambda: {
        "left": [0.0] * HAND_SLOT_COUNT, "right": [0.0] * HAND_SLOT_COUNT,
    })
    _arm: list[dict[str, Any]] = field(default_factory=lambda: [
        {"name": f"{side}_{joint}_joint", "position": 0.0, "velocity": 0.0, "effort": 0.0}
        for side in ("left", "right")
        for joint in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
                      "wrist_yaw", "wrist_pitch", "wrist_roll")
    ])

    def control_capabilities(self) -> dict[str, Any]:
        return {"backend": "mock", "hand_position": True, "upper_body_playback": True,
                "teaching": False, "reason": "模拟模式：反馈和执行均为模拟，不代表真机验收"}

    def hand_positions(self, side: str, positions: list[float]) -> None:
        self._positions[side] = list(positions)
        print(f"HAND POSITION side={side} joints={len(positions)}", file=self.stream, flush=True)

    def move(self, linear_x: float, linear_y: float, angular_z: float) -> None:
        print(f"MOVE vx={linear_x:.3f} vy={linear_y:.3f} wz={angular_z:.3f}", file=self.stream, flush=True)

    def stop(self) -> None:
        print("MOVE vx=0.000 vy=0.000 wz=0.000", file=self.stream, flush=True)

    def set_mode(self, mode: Mode) -> None:
        print(f"MODE={mode.value}", file=self.stream, flush=True)

    def hand_action(self, action: HandAction) -> None:
        print(f"HAND ACTION={action.value}", file=self.stream, flush=True)

    def hand_target(self, side: str, joints: list[tuple[int, float, float, float, float, float]]) -> None:
        self._positions[side] = [joint[1] for joint in joints]
        print(f"HAND TARGET side={side} joints={len(joints)}", file=self.stream, flush=True)

    def arm_target(self, joints: list[dict[str, Any]]) -> None:
        self._arm = copy.deepcopy(joints)
        print(f"ARM TARGET joints={len(joints)}", file=self.stream, flush=True)

    def upper_body_target(self, frame: dict[str, Any]) -> None:
        self.arm_target(frame.get("arm", []))
        for side in ("left", "right"):
            if frame.get(f"{side}_hand"):
                self.hand_positions(side, [j["position"] for j in frame[f"{side}_hand"]])

    def upper_body_state(self) -> dict[str, Any] | None:
        return {"arm": copy.deepcopy(self._arm), **{
            f"{side}_hand": [{"name": f"{side}_{i}", "position": pos,
                             "velocity": 0.0, "effort": 0.0}
                            for i, pos in enumerate(self._positions[side])]
            for side in ("left", "right")
        }}

    def prepare_upper_body_teaching(self) -> None:
        print("ARM TEACHING prepare", file=self.stream, flush=True)

    def upper_body_teaching_step(self) -> None:
        print("ARM TEACHING damping", file=self.stream, flush=True)

    def end_upper_body_teaching(self) -> None:
        print("ARM TEACHING end", file=self.stream, flush=True)

    def prepare_upper_body_playback(self, frame: dict[str, Any]) -> None:
        print(f"ARM PLAYBACK prepare joints={len(frame.get('arm', []))}", file=self.stream, flush=True)

    def close(self) -> None:
        self.stop()


class X2RosRobot(RobotInterface):
    """发布官方文档中确认的 AimDK 消息/服务类型。"""

    def control_capabilities(self) -> dict[str, Any]:
        # Restore the hand path previously exercised on this robot. Arm
        # ownership and interruptible animation remain separate capabilities.
        return {"backend": "x2", "hand_position": True, "upper_body_playback": False,
                "teaching": False,
                "reason": "已恢复灵巧手参数与位置控制；机械臂控制权及动画停止链路尚未确认，真机上肢回放和卸力示教未开放"}

    def _require_control(self, capability: str) -> None:
        capabilities = self.control_capabilities()
        if not capabilities[capability]:
            raise RuntimeError(capabilities["reason"])

    def hand_positions(self, side: str, positions: list[float]) -> None:
        self._require_control("hand_position")
        if side not in ("left", "right"):
            raise ValueError("side 必须是 left 或 right")
        if len(positions) != HAND_SLOT_COUNT or any(
            not math.isfinite(p) or abs(p) > math.pi for p in positions
        ):
            raise ValueError("需要 10 个 −π～π rad 的有限位置值")
        # Raw feedback already contains the left thumb sign. Cancel the
        # legacy editor transform before using the shared message builder.
        self.hand_target(side, [
            (i, -p if side == "left" and i < 3 else p, 0.1, 0.0, 0.0, 0.0)
            for i, p in enumerate(positions)
        ])

    def __init__(
        self,
        node_name: str = "x2_ps5_teleop",
        source: str = "ps5_demo",
        preset_actions=None,
    ):
        try:
            common_aimdk = "/agibot/software/common/local/lib/python3.10/dist-packages"
            ec_aimdk = "/agibot/software/ec/local/lib/python3.10/dist-packages"
            sys.path[:] = [path for path in sys.path if "/agibot/software/ec/" not in path]
            if common_aimdk not in sys.path:
                sys.path.insert(0, common_aimdk)
            import rclpy
            from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
            from aimdk_msgs.msg import (
                McLocomotionVelocity,
                McActionCommand,
                McControlArea,
                McPresetMotion,
                HandCommandArray,
                HandCommand,
                MessageHeader,
                RequestHeader,
                JointCommandArray,
                JointCommand,
                JointStateArray,
                HandStateArray,
            )
            from aimdk_msgs.srv import SetMcAction, SetMcInputSource, GetHandType, GetSystemState
            from aimdk_msgs.msg import McInputAction, McInputSource
        except ImportError as exc:  # pragma: no cover - 仅在 ROS 主机上执行
            raise RuntimeError("X2 模式需要开发计算机上的 ROS 2 Humble 和 aimdk_msgs") from exc
        self._rclpy = rclpy
        self._state_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE)
        self._msg = (McLocomotionVelocity, McActionCommand, McControlArea, McPresetMotion,
                     MessageHeader, RequestHeader, HandCommandArray, HandCommand,
                     JointCommandArray, JointCommand, JointStateArray, HandStateArray)
        self._srv = (SetMcAction, SetMcInputSource, GetHandType, GetSystemState)
        self._input_types = (McInputAction, McInputSource)
        if not rclpy.ok():
            rclpy.init()
        from rclpy.node import Node
        self.node = Node(node_name)
        self.source = source
        self.preset_actions = preset_actions or PRESET_ACTIONS
        self.velocity_pub = self.node.create_publisher(McLocomotionVelocity, "/aima/mc/locomotion/velocity", 10)
        self.hand_pub = self.node.create_publisher(HandCommandArray, "/aima/hal/joint/hand/command", 10)
        self.arm_pub = self.node.create_publisher(JointCommandArray, "/aima/hal/joint/arm/command", 10)
        self._arm_state: Any = None
        self._hand_state: Any = None
        self._arm_received_at = 0.0
        self._hand_received_at = 0.0
        self.node.create_subscription(JointStateArray, "/aima/hal/joint/arm/state", self._on_arm_state, self._state_qos)
        self.node.create_subscription(HandStateArray, "/aima/hal/joint/hand/state", self._on_hand_state, self._state_qos)
        self.mode_client = self.node.create_client(SetMcAction, "/aimdk_5Fmsgs/srv/SetMcAction")
        self.source_client = self.node.create_client(SetMcInputSource, "/aimdk_5Fmsgs/srv/SetMcInputSource")
        self.hand_type_client = self.node.create_client(GetHandType, "/aimdk_5Fmsgs/srv/GetHandType")
        self.system_state_client = self.node.create_client(GetSystemState, "/aimdk_5Fmsgs/srv/GetSystemState")
        try:
            self._require_services()
            self._register_source()
            self._verify_hand_type()
        except Exception:
            self.node.destroy_node()
            if self._rclpy.ok():
                self._rclpy.shutdown()
            raise

    def _require_services(self) -> None:
        required = (
            (self.source_client, "/aimdk_5Fmsgs/srv/SetMcInputSource"),
            (self.mode_client, "/aimdk_5Fmsgs/srv/SetMcAction"),
            (self.hand_type_client, "/aimdk_5Fmsgs/srv/GetHandType"),
            (self.system_state_client, "/aimdk_5Fmsgs/srv/GetSystemState"),
        )
        unavailable = [name for client, name in required if not client.wait_for_service(timeout_sec=2.0)]
        if unavailable:
            raise RuntimeError(f"AimDK 服务不可用: {', '.join(unavailable)}")

    def _wait_for_result(self, future, operation: str, timeout: float = 2.0):
        self._rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout)
        if not future.done():
            raise RuntimeError(f"{operation}超时")
        if future.exception() is not None:
            raise RuntimeError(f"{operation}失败") from future.exception()
        result = future.result()
        if result is None:
            raise RuntimeError(f"{operation}未返回结果")
        return result

    def _register_source(self) -> None:
        _action_type, _source_type = self._input_types
        req = self._srv[1].Request()
        req.request.header = self._msg[5]()
        req.action.value = 1001
        req.input_source.name = self.source
        req.input_source.priority = 40
        req.input_source.timeout = 1000
        future = self.source_client.call_async(req)
        response = self._wait_for_result(future, "注册 AimDK 输入源")
        task = response.response
        if task.header.code != 1:
            raise RuntimeError(f"注册 AimDK 输入源被拒绝: code={task.header.code}, state={task.state.value}")

    def _verify_hand_type(self) -> None:
        future = self.hand_type_client.call_async(self._srv[2].Request())
        response = self._wait_for_result(future, "查询灵巧手类型")
        # AimDK images have shipped both singular and plural response field
        # names. Read the installed interface explicitly so startup validation
        # works across those versions instead of failing on the first command.
        left = self._hand_type_value(response, "left")
        right = self._hand_type_value(response, "right")
        if left != 1 or right != 1:
            raise RuntimeError(f"需要左右 NIMBLE_HANDS(value=1)，实际为 left={left}, right={right}")

    @staticmethod
    def _hand_type_value(response, side: str) -> int:
        for field in (f"{side}_hand", f"{side}_hands_type"):
            value = getattr(response, field, None)
            if value is not None:
                return int(value.value)
        raise RuntimeError("GetHandType 响应缺少左右手类型字段")

    def move(self, linear_x: float, linear_y: float, angular_z: float) -> None:
        if not self._rclpy.ok():
            return
        msg = self._msg[0]()
        msg.header = self._msg[4]()
        msg.header.stamp = self._now().to_msg()
        msg.source = self.source
        msg.forward_velocity = float(linear_x)
        msg.lateral_velocity = float(linear_y)
        msg.angular_velocity = float(angular_z)
        self.velocity_pub.publish(msg)

    def stop(self) -> None:
        self.move(0.0, 0.0, 0.0)

    def set_mode(self, mode: Mode) -> None:
        if not self.mode_client.wait_for_service(timeout_sec=1.0):
            self.node.get_logger().warning("SetMcAction 服务不可用")
            return
        req = self._srv[0].Request()
        req.header = self._msg[5]()
        req.header.stamp = self._now().to_msg()
        req.source = self.source
        req.command.action_desc = mode.value
        future = self.mode_client.call_async(req)
        self._wait_for_result(future, "切换运动模式", timeout=1.0)

    def hand_action(self, action: HandAction) -> None:
        self._require_control("hand_position")
        side, positions = HAND_PRESETS[action]
        msg = self._msg[6]()
        msg.header = self._msg[4]()
        msg.header.stamp = self._now().to_msg()
        msg.header.frame_id = "hand_command"
        msg.left_hand_type.value = 1
        msg.right_hand_type.value = 1
        msg.left_hands = [self._hand_command("left", i, positions[i]) for i in range(HAND_SLOT_COUNT)]
        msg.right_hands = [self._hand_command("right", i, positions[i]) for i in range(HAND_SLOT_COUNT)]
        self.hand_pub.publish(msg)

    def hand_target(self, side: str, joints: list[tuple[int, float, float, float, float, float]]) -> None:
        self._require_control("hand_position")
        if side not in ("left", "right"):
            raise ValueError("side 必须是 left 或 right")
        msg = self._msg[6]()
        msg.header = self._msg[4]()
        msg.header.stamp = self._now().to_msg()
        msg.header.frame_id = "hand_command"
        msg.left_hand_type.value = 1
        msg.right_hand_type.value = 1
        values = {index: (position, velocity, acceleration, deceleration, effort) for index, position, velocity, acceleration, deceleration, effort in joints}
        hands = [self._hand_command(side, index, *values[index]) for index in range(HAND_SLOT_COUNT)]
        if side == "left":
            msg.left_hands, msg.right_hands = hands, []
        else:
            msg.right_hands, msg.left_hands = hands, []
        self.hand_pub.publish(msg)

    def _on_arm_state(self, msg) -> None:
        self._arm_state = msg
        self._arm_received_at = time.monotonic()

    def _on_hand_state(self, msg) -> None:
        self._hand_state = msg
        self._hand_received_at = time.monotonic()

    def upper_body_state(self) -> dict[str, Any] | None:
        if not self._rclpy.ok():
            return None
        # Drain a bounded number of callbacks so both subscriptions can update.
        for _ in range(4):
            self._rclpy.spin_once(self.node, timeout_sec=0.0)
        now = time.monotonic()
        if (self._arm_state is None or self._hand_state is None
                or now - self._arm_received_at > 0.5 or now - self._hand_received_at > 0.5):
            return None
        def joints(values):
            return [{"name": str(item.name), "position": float(item.position), "velocity": float(getattr(item, "velocity", 0.0)),
                     "effort": float(getattr(item, "effort", 0.0))} for item in (values or [])]
        return {
            "arm": joints(getattr(self._arm_state, "joints", [])),
            "left_hand": joints(getattr(self._hand_state, "left_hands", [])),
            "right_hand": joints(getattr(self._hand_state, "right_hands", [])),
        }

    def arm_target(self, joints: list[dict[str, Any]]) -> None:
        self._require_control("upper_body_playback")
        msg = self._msg[8]()
        msg.header = self._msg[4]()
        msg.header.stamp = self._now().to_msg()
        msg.header.frame_id = "arm_command"
        msg.joints = []
        for item in joints:
            cmd = self._msg[9]()
            cmd.name = str(item["name"])
            cmd.position = float(item.get("position", 0.0))
            cmd.velocity = float(item.get("velocity", 0.0))
            cmd.effort = float(item.get("effort", 0.0))
            cmd.stiffness = float(item.get("stiffness", ARM_POSITION_STIFFNESS))
            cmd.damping = float(item.get("damping", ARM_POSITION_DAMPING))
            msg.joints.append(cmd)
        self.arm_pub.publish(msg)

    def upper_body_target(self, frame: dict[str, Any]) -> None:
        # Both publishers use the same ROS clock tick.  The bridge never
        # interleaves frames, so a pair of arm/hand messages is one logical
        # synchronized upper-body frame without touching leg or waist topics.
        self.arm_target(frame.get("arm", []))
        left = frame.get("left_hand", [])
        right = frame.get("right_hand", [])
        if left or right:
            msg = self._msg[6]()
            msg.header = self._msg[4]()
            msg.header.stamp = self._now().to_msg()
            msg.header.frame_id = "hand_command"
            msg.left_hand_type.value = 1
            msg.right_hand_type.value = 1
            msg.left_hands = [self._hand_command("left", i, float(item.get("position", 0.0)), float(item.get("velocity", 0.1)), 0.0, 0.0, float(item.get("effort", 0.0))) for i, item in enumerate(left)]
            msg.right_hands = [self._hand_command("right", i, float(item.get("position", 0.0)), float(item.get("velocity", 0.1)), 0.0, 0.0, float(item.get("effort", 0.0))) for i, item in enumerate(right)]
            self.hand_pub.publish(msg)

    def upper_body_teaching_step(self) -> None:
        """Apply damping to the 14 arm joints only; leg/waist topics are untouched."""
        state = self.upper_body_state()
        if not state or not state.get("arm"):
            return
        joints = [{
            "name": item["name"],
            "position": 0.0,
            "velocity": 0.0,
            "effort": 0.0,
            "stiffness": 0.0,
            "damping": ARM_TEACH_DAMPING,
        } for item in state["arm"]]
        self.arm_target(joints)

    def prepare_upper_body_teaching(self) -> None:
        self._require_develop_mc("上肢示教录制")
        self._require_control("teaching")
        state = self.upper_body_state()
        arm_count = len(state.get("arm", [])) if state else 0
        if arm_count != 14:
            raise RuntimeError(f"机械臂状态应包含 14 个关节，实际为 {arm_count}")
        self.upper_body_teaching_step()

    def end_upper_body_teaching(self) -> None:
        # Hold the measured pose when leaving teaching mode, avoiding a jump to
        # a stale target. Hands aren't changed because OmniHand exposes no
        # documented torque-disable field on this firmware.
        state = self.upper_body_state()
        if state and state.get("arm"):
            self.arm_target(state["arm"])

    def prepare_upper_body_playback(self, frame: dict[str, Any]) -> None:
        self._require_develop_mc("上肢轨迹播放")
        self._require_control("upper_body_playback")
        arm = frame.get("arm", [])
        if len(arm) != 14:
            raise RuntimeError(f"轨迹机械臂关节数应为 14，实际为 {len(arm)}")

    def _require_develop_mc(self, operation: str) -> None:
        if not self.system_state_client.wait_for_service(timeout_sec=1.0):
            raise RuntimeError("GetSystemState 服务不可用，无法安全确认系统状态")
        future = self.system_state_client.call_async(self._srv[3].Request())
        response = self._wait_for_result(future, "查询系统状态", timeout=2.0)
        current = str(response.cur_state)
        if current != "Develop_MC":
            raise RuntimeError(
                f"当前系统状态为 {current}；{operation}已被 HAL 控制保护拦截。"
                "现有实现要求 Develop_MC，但已调查的 X2 v0.9.7 未配置该状态；"
                "且官方 Develop_MC 会停用全身原生运控，不能保证腿部站立。"
                "请先确认本固件支持的 MC 上肢控制链路，不要强制切换状态或绕过检查。"
                "详见 docs/X2_V0_9_7_CONTROL_INVESTIGATION.md"
            )

    def _hand_command(self, side: str, index: int, position: float, velocity: float = 0.1, acceleration: float = 0.0, deceleration: float = 0.0, effort: float = 0.0):
        cmd = self._msg[7]()
        if index == 0:
            cmd.name = f"{side}_thumb"
        elif side == "left":
            cmd.name = "left_index"
        else:
            cmd.name = "right_pinky"
        cmd.position = float(-position if side == "left" and index < 3 else position)
        cmd.velocity = float(velocity)
        cmd.acceleration = float(acceleration)
        cmd.deceleration = float(deceleration)
        cmd.effort = float(effort)
        return cmd

    def _now(self):
        return self.node.get_clock().now()

    def close(self) -> None:
        if self._rclpy.ok():
            self.stop()
        self.node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()
