#!/usr/bin/env python3
"""Build reviewable metrics from two attended MC validation traces."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


PLAYER_STATES = {0: "IDLE", 1: "PRE_PLAYING", 2: "PLAYING", 3: "INTERRUPTING", 4: "ERROR"}
WAIST_JOINTS = ("waist_pitch_joint", "waist_roll_joint", "waist_yaw_joint")


def load_test_data(test_dir: Path) -> dict[str, Any]:
    """Load report and trace without treating incomplete execution as success."""
    report_path = test_dir / "report.json"
    trace_path = test_dir / "trace.json"
    if not report_path.exists() or not trace_path.exists():
        raise FileNotFoundError(f"Missing report.json or trace.json in {test_dir}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not isinstance(trace, list):
        raise ValueError(f"Invalid validation data in {test_dir}")
    return {"report": report, "trace": trace}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def player_state(sample: dict[str, Any]) -> str | None:
    mc = sample.get("mc")
    if not isinstance(mc, dict):
        return None
    name = mc.get("player_state_name")
    return name if isinstance(name, str) else PLAYER_STATES.get(mc.get("player_state"))


def observed_sequence(trace: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for sample in trace:
        state = player_state(sample)
        if state and (not result or result[-1] != state):
            result.append(state)
    return result


def _joint_maps(trace: list[dict[str, Any]], key: str) -> list[dict[str, float]]:
    result = []
    for sample in trace:
        values = sample.get(key)
        if isinstance(values, dict):
            clean = {name: value for name, raw in values.items()
                     if isinstance(name, str) and (value := _number(raw)) is not None}
            if clean:
                result.append(clean)
    return result


def _range_stats(maps: list[dict[str, float]]) -> dict[str, Any]:
    names = sorted({name for values in maps for name in values})
    joints = {}
    for name in names:
        values = [sample[name] for sample in maps if name in sample]
        joints[name] = {"min": min(values), "max": max(values),
                        "range_rad": max(values) - min(values), "samples": len(values)}
    return {"samples": len(maps), "joints": joints,
            "max_joint_range_rad": max((item["range_rad"] for item in joints.values()), default=None)}


def _fresh_baseline(trace: list[dict[str, Any]], key: str, first_active: int) -> tuple[dict | None, float | None]:
    for index in range(first_active - 1, -1, -1):
        values = trace[index].get(key)
        age = _number(trace[index].get(key + "_age"))
        if isinstance(values, dict) and values and age is not None and 0 <= age <= 0.25:
            return values, age
    return None, None


def analyze_waist_motion(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Report ranges by phase and maximum displacement from pre-play baseline."""
    phases = {}
    for phase in ("PRE_PLAYING", "PLAYING", "IDLE"):
        phases[phase] = _range_stats([
            sample["waist_command"] for sample in trace
            if player_state(sample) == phase and isinstance(sample.get("waist_command"), dict)
            and sample["waist_command"]
        ])

    first_active = next((index for index, sample in enumerate(trace)
                         if player_state(sample) in ("PRE_PLAYING", "PLAYING", "INTERRUPTING")), None)
    baseline = None
    baseline_age = None
    if first_active is not None:
        baseline, baseline_age = _fresh_baseline(trace, "waist_command", first_active)
    offsets = {}
    if baseline:
        activity = trace[first_active:]
        for joint in WAIST_JOINTS:
            origin = _number(baseline.get(joint))
            values = [_number(sample.get("waist_command", {}).get(joint)) for sample in activity]
            values = [value for value in values if value is not None]
            if origin is not None and values:
                offsets[joint] = max(abs(value - origin) for value in values)
    return {"phases": phases, "baseline": baseline, "baseline_age_s": baseline_age,
            "baseline_fresh": baseline is not None,
            "max_abs_offset_from_initial_rad": offsets,
            "max_waist_abs_offset_rad": max(offsets.values(), default=None)}


def analyze_arm_tracking(trace: list[dict[str, Any]]) -> dict[str, Any]:
    per_joint: dict[str, list[float]] = {}
    sample_count = 0
    for sample in trace:
        if player_state(sample) != "PLAYING":
            continue
        target, feedback = sample.get("arm_command"), sample.get("arm")
        if not isinstance(target, dict) or not isinstance(feedback, dict):
            continue
        errors = []
        for joint in target.keys() & feedback.keys():
            target_value, feedback_value = _number(target[joint]), _number(feedback[joint])
            if target_value is not None and feedback_value is not None:
                error = abs(target_value - feedback_value)
                per_joint.setdefault(joint, []).append(error)
                errors.append(error)
        if errors:
            sample_count += 1
    flattened = [value for values in per_joint.values() for value in values]
    return {"samples": sample_count,
            "max_abs_error_rad": max(flattened, default=None),
            "mean_abs_error_rad": sum(flattened) / len(flattened) if flattened else None,
            "per_joint_max_abs_error_rad": {name: max(values) for name, values in sorted(per_joint.items())}}


def analyze_command_continuity(trace: list[dict[str, Any]], key: str) -> dict[str, Any]:
    active = [sample for sample in trace
              if player_state(sample) in ("PRE_PLAYING", "PLAYING", "INTERRUPTING")]
    intervals = [_number(sample.get(key + "_max_interval")) for sample in active]
    ages = [_number(sample.get(key + "_age")) for sample in active]
    intervals = [value for value in intervals if value is not None and value > 0]
    ages = [value for value in ages if value is not None and value >= 0]
    payload_samples = sum(bool(sample.get(key)) for sample in active)
    return {"activity_samples": len(active), "payload_samples": payload_samples,
            "max_message_interval_s": max(intervals, default=None),
            "max_observed_age_s": max(ages, default=None),
            "interval_instrumented": any(key + "_max_interval" in sample for sample in active)}


def analyze_head_motion(trace: list[dict[str, Any]]) -> dict[str, Any]:
    first_active = next((index for index, sample in enumerate(trace)
                         if player_state(sample) in ("PRE_PLAYING", "PLAYING", "INTERRUPTING")), None)
    baseline = None
    baseline_age = None
    if first_active is not None:
        baseline, baseline_age = _fresh_baseline(trace, "head_command", first_active)
    activity_maps = _joint_maps(trace[first_active:] if first_active is not None else [], "head_command")
    ranges = _range_stats(activity_maps)
    offsets = {}
    if isinstance(baseline, dict):
        for name, raw in baseline.items():
            origin = _number(raw)
            values = [sample[name] for sample in activity_maps if name in sample]
            if origin is not None and values:
                offsets[name] = max(abs(value - origin) for value in values)
    return {**ranges, "baseline": baseline, "baseline_age_s": baseline_age,
            "baseline_fresh": baseline is not None, "max_abs_offset_from_initial_rad": offsets,
            "max_head_abs_offset_rad": max(offsets.values(), default=None)}


def analyze_stop_stability(trace: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    start = _number(report.get("hold_idle_observed_at"))
    end = _number(report.get("hold_stable_observed_until"))
    if start is None or end is None or end < start:
        return {"evidence_available": False, "stable_300ms_observed": False,
                "reason": "report lacks hold IDLE stability timestamps"}
    samples = [sample for sample in trace
               if (timestamp := _number(sample.get("monotonic"))) is not None and start <= timestamp <= end]
    states = [player_state(sample) for sample in samples]
    return {"evidence_available": bool(samples), "window_s": end - start,
            "samples": len(samples), "states": observed_sequence(samples),
            "all_idle": bool(states) and all(state == "IDLE" for state in states),
            "stable_300ms_observed": end - start >= 0.3 and bool(states)
            and all(state == "IDLE" for state in states),
            "arm_feedback": _range_stats(_joint_maps(samples, "arm")),
            "arm_command": _range_stats(_joint_maps(samples, "arm_command"))}


def analyze_validation(test_dir: Path) -> dict[str, Any]:
    data = load_test_data(test_dir)
    report, trace = data["report"], data["trace"]
    return {
        "directory": str(test_dir),
        "executed": report.get("executed") is True,
        "probe_error": report.get("error"),
        "configuration": {key: report.get(key) for key in
                          ("duration_ms", "csv_rows", "scheme", "waist_channels", "stop_mode")},
        "observed_state_sequence": observed_sequence(trace),
        "arm_tracking_playing": analyze_arm_tracking(trace),
        "stop_stability": analyze_stop_stability(trace, report),
        "leg_command_continuity": analyze_command_continuity(trace, "leg_command"),
        "head_command_motion": analyze_head_motion(trace),
        "waist_command_motion": analyze_waist_motion(trace),
    }


def compare_validations(val1_dir: Path, val2_dir: Path) -> dict[str, Any]:
    first, second = analyze_validation(val1_dir), analyze_validation(val2_dir)
    first_pitch = first["waist_command_motion"]["phases"]["PLAYING"]["joints"].get(
        "waist_pitch_joint", {}).get("range_rad")
    second_pitch = second["waist_command_motion"]["phases"]["PLAYING"]["joints"].get(
        "waist_pitch_joint", {}).get("range_rad")
    comparison = {
        "playing_pitch_range_rad": {"extended_duration": first_pitch, "waist_hold": second_pitch},
        "waist_hold_reduced_pitch_range": (
            None if first_pitch is None or second_pitch is None else second_pitch < first_pitch),
        "note": "Metrics are evidence for human review; this file does not commission playback.",
    }
    return {"schema_version": 1, "validation_1": first, "validation_2": second,
            "comparison": comparison}


def _print_summary(result: dict[str, Any]) -> None:
    for key, label in (("validation_1", "Validation 1"), ("validation_2", "Validation 2")):
        item = result[key]
        print(f"{label}: {item['directory']}")
        print(f"  executed={item['executed']} error={item['probe_error'] or '-'}")
        print(f"  states={' -> '.join(item['observed_state_sequence']) or 'none'}")
        print(f"  arm max error={item['arm_tracking_playing']['max_abs_error_rad']}")
        print(f"  stop stable 300ms={item['stop_stability']['stable_300ms_observed']}")
        print(f"  leg max interval={item['leg_command_continuity']['max_message_interval_s']}")
        print(f"  head max offset={item['head_command_motion']['max_head_abs_offset_rad']}")
        print(f"  waist max offset={item['waist_command_motion']['max_waist_abs_offset_rad']}")
    print("No commissioning decision was made; review the JSON and raw evidence.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("validation_1", type=Path)
    parser.add_argument("validation_2", type=Path)
    parser.add_argument("--output", type=Path, help="write combined review metrics as JSON")
    args = parser.parse_args()
    result = compare_validations(args.validation_1, args.validation_2)
    _print_summary(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
