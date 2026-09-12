from x2_ps5_teleop.controller.dualsense import DualSenseReader


def test_matches_common_dualsense_sdl_names():
    reader = DualSenseReader()

    assert reader._matches_name("Wireless Controller")
    assert reader._matches_name("Sony DualSense Wireless Controller")
    assert reader._matches_name("PS5 Controller")


def test_matches_custom_name_hint_case_insensitively():
    reader = DualSenseReader(name_hint="X2 Pad")

    assert reader._matches_name("x2 pad usb")
    assert not reader._matches_name("Xbox Controller")
