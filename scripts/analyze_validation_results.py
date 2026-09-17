#!/usr/bin/env python3
"""Analyze and compare MC animation validation test results.

This script compares Validation 1 (extended duration) and Validation 2 (with waist)
to determine if adding constant waist columns stabilizes waist motion during playback.
"""
import json
import sys
from pathlib import Path
from typing import Any


def load_test_data(test_dir: Path) -> dict[str, Any]:
    """Load report and trace from a validation test directory."""
    report_path = test_dir / "report.json"
    trace_path = test_dir / "trace.json"

    if not report_path.exists() or not trace_path.exists():
        raise FileNotFoundError(f"Missing data files in {test_dir}")

    with open(report_path) as f:
        report = json.load(f)
    with open(trace_path) as f:
        trace = json.load(f)

    return {"report": report, "trace": trace}


def analyze_waist_motion(trace: list[dict], phase_key: str = "mc") -> dict:
    """Analyze waist joint motion across MC player states."""
    waist_joints = ["waist_pitch_joint", "waist_roll_joint", "waist_yaw_joint"]

    # Collect samples by MC player state
    phases = {"PRE_PLAYING": [], "PLAYING": [], "IDLE": []}

    for sample in trace:
        mc_state = sample.get(phase_key)
        if not mc_state:
            continue

        player_state = mc_state.get("player_state")
        waist_cmd = sample.get("waist_command", {})

        if player_state in phases and waist_cmd:
            phases[player_state].append(waist_cmd)

    # Calculate ranges for each phase
    results = {}
    for phase, samples in phases.items():
        if not samples:
            results[phase] = {"count": 0}
            continue

        joint_ranges = {}
        for joint in waist_joints:
            values = [s.get(joint) for s in samples if joint in s]
            if values:
                joint_ranges[joint] = {
                    "min": min(values),
                    "max": max(values),
                    "range": max(values) - min(values),
                    "samples": len(values)
                }

        results[phase] = {
            "count": len(samples),
            "joints": joint_ranges
        }

    return results


def compare_validations(val1_dir: Path, val2_dir: Path) -> None:
    """Compare Validation 1 (extended) vs Validation 2 (with waist)."""
    print("=" * 80)
    print("MC Animation Validation Results Comparison")
    print("=" * 80)
    print()

    # Load data
    print("Loading test data...")
    val1 = load_test_data(val1_dir)
    val2 = load_test_data(val2_dir)

    # Report summaries
    print("\n" + "-" * 80)
    print("Test Configuration")
    print("-" * 80)

    for name, data in [("Validation 1 (Extended)", val1), ("Validation 2 (With Waist)", val2)]:
        report = data["report"]
        print(f"\n{name}:")
        print(f"  Duration: {report.get('duration_ms', 'N/A')} ms")
        print(f"  CSV Rows: {report.get('csv_rows', 'N/A')}")
        print(f"  Scheme: {report.get('scheme', 'N/A')}")
        print(f"  Waist Channels: {report.get('waist_channels', [])}")
        print(f"  Executed: {report.get('executed', False)}")
        if "error" in report:
            print(f"  ⚠️  Error: {report['error']}")

    # Analyze waist motion
    print("\n" + "-" * 80)
    print("Waist Motion Analysis")
    print("-" * 80)

    val1_waist = analyze_waist_motion(val1["trace"])
    val2_waist = analyze_waist_motion(val2["trace"])

    for phase in ["PRE_PLAYING", "PLAYING", "IDLE"]:
        print(f"\n{phase} Phase:")

        for name, analysis in [("Val 1 (Extended)", val1_waist), ("Val 2 (With Waist)", val2_waist)]:
            phase_data = analysis.get(phase, {})
            sample_count = phase_data.get("count", 0)
            print(f"\n  {name} ({sample_count} samples):")

            joints_data = phase_data.get("joints", {})
            if not joints_data:
                print("    No waist data captured")
                continue

            for joint, stats in joints_data.items():
                range_rad = stats["range"]
                print(f"    {joint}: range = {range_rad:.6f} rad ({stats['samples']} samples)")

    # Comparison conclusion
    print("\n" + "=" * 80)
    print("Conclusion")
    print("=" * 80)

    # Compare PLAYING phase ranges
    val1_playing = val1_waist.get("PLAYING", {}).get("joints", {})
    val2_playing = val2_waist.get("PLAYING", {}).get("joints", {})

    if val1_playing and val2_playing:
        print("\nWaist Pitch Range Comparison (PLAYING phase):")
        val1_pitch_range = val1_playing.get("waist_pitch_joint", {}).get("range", 0)
        val2_pitch_range = val2_playing.get("waist_pitch_joint", {}).get("range", 0)

        print(f"  Validation 1 (Extended):    {val1_pitch_range:.6f} rad")
        print(f"  Validation 2 (With Waist):   {val2_pitch_range:.6f} rad")

        if val2_pitch_range < val1_pitch_range * 0.5:
            print("\n✅ Validation 2 SUCCESS: Adding waist columns significantly reduced waist motion")
            print("   → Waist range reduced by >50%")
        elif val2_pitch_range < val1_pitch_range * 0.8:
            print("\n⚠️  Validation 2 PARTIAL: Adding waist columns reduced waist motion")
            print("   → Waist range reduced but still significant")
        else:
            print("\n❌ Validation 2 FAILED: Adding waist columns did not stabilize waist")
            print("   → Waist motion persists despite constant waist targets")
    else:
        print("\n⚠️  Insufficient data to compare PLAYING phase waist motion")

    print("\n" + "=" * 80)
    print("Next Steps: Review full trace data and consult MC_ANIMATION_NEXT_VALIDATION.md")
    print("=" * 80)
    print()


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <validation-1-dir> <validation-2-dir>")
        print()
        print("Example:")
        print(f"  {sys.argv[0]} /tmp/validation-1-extended /tmp/validation-2-waist")
        sys.exit(1)

    val1_dir = Path(sys.argv[1])
    val2_dir = Path(sys.argv[2])

    if not val1_dir.exists():
        print(f"Error: Validation 1 directory not found: {val1_dir}")
        sys.exit(1)

    if not val2_dir.exists():
        print(f"Error: Validation 2 directory not found: {val2_dir}")
        sys.exit(1)

    compare_validations(val1_dir, val2_dir)


if __name__ == "__main__":
    main()
