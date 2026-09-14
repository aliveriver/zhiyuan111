from x2_ps5_teleop.core import HandAction, JoySample, Ps5Teleop, Mode, SafetyState


def sample(axes=(0, 0, 0, 0, 0, 0), buttons=(), stamp=10.0):
    return JoySample(tuple(axes), tuple(buttons), stamp)


def test_axis_mapping_and_deadzone():
    out = Ps5Teleop().update(sample(axes=(0.2, -1.0, 0.5, 0, 0, 0)), now=10.0)
    assert out.linear_x == 0.12
    assert out.linear_y > 0
    assert out.angular_z > 0


def test_mode_buttons_are_rising_edge_only():
    teleop = Ps5Teleop()
    assert teleop.update(sample(buttons=(0, 0, 1, 0)), now=10).mode == Mode.RL
    assert teleop.update(sample(buttons=(0, 0, 1, 0), stamp=10.1), now=10.1).mode is None
    assert teleop.update(sample(buttons=(), stamp=10.2), now=10.2).mode is None


def test_standard_dualsense_square_and_triangle_modes():
    teleop = Ps5Teleop()
    assert teleop.mapping.square == 2
    assert teleop.mapping.triangle == 3
    assert teleop.update(sample(buttons=(0, 0, 1, 0)), now=10).mode == Mode.RL
    teleop.update(sample(buttons=(), stamp=10.1), now=10.1)
    assert teleop.update(sample(buttons=(0, 0, 0, 1), stamp=10.2), now=10.2).mode == Mode.JOINT


def test_hand_actions_map_to_l1_r1_and_triggers():
    teleop = Ps5Teleop()
    out = teleop.update(sample(axes=(0, 0, 0, 0, 1, 1), buttons=(0, 0, 0, 0, 1, 1)), now=10)
    assert set(out.hand_actions) == {HandAction.L1, HandAction.R1, HandAction.LT, HandAction.RT}


def test_mode_and_hand_events_can_share_a_frame():
    out = Ps5Teleop().update(sample(axes=(0, 0, 0, 0, 0, 1), buttons=(0, 0, 1, 0, 0, 1)), now=10)
    assert out.mode == Mode.RL
    assert out.hand_actions == (HandAction.R1, HandAction.RT)


def test_stale_input_forces_damping_and_zero_velocity():
    out = Ps5Teleop(timeout=0.5).update(sample(axes=(0, -1, 0, 0, 0, 0), stamp=1), now=2)
    assert out.safety == SafetyState.STALE
    assert out.mode == Mode.DAMPING
    assert (out.linear_x, out.linear_y, out.angular_z) == (0, 0, 0)
