"""与硬件无关的遥操作核心的 ROS 2 适配器。

模式和手部话题有意设计为简单的 String 事件。机器人侧的 AimDK 桥接层应将它们
转换为已安装的消息/服务类型。
"""

from __future__ import annotations

from .controller.mapping import DEFAULT_MAPPING
from .core import JoySample
from .teleop.state_machine import TeleopState, TeleopStateMachine


def main() -> None:
    try:
        import rclpy
        from geometry_msgs.msg import Twist
        from rclpy.node import Node
        from sensor_msgs.msg import Joy
        from std_msgs.msg import String
    except ImportError as exc:  # pragma: no cover - 仅在 ROS 主机上执行
        raise RuntimeError("x2-teleop-ros 需要 ROS 2 Humble 软件包") from exc

    class NodeImpl(Node):
        def __init__(self) -> None:
            super().__init__("x2_ps5_teleop")
            self.declare_parameter("joy_topic", "/joy")
            self.declare_parameter("cmd_vel_topic", "/cmd_vel")
            self.declare_parameter("mode_topic", "/x2_ps5_teleop/mode")
            self.declare_parameter("hand_action_topic", "/x2_ps5_teleop/hand_action")
            self.core = TeleopStateMachine(DEFAULT_MAPPING)
            self.cmd_pub = self.create_publisher(Twist, self.get_parameter("cmd_vel_topic").value, 10)
            self.mode_pub = self.create_publisher(String, self.get_parameter("mode_topic").value, 10)
            self.hand_pub = self.create_publisher(String, self.get_parameter("hand_action_topic").value, 10)
            self.create_subscription(Joy, self.get_parameter("joy_topic").value, self.on_joy, 10)
            self.create_timer(0.1, self.on_tick)

        def publish_output(self, output) -> None:
            twist = Twist()
            twist.linear.x, twist.linear.y, twist.angular.z = output.linear_x, output.linear_y, output.angular_z
            self.cmd_pub.publish(twist)
            if output.mode is not None:
                self.mode_pub.publish(String(data=output.mode.value))
            for action in output.hand_actions:
                self.hand_pub.publish(String(data=action.value))

        def on_joy(self, msg: Joy) -> None:
            self.publish_output(self.core.update(JoySample(tuple(msg.axes), tuple(msg.buttons))))

        def on_tick(self) -> None:
            self.publish_output(self.core.tick())

    rclpy.init()
    node = NodeImpl()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
