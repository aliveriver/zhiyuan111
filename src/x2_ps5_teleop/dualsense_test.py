"""独立运行的 DualSense 实时输入诊断工具。"""

from __future__ import annotations

import argparse
import time

from .controller.dualsense import DualSenseReader


def main() -> None:
    parser = argparse.ArgumentParser(description="显示 DualSense 实时轴值、按钮值和连接状态")
    parser.add_argument("--hz", type=float, default=10.0)
    parser.add_argument("--name-hint", default="dualsense", help="SDL 设备名过滤提示（默认 dualsense）")
    args = parser.parse_args()
    reader = DualSenseReader(name_hint=args.name_hint)
    last_connected = None
    last_devices = None
    try:
        reader.start()
        print("正在等待 PS5 DualSense（USB 或蓝牙 HID）。按 Ctrl+C 停止。")
        while True:
            devices = reader.device_names()
            if devices != last_devices:
                print(f"pygame joysticks={len(devices)}", flush=True)
                for index, name in enumerate(devices):
                    print(f"  [{index}] {name}", flush=True)
                if not devices:
                    print("未发现 SDL 手柄；检查 /dev/input、蓝牙配对或官方遥操是否占用手柄。", flush=True)
                last_devices = devices
            sample = reader.poll()
            if sample.connected != last_connected:
                print("connected" if sample.connected else "disconnected", flush=True)
                last_connected = sample.connected
            if sample.connected:
                axes = sample.axes + (0.0,) * 6
                buttons = sample.buttons + (0,) * 13
                print(
                    "LX={:+.3f} LY={:+.3f} RX={:+.3f} RY={:+.3f} "
                    "LT={:+.3f} RT={:+.3f} L1={} R1={} Cross={} Circle={} Square={} Triangle={}".format(
                        axes[0], axes[1], axes[2], axes[3], axes[4], axes[5],
                        buttons[4], buttons[5], buttons[0], buttons[1], buttons[2], buttons[3],
                    ),
                    flush=True,
                )
            time.sleep(1.0 / max(args.hz, 1.0))
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()


if __name__ == "__main__":
    main()
