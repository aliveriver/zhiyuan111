"""机器人适配器使用的简单安全限幅器。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyLimiter:
    max_linear_x: float = 0.12
    max_linear_y: float = 0.08
    max_angular_z: float = 0.15

    def clamp(self, linear_x: float, linear_y: float, angular_z: float) -> tuple[float, float, float]:
        return (
            max(-self.max_linear_x, min(self.max_linear_x, linear_x)),
            max(-self.max_linear_y, min(self.max_linear_y, linear_y)),
            max(-self.max_angular_z, min(self.max_angular_z, angular_z)),
        )
