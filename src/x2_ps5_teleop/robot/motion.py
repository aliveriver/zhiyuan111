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

    def __init__(self, node_name: str = "x2_ps5_teleop", source: str = "ps5_demo"):
        try:
            import rclpy
            from aimdk_msgs.msg import McLocomotionVelocity, McActionCommand, McControlArea, McPresetMotion
            from aimdk_msgs.srv import SetMcAction, SetMcInputSource, SetMcPresetMotion
            from aimdk_msgs.msg import McInputAction, McInputSource
        except ImportError as exc:  # pragma: no cover - 仅在 ROS 主机上执行
            raise RuntimeError("X2 模式需要开发计算机上的 ROS 2 Humble 和 aimdk_msgs") from exc
        self._rclpy = rclpy
        self._msg = (McLocomotionVelocity, McActionCommand, McControlArea, McPresetMotion)
        self._srv = (SetMcAction, SetMcInputSource, SetMcPresetMotion)
        self._input_types = (McInputAction, McInputSource)
        if not rclpy.ok():
            rclpy.init()
        from rclpy.node import Node
        self.node = Node(node_name)
        self.source = source
        self.velocity_pub = self.node.create_publisher(McLocomotionVelocity, "/aima/mc/locomotion/velocity", 10)
        self.mode_client = self.node.create_client(SetMcAction, "/aimdk_5Fmsgs/srv/SetMcAction")
        self.source_client = self.node.create_client(SetMcInputSource, "/aimdk_5Fmsgs/srv/SetMcInputSource")
        self.preset_client = self.node.create_client(SetMcPresetMotion, "/aimdk_5Fmsgs/srv/SetMcPresetMotion")
        self._register_source()

    def _register_source(self) -> None:
        _action_type, _source_type = self._input_types
        req = self._srv[1].Request()
        # 文档中 McInputAction 的取值为 ADD=1001、MODIFY=1002……
        req.action.value = 1001
        req.input_source.name = self.source
        req.input_source.priority = 30
        req.input_source.timeout = 500
        if self.source_client.wait_for_service(timeout_sec=2.0):
            future = self.source_client.call_async(req)
            self._rclpy.spin_until_future_complete(self.node, future, timeout_sec=2.0)

    def move(self, linear_x: float, linear_y: float, angular_z: float) -> None:
        if not self._rclpy.ok():
            return
        msg = self._msg[0]()
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
        req.source = self.source
        req.command.action_desc = mode.value
        future = self.mode_client.call_async(req)
        self._rclpy.spin_until_future_complete(self.node, future, timeout_sec=1.0)

    def hand_action(self, action: HandAction) -> None:
        if not self.preset_client.wait_for_service(timeout_sec=1.0):
            self.node.get_logger().warning("SetMcPresetMotion 服务不可用")
            return
        motion, area, _ = PRESET_ACTIONS[action]
        req = self._srv[2].Request()
        req.area.value = area
        req.motion.value = motion
        req.interrupt = True
        req.ani_path = ""
        # 较旧的 AimDK 构建版本提供 play_timestamp；当前 X2 消息没有此字段。
        if hasattr(req, "play_timestamp"):
            req.play_timestamp = 0
        future = self.preset_client.call_async(req)
        self._rclpy.spin_until_future_complete(self.node, future, timeout_sec=1.0)

    def close(self) -> None:
        if self._rclpy.ok():
            self.stop()
        self.node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()
