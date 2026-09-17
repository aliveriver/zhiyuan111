import copy
import csv
import io

import pytest

from x2_ps5_teleop.robot.mc_animation import ARM_NAMES, UPPER_NAMES, WAIST_NAMES, compile_animation, hold_animation


def frame(t_ms=0, delta=0.0):
    return {"type": "upper_body", "t_ms": t_ms,
            "arm": [{"name": name, "position": -0.2 + delta} for name in ARM_NAMES],
            "left_hand": [{"name": "", "position": -0.1} for _ in range(10)],
            "right_hand": [{"name": "", "position": 0.1} for _ in range(10)]}


def read_csv(animation):
    return list(csv.DictReader(io.StringIO(animation.content.decode())))


def test_resamples_20_hz_recording_to_firmware_ticks_and_applies_speed():
    animation = compile_animation([frame(0), frame(50, 0.02), frame(100, 0.04)], speed=0.25)
    rows = read_csv(animation)
    assert len(rows) == 201  # 400 ms / 2 ms plus final sample; not 3 rows.
    assert animation.duration_ms == 400
    assert float(rows[50]["command_pos::left_elbow_joint"]) == pytest.approx(-0.19)
    assert float(rows[-1]["command_pos::right_elbow_joint"]) == pytest.approx(-0.16)
    assert [int(row["timeMS"]) for row in rows] == list(range(0, 401, 2))


def test_export_whitelists_upper_body_and_preserves_hand_feedback_signs():
    a, b = frame(), frame(50)
    a["waist"] = [{"name": "waist_yaw_joint", "position": 1.0}]
    a["arm"][0]["stiffness"] = 1000
    b["arm"].reverse()
    rows = read_csv(compile_animation([a, b]))
    assert set(rows[0]) == {"timeMS", *(f"command_pos::{name}" for name in UPPER_NAMES)}
    assert float(rows[0]["command_pos::left_thumb_roll_joint"]) == -0.1
    assert float(rows[0]["command_pos::right_thumb_roll_joint"]) == 0.1


def test_hold_is_constant_measured_pose_without_changing_recording():
    measured = frame(100, 0.12)
    before = copy.deepcopy(measured)
    rows = read_csv(hold_animation(measured))
    assert len(rows) == 501
    assert {row["command_pos::left_elbow_joint"] for row in rows} == {"-0.08"}
    assert measured == before


def test_waist_hold_is_opt_in_and_requires_all_three_channels():
    a, b = frame(), frame(100)
    a["waist"] = [{"name": name, "position": i * .01} for i, name in enumerate(WAIST_NAMES)]
    b["waist"] = list(reversed(a["waist"]))
    animation = compile_animation([a, b], include_waist=True)
    rows = read_csv(animation)
    assert {key for key in rows[0] if key.startswith("command_pos::waist")} == {
        f"command_pos::{name}" for name in WAIST_NAMES}
    with pytest.raises(ValueError):
        compile_animation([a, {**b, "waist": a["waist"][:2]}], include_waist=True)


@pytest.mark.parametrize("fault", ["name", "duplicate", "nan", "time", "empty_hand"])
def test_rejects_ambiguous_or_invalid_motion(fault):
    a, b = frame(), frame(50)
    if fault == "name":
        b["arm"][0]["name"] = "waist_yaw_joint"
    elif fault == "duplicate":
        b["t_ms"] = 0
        b["arm"][0]["position"] = 1
    elif fault == "nan":
        b["arm"][0]["position"] = float("nan")
    elif fault == "time":
        b["t_ms"] = -1
    else:
        b["left_hand"] = []
    with pytest.raises(ValueError):
        compile_animation([a, b])


def test_rejects_unbounded_resampling_before_allocation():
    with pytest.raises(ValueError, match="五分钟"):
        compile_animation([frame(), frame(300001)], speed=0.01)


def test_probe_changes_only_requested_joint_and_preserves_source():
    from x2_ps5_teleop.robot.mc_animation_probe import single_joint_frames
    measured = frame()
    before = copy.deepcopy(measured)
    samples = single_joint_frames(measured, "left_wrist_yaw_joint", 0.02)
    assert len(samples) == 61
    assert samples[-1]["t_ms"] == 3000
    assert measured == before
    for sample in samples:
        for joint in sample["arm"]:
            if joint["name"] != "left_wrist_yaw_joint":
                assert joint["position"] == -0.2
        assert sample["left_hand"] == measured["left_hand"]
        assert sample["right_hand"] == measured["right_hand"]
    assert samples[-1]["arm"][4]["position"] == pytest.approx(-0.18)
    with pytest.raises(ValueError):
        single_joint_frames(measured, "left_wrist_yaw_joint", 0.2)
    extended = single_joint_frames(measured, "left_wrist_yaw_joint", 0.02, 10000)
    assert len(extended) == 201 and extended[-1]["t_ms"] == 10000


def test_probe_observation_requires_playing_then_idle():
    from x2_ps5_teleop.robot.mc_animation_probe import observe_hold, observe_primary

    class TraceProbe:
        def __init__(self, states):
            self.states = iter(states)
            self.monotonic = 0.0
        def wait(self, _seconds):
            pass
        def sample(self):
            self.monotonic += 0.05
            return {"monotonic": self.monotonic,
                    "mc": {"player_state_name": next(self.states)}}

    assert observe_primary(TraceProbe(["IDLE", "PRE_PLAYING", "PLAYING", "IDLE"]),
                           3000, "complete", 2) == "complete"
    result = observe_hold(TraceProbe(
        ["PRE_PLAYING", "PLAYING", "IDLE", "IDLE", "IDLE", "IDLE", "IDLE", "IDLE", "IDLE", "IDLE"]))
    assert result["hold_stable_observed_s"] >= 0.3


def test_probe_default_does_not_upload_or_execute(tmp_path, monkeypatch):
    import json
    import sys
    from x2_ps5_teleop.robot import mc_animation_probe as probe
    class ReadOnly:
        trace = []
        def inspect(self):
            return {"action": "PASSIVE_DEFAULT"}
        def frame(self):
            return frame()
        def close(self):
            pass
    def forbidden(*args, **kwargs):
        pytest.fail("默认诊断不得上传文件或调用控制接口")
    ReadOnly.play = forbidden
    monkeypatch.setattr(probe, "Probe", ReadOnly)
    monkeypatch.setattr(probe, "upload", forbidden)
    output = tmp_path / "probe"
    monkeypatch.setattr(sys, "argv", ["probe", "--output", str(output)])
    probe.main()
    report = json.loads((output / "report.json").read_text())
    assert report["executed"] is False
    assert report["commissioned"] is False
    assert (output / "probe.csv").exists()


def test_probe_execute_requires_attended_unloaded_conditions(tmp_path, monkeypatch):
    import sys
    from x2_ps5_teleop.robot import mc_animation_probe as probe
    output = tmp_path / "probe"
    monkeypatch.setattr(sys, "argv", ["probe", "--output", str(output), "--execute"])
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2
    assert not output.exists()
