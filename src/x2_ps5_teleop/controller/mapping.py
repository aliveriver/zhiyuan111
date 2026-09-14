"""输入层和遥操作层使用的稳定 PS5/SDL 编号。"""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from ..core import Mapping


@dataclass(frozen=True)
class ControllerInput:
    """来自 DualSense 或测试源的标准化快照。"""

    axes: tuple[float, ...] = ()
    buttons: tuple[int, ...] = ()
    connected: bool = True
    stamp: float = field(default_factory=time.monotonic)

    def joy_sample(self):
        from ..core import JoySample

        return JoySample(self.axes, self.buttons, self.stamp, self.connected)


# SDL 的标准 DualSense 布局。使用新固件时，请通过输入测试进行确认。
DEFAULT_MAPPING = Mapping(
    left_x=0,
    left_y=1,
    right_x=2,
    lt=4,
    rt=5,
    cross=0,
    circle=1,
    triangle=3,
    square=2,
    l1=4,
    r1=5,
    teleop_toggle=9,       # OPTIONS 切换 IDLE <-> TELEOP
    emergency_stop=12,     # PS 键作为软件急停
    max_linear_x=0.12,
    max_linear_y=0.08,
    max_angular_z=0.15,
)
