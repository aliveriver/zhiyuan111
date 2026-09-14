"""Mock 和官方 AimDK ROS 2 机器人后端。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TextIO

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
HAND_PRESETS = {
    HandAction.RT: ("right", 0.0),
    HandAction.LT: ("left", 0.0),
    HandAction.R1: ("right", 0.8),
    HandAction.L1: ("left", 0.8),
}


class RobotInterface:
    def move(self, linear_x: float, linear_y: float, angular_z: float) -> None: ...
    def stop(self) -> None: ...
    def set_mode(self, mode: Mode) -> None: ...
    def hand_action(self, action: HandAction) -> None: ...
    def close(self) -> None: ...


@dataclass
class MockRobot(RobotInterface):
    stream: TextIO

    def move(self, linear_x: float, linear_y: float, angular_z: float) -> None:
        print(f"MOVE vx={linear_x:.3f} vy={linear_y:.3f} wz={angular_z:.3f}", file=self.stream, flush=True)

    def stop(self) -> None:
        print("MOVE vx=0.000 vy=0.000 wz=0.000", file=self.stream, flush=True)

    def set_mode(self, mode: Mode) -> None:
        print(f"MODE={mode.value}", file=self.stream, flush=True)

    def hand_action(self, action: HandAction) -> None:
        print(f"HAND ACTION={action.value}", file=self.stream, flush=True)

    def close(self) -> None:
        self.stop()


class X2RosRobot(RobotInterface):
    """发布官方文档中确认的 AimDK 消息/服务类型。"""

    def __init__(
        self,
        node_name: str = "x2_ps5_teleop",
        source: str = "ps5_demo",
        preset_actions=None,
    ):
        try:
            import rclpy
            from aimdk_msgs.msg import (
                McLocomotionVelocity,
                McActionCommand,
                McControlArea,
                McPresetMotion,
                HandCommandArray,
                HandCommand,
                MessageHeader,
                RequestHeader,
            )
            from aimdk_msgs.srv import SetMcAction, SetMcInputSource, GetHandType
            from aimdk_msgs.msg import McInputAction, McInputSource
        except ImportError as exc:  # pragma: no cover - 仅在 ROS 主机上执行
            raise RuntimeError("X2 模式需要开发计算机上的 ROS 2 Humble 和 aimdk_msgs") from exc
        self._rclpy = rclpy
        self._msg = (McLocomotionVelocity, McActionCommand, McControlArea, McPresetMotion,
                     MessageHeader, RequestHeader, HandCommandArray, HandCommand)
        self._srv = (SetMcAction, SetMcInputSource, GetHandType)
        self._input_types = (McInputAction, McInputSource)
        if not rclpy.ok():
            rclpy.init()
        from rclpy.node import Node
        self.node = Node(node_name)
        self.source = source
        self.preset_actions = preset_actions or PRESET_ACTIONS
        self.velocity_pub = self.node.create_publisher(McLocomotionVelocity, "/aima/mc/locomotion/velocity", 10)
        self.hand_pub = self.node.create_publisher(HandCommandArray, "/aima/hal/joint/hand/command", 10)
        self.mode_client = self.node.create_client(SetMcAction, "/aimdk_5Fmsgs/srv/SetMcAction")
        self.source_client = self.node.create_client(SetMcInputSource, "/aimdk_5Fmsgs/srv/SetMcInputSource")
        self.hand_type_client = self.node.create_client(GetHandType, "/aimdk_5Fmsgs/srv/GetHandType")
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
        left = response.left_hands_type.value
        right = response.right_hands_type.value
        if left != 1 or right != 1:
            raise RuntimeError(f"需要左右 NIMBLE_HANDS(value=1)，实际为 left={left}, right={right}")

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
        side, position = HAND_PRESETS[action]
        msg = self._msg[6]()
        msg.header = self._msg[4]()
        msg.header.stamp = self._now().to_msg()
        msg.header.frame_id = "hand_command"
        msg.left_hand_type.value = 1
        msg.right_hand_type.value = 1
        msg.left_hands = [self._hand_command("left", i, position if side == "left" else 0.0)
                          for i in range(HAND_SLOT_COUNT)]
        msg.right_hands = [self._hand_command("right", i, position if side == "right" else 0.0)
                           for i in range(HAND_SLOT_COUNT)]
        self.hand_pub.publish(msg)

    def _hand_command(self, side: str, index: int, position: float):
        cmd = self._msg[7]()
        if index == 0:
            cmd.name = f"{side}_thumb"
        elif side == "left":
            cmd.name = "left_index"
        else:
            cmd.name = "right_pinky"
        cmd.position = float(-position if side == "left" and index < 3 else position)
        cmd.velocity = 0.1
        cmd.acceleration = 0.0
        cmd.deceleration = 0.0
        cmd.effort = 0.0
        return cmd

    def _now(self):
        return self.node.get_clock().now()

    def close(self) -> None:
        if self._rclpy.ok():
            self.stop()
        self.node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()
