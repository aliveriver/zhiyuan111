"""Collect a read-only X2 host and ROS 2 interface report."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
from collections.abc import Sequence


MATCH_TERMS = (
    "aima",
    "aimdk",
    "hand",
    "joint",
    "locomotion",
    "motion",
    "joy",
    "gamepad",
    "teleop",
)


def _run(command: Sequence[str], timeout: float = 8.0) -> str:
    if shutil.which(command[0]) is None:
        return f"unavailable: {command[0]}"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    output = (result.stdout + result.stderr).strip()
    return output or f"exit={result.returncode} (no output)"


def _section(title: str, command: Sequence[str], timeout: float = 8.0) -> None:
    print(f"\n## {title}\n{_run(command, timeout)}")


def _relevant_ros_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if any(term in line.lower() for term in MATCH_TERMS)]


def _interface_types(lines: Sequence[str]) -> set[str]:
    types: set[str] = set()
    for line in lines:
        if "[" not in line or "]" not in line:
            continue
        for interface_type in line.split("[", 1)[1].split("]", 1)[0].split(","):
            if interface_type.strip():
                types.add(interface_type.strip())
    return types


def _process_report() -> None:
    # Command arguments may contain credentials; never include them in reports.
    output = _run(["ps", "-eo", "user,pid,comm", "--sort=comm"])
    print("\n## Processes")
    print(output)


def _ros_report() -> None:
    print("\n## ROS environment")
    for name in ("ROS_VERSION", "ROS_DISTRO", "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "AMENT_PREFIX_PATH"):
        print(f"{name}={os.environ.get(name, '')}")

    for kind in ("node", "topic", "service", "action"):
        command = ["ros2", kind, "list"]
        if kind != "node":
            command.append("-t")
        output = _run(command, timeout=15.0)
        print(f"\n## ros2 {kind} list")
        print(output)
        relevant_lines = _relevant_ros_lines(output)
        for interface_type in sorted(_interface_types(relevant_lines)):
            print(f"\n### ros2 interface show {interface_type}")
            print(_run(["ros2", "interface", "show", interface_type], timeout=10.0))
        if kind == "topic":
            topic_names = []
            for line in relevant_lines:
                name = line.split()[0]
                if name.startswith("/"):
                    topic_names.append(name)
            for name in sorted(set(topic_names)):
                print(f"\n### ros2 topic info -v {name}")
                print(_run(["ros2", "topic", "info", "-v", name], timeout=10.0))


def main() -> None:
    parser = argparse.ArgumentParser(description="只读采集 X2 节点、进程和 ROS 2 接口")
    parser.add_argument("--no-ros", action="store_true", help="跳过 ROS 2 图调查")
    args = parser.parse_args()

    print("# X2 node inspection")
    print(f"hostname={socket.gethostname()}")
    print(f"platform={platform.platform()}")
    _section("Operating system", ["cat", "/etc/os-release"])
    _section("CPU", ["lscpu"])
    _section("PCI devices", ["lspci", "-nn"])
    _section("NVIDIA GPU", ["nvidia-smi", "-L"])
    _section("NPU", ["npu-smi", "info"])
    _process_report()
    _section("Running services", ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"])
    _section("Containers", ["docker", "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Status}}"])
    _section("Input devices", ["cat", "/proc/bus/input/devices"])
    _section("Bluetooth", ["systemctl", "is-active", "bluetooth"])
    if not args.no_ros:
        _ros_report()


if __name__ == "__main__":
    main()
