from types import SimpleNamespace

import pytest

from x2_ps5_teleop.robot.motion import X2RosRobot


def test_hand_type_accepts_installed_plural_fields():
    response = SimpleNamespace(
        left_hands_type=SimpleNamespace(value=1),
        right_hands_type=SimpleNamespace(value=1),
    )
    assert X2RosRobot._hand_type_value(response, "left") == 1
    assert X2RosRobot._hand_type_value(response, "right") == 1


def test_hand_type_accepts_singular_fields():
    response = SimpleNamespace(
        left_hand=SimpleNamespace(value=1),
        right_hand=SimpleNamespace(value=1),
    )
    assert X2RosRobot._hand_type_value(response, "left") == 1
    assert X2RosRobot._hand_type_value(response, "right") == 1


def test_hand_type_rejects_unknown_interface():
    with pytest.raises(RuntimeError, match="缺少左右手类型字段"):
        X2RosRobot._hand_type_value(SimpleNamespace(), "left")


def test_real_backend_never_publishes_unverified_upper_body_commands():
    # No ROS node or publishers are constructed. Each call must fail before
    # touching a publisher, even when a client bypasses the bridge UI.
    robot = X2RosRobot.__new__(X2RosRobot)
    for call in (lambda: robot.hand_positions("left", [0.0] * 10),
                 lambda: robot.hand_target("left", []),
                 lambda: robot.arm_target([]),
                 lambda: robot.upper_body_target({"arm": []}),
                 lambda: robot.hand_action(None)):
        with pytest.raises(RuntimeError, match="尚未确认"):
            call()


def test_real_feedback_requires_fresh_arm_and_hand_messages():
    import time
    robot = X2RosRobot.__new__(X2RosRobot)
    robot._rclpy = SimpleNamespace(ok=lambda: True, spin_once=lambda *a, **kw: None)
    robot.node = object()
    robot._arm_state = SimpleNamespace(joints=[])
    robot._hand_state = SimpleNamespace(left_hands=[], right_hands=[])
    robot._arm_received_at = time.monotonic()
    robot._hand_received_at = time.monotonic() - 1
    assert robot.upper_body_state() is None
