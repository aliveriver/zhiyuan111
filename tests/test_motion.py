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
