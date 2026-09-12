"""本地简易模拟器：``uv run x2-teleop-sim``。"""

from .core import JoySample, Ps5Teleop


def main() -> None:
    teleop = Ps5Teleop(timeout=0.5)
    print("PS5 映射模拟器；请输入 6 个轴值，再输入按钮值")
    try:
        while True:
            raw = input("> ").split()
            axes = tuple(float(v) for v in raw[:6])
            buttons = tuple(int(v) for v in raw[6:])
            print(teleop.update(JoySample(axes=axes, buttons=buttons)))
    except (EOFError, KeyboardInterrupt):
        print("\n再见")


if __name__ == "__main__":
    main()
