"""使用 pygame-ce 的 SDL 摇杆后端读取 DualSense。

蓝牙配对由操作系统/X2 完成。本类只读取已配对的 HID 设备，并报告连接状态变化。
"""

from __future__ import annotations

import time
from typing import Iterator

from .mapping import ControllerInput, DEFAULT_MAPPING


class DualSenseReader:
    # SDL commonly exposes both USB and Bluetooth DualSense devices as
    # "Wireless Controller". Keep the explicit hint for custom names, while
    # accepting the names used by the stock SDL mapping.
    _KNOWN_NAME_MARKERS = ("dualsense", "dual sense", "ps5", "wireless controller")

    def __init__(self, mapping=DEFAULT_MAPPING, name_hint: str = "dualsense"):
        self.mapping = mapping
        self.name_hint = name_hint.strip().lower()
        self._pygame = None
        self._joystick = None
        self._joystick_instance_id = None
        self._connected = False

    def start(self) -> None:
        try:
            import pygame
        except ImportError as exc:  # pragma: no cover - 仅依赖/运行时场景
            raise RuntimeError("DualSense 输入需要安装 pygame-ce") from exc
        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()
        self._open_first_matching()

    def close(self) -> None:
        if self._joystick is not None:
            self._joystick.quit()
        if self._pygame is not None:
            self._pygame.joystick.quit()
            self._pygame.quit()
        self._joystick = None
        self._joystick_instance_id = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the last poll returned a live joystick sample."""
        return self._connected

    def device_names(self) -> tuple[str, ...]:
        """Return currently enumerated SDL joystick names for diagnostics."""
        if self._pygame is None:
            raise RuntimeError("必须先调用 DualSenseReader.start()")
        names: list[str] = []
        for index in range(self._pygame.joystick.get_count()):
            try:
                joystick = self._pygame.joystick.Joystick(index)
                names.append(str(joystick.get_name()))
            except (self._pygame.error, OSError):
                # A device can disappear while SDL is enumerating it.
                continue
        return tuple(names)

    def _matches_name(self, name: str) -> bool:
        normalized = " ".join(str(name).lower().replace("-", " ").split())
        return bool(self.name_hint and self.name_hint in normalized) or any(
            marker in normalized for marker in self._KNOWN_NAME_MARKERS
        )

    def _open_first_matching(self) -> None:
        if self._pygame is None:
            return
        for index in range(self._pygame.joystick.get_count()):
            try:
                joystick = self._pygame.joystick.Joystick(index)
                name = joystick.get_name()
            except (self._pygame.error, OSError):
                continue
            if self._matches_name(name):
                joystick.init()
                self._joystick = joystick
                try:
                    self._joystick_instance_id = joystick.get_instance_id()
                except (AttributeError, self._pygame.error, OSError):
                    self._joystick_instance_id = None
                return

    def poll(self) -> ControllerInput:
        if self._pygame is None:
            raise RuntimeError("必须先调用 DualSenseReader.start()")
        for event in self._pygame.event.get():
            if event.type == self._pygame.JOYDEVICEADDED and self._joystick is None:
                self._open_first_matching()
            elif event.type == self._pygame.JOYDEVICEREMOVED:
                removed_id = getattr(event, "instance_id", getattr(event, "joy", None))
                if self._joystick is not None and (
                    removed_id is None or self._joystick_instance_id is None or removed_id == self._joystick_instance_id
                ):
                    self._joystick = None
                    self._joystick_instance_id = None
        if self._joystick is None:
            self._connected = False
            return ControllerInput(connected=False)

        try:
            axes = tuple(self._joystick.get_axis(i) for i in range(self._joystick.get_numaxes()))
            buttons = tuple(self._joystick.get_button(i) for i in range(self._joystick.get_numbuttons()))
        except (self._pygame.error, OSError):
            self._joystick = None
            self._joystick_instance_id = None
            self._connected = False
            return ControllerInput(connected=False)
        self._connected = True
        return ControllerInput(axes=axes, buttons=buttons, connected=True, stamp=time.monotonic())

    def samples(self, period: float = 0.02) -> Iterator[ControllerInput]:
        self.start()
        try:
            while True:
                yield self.poll()
                time.sleep(period)
        finally:
            self.close()
