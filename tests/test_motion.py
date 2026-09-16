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
    for call in (lambda: robot.arm_target([]),
                 lambda: robot.upper_body_target({"arm": []})):
        with pytest.raises(RuntimeError, match="尚未确认"):
            call()


@pytest.fixture
def hand_robot():
    robot = X2RosRobot.__new__(X2RosRobot)
    messages = []
    robot.hand_pub = SimpleNamespace(publish=messages.append)
    robot._now = lambda: SimpleNamespace(to_msg=lambda: "test_stamp")
    array = lambda: SimpleNamespace(left_hand_type=SimpleNamespace(), right_hand_type=SimpleNamespace())
    robot._msg = (None,) * 4 + (SimpleNamespace, None, array, SimpleNamespace)
    return robot, messages


@pytest.mark.parametrize("side", ["left", "right"])
def test_restored_hand_parameters_preserve_legacy_mapping(hand_robot, side):
    robot, messages = hand_robot
    assert robot.control_capabilities()["hand_position"] is True
    robot.hand_target(side, [(i, 0.2, 0.3, 0.4, 0.5, 0.6) for i in range(10)])
    assert len(messages) == 1
    message = messages[0]
    assert message.left_hand_type.value == message.right_hand_type.value == 1
    assert getattr(message, "right_hands" if side == "left" else "left_hands") == []
    hands = getattr(message, f"{side}_hands")
    assert len(hands) == 10
    for i, hand in enumerate(hands):
        assert hand.position == (-0.2 if side == "left" and i < 3 else 0.2)
        assert (hand.velocity, hand.acceleration, hand.deceleration, hand.effort) == (0.3, 0.4, 0.5, 0.6)


@pytest.mark.parametrize("side", ["left", "right"])
def test_raw_positions_preserve_feedback_sign_and_slots(hand_robot, side):
    robot, messages = hand_robot
    positions = [-0.3, 0.2, -0.1, 1.0000317, 0.4, 0.3, 0.2, 0.1, 0.0, 0.5]
    robot.hand_positions(side, positions)
    assert len(messages) == 1
    assert [hand.position for hand in getattr(messages[0], f"{side}_hands")] == positions
    assert getattr(messages[0], "right_hands" if side == "left" else "left_hands") == []


@pytest.mark.parametrize("positions", [[0.0] * 9, [float("nan")] * 10, [float("inf")] * 10, [4.0] * 10])
def test_raw_positions_reject_invalid_before_publish(hand_robot, positions):
    robot, messages = hand_robot
    with pytest.raises(ValueError):
        robot.hand_positions("left", positions)
    assert not messages


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
