from x2_ps5_teleop.inspect_node import _interface_types, _relevant_ros_lines


def test_extracts_relevant_ros_interface_types():
    output = """/rosout [rcl_interfaces/msg/Log]
/aima/hal/joint/hand/command [aimdk_msgs/msg/HandCommandArray]
/aimdk/srv/SetMcAction [aimdk_msgs/srv/SetMcAction]
"""

    relevant = _relevant_ros_lines(output)

    assert len(relevant) == 2
    assert _interface_types(relevant) == {
        "aimdk_msgs/msg/HandCommandArray",
        "aimdk_msgs/srv/SetMcAction",
    }
