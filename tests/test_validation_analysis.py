import json

import pytest

from scripts.analyze_validation_results import analyze_validation, compare_validations


def sample(t, state, *, feedback=0.0, target=0.0, waist=0.0, head=0.0, leg_interval=0.01):
    return {
        "monotonic": t,
        "mc": {"player_state_name": state},
        "arm": {"joint": feedback},
        "arm_command": {"joint": target},
        "leg_command": {"leg": 0.2},
        "leg_command_age": 0.004,
        "leg_command_max_interval": leg_interval,
        "waist_command_age": 0.004,
        "head_command_age": 0.004,
        "waist_command": {
            "waist_pitch_joint": waist,
            "waist_roll_joint": 0.0,
            "waist_yaw_joint": 0.0,
        },
        "head_command": {"head": head},
    }


def write_evidence(path, *, stable=True):
    path.mkdir()
    report = {"executed": True, "scheme": "extended_duration", "stop_mode": "midway"}
    if stable:
        report.update(hold_idle_observed_at=1.0, hold_stable_observed_until=1.35)
    trace = [
        sample(0.0, "IDLE"),
        sample(0.1, "PRE_PLAYING", waist=0.01, head=0.005),
        sample(0.2, "PLAYING", feedback=0.9, target=1.0, waist=0.03, head=0.01),
        sample(0.3, "PLAYING", feedback=1.05, target=1.0, waist=0.02, head=0.008,
               leg_interval=0.02),
        sample(0.4, "INTERRUPTING", feedback=1.0, target=1.0, waist=0.01),
        sample(1.0, "IDLE", feedback=1.0, target=1.0, waist=0.0),
        sample(1.1, "IDLE", feedback=1.001, target=1.0, waist=0.0),
        sample(1.2, "IDLE", feedback=1.0, target=1.0, waist=0.0),
        sample(1.35, "IDLE", feedback=1.0, target=1.0, waist=0.0),
    ]
    (path / "report.json").write_text(json.dumps(report))
    (path / "trace.json").write_text(json.dumps(trace))


def test_validation_analysis_covers_commissioning_metrics(tmp_path):
    evidence = tmp_path / "evidence"
    write_evidence(evidence)

    result = analyze_validation(evidence)

    assert result["arm_tracking_playing"]["max_abs_error_rad"] == pytest.approx(0.1)
    assert result["stop_stability"]["stable_300ms_observed"] is True
    assert result["stop_stability"]["arm_feedback"]["max_joint_range_rad"] == pytest.approx(0.001)
    assert result["leg_command_continuity"]["max_message_interval_s"] == 0.02
    assert result["head_command_motion"]["max_head_abs_offset_rad"] == 0.01
    assert result["waist_command_motion"]["max_waist_abs_offset_rad"] == 0.03


def test_missing_stop_window_is_evidence_failure_and_comparison_is_non_commissioning(tmp_path):
    old = tmp_path / "old"
    current = tmp_path / "current"
    write_evidence(old, stable=False)
    write_evidence(current)

    assert analyze_validation(old)["stop_stability"]["stable_300ms_observed"] is False
    result = compare_validations(old, current)
    assert "does not commission" in result["comparison"]["note"]
