from x2_ps5_teleop.core import HandAction, JoySample, Mode
from x2_ps5_teleop.controller.mapping import DEFAULT_MAPPING
from x2_ps5_teleop.teleop.state_machine import TeleopState, TeleopStateMachine


def frame(*, axes=(), buttons=(), stamp=10.0, connected=True):
    return JoySample(tuple(axes), tuple(buttons), stamp, connected)


def press(index: int, size: int = 13):
    values = [0] * size
    values[index] = 1
    return tuple(values)


def test_starts_idle_and_options_toggles_teleop():
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    assert teleop.state == TeleopState.IDLE
    assert teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10).state == TeleopState.TELEOP
    assert teleop.update(frame(buttons=()), now=10).state == TeleopState.TELEOP
    assert teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10).state == TeleopState.IDLE


def test_idle_does_not_emit_motion_or_hand_actions():
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    output = teleop.update(frame(axes=(0.5, -1, 0, 0, 0, 1), buttons=press(DEFAULT_MAPPING.r1)), now=10)
    assert output.state == TeleopState.IDLE
    assert (output.linear_x, output.linear_y, output.angular_z) == (0, 0, 0)
    assert output.hand_actions == ()


def test_teleop_maps_axes_modes_and_presets():
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10)
    output = teleop.update(frame(axes=(0.5, -1, 0.5, 0, 0, 1), buttons=press(DEFAULT_MAPPING.r1)), now=10)
    assert output.state == TeleopState.TELEOP
    assert output.linear_x == DEFAULT_MAPPING.max_linear_x
    assert output.linear_y > 0
    assert output.angular_z > 0
    assert output.hand_actions == (HandAction.R1,)
    mode = teleop.update(frame(buttons=press(DEFAULT_MAPPING.square)), now=10).mode
    assert mode == Mode.RL


def test_disconnect_timeout_and_estop_zero_output():
    teleop = TeleopStateMachine(DEFAULT_MAPPING, timeout=0.5)
    teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10)
    disconnected = teleop.update(frame(connected=False), now=10)
    assert disconnected.state == TeleopState.DISCONNECTED
    assert disconnected.linear_x == 0
    teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10)
    stale = teleop.update(frame(axes=(0, -1), stamp=1), now=2)
    assert stale.state == TeleopState.TIMEOUT
    teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10)
    estop = teleop.update(frame(buttons=press(DEFAULT_MAPPING.emergency_stop)), now=10)
    assert estop.state == TeleopState.ESTOP
    assert (estop.linear_x, estop.linear_y, estop.angular_z) == (0, 0, 0)
    teleop.clear_estop()
    assert teleop.state == TeleopState.IDLE


def test_only_one_preset_is_emitted_per_input_frame():
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10)

    output = teleop.update(
        frame(axes=(0, 0, 0, 0, 1, 1), buttons=(0, 0, 0, 0, 1, 1)),
        now=10,
    )

    assert output.hand_actions == (HandAction.L1,)


def test_enter_idle_requires_explicit_rearm():
    teleop = TeleopStateMachine(DEFAULT_MAPPING)
    teleop.update(frame(buttons=press(DEFAULT_MAPPING.teleop_toggle)), now=10)
    teleop.enter_idle()

    stopped = teleop.update(frame(axes=(0, -1), stamp=10.1), now=10.1)

    assert stopped.state == TeleopState.IDLE
    assert stopped.linear_x == 0
