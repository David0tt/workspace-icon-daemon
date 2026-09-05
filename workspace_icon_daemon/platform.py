"""Compositor and bar-specific behavior."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class Compositor(str, Enum):
    """Supported compositor/window-manager implementations."""

    AUTO = "auto"
    I3 = "i3"
    SWAY = "sway"


class Bar(str, Enum):
    """Supported status bars."""

    AUTO = "auto"
    I3BAR = "i3bar"
    WAYBAR = "waybar"
    NONE = "none"


class Connection(Protocol):
    """The subset of i3ipc.Connection used for platform detection."""

    def get_version(self) -> object: ...


def detect_compositor(connection: Connection) -> Compositor:
    """Detect i3 or Sway from the IPC version response."""
    version = connection.get_version()
    values = (
        getattr(version, "human_readable", ""),
        getattr(version, "loaded_config_file_name", ""),
    )
    if any("sway" in str(value).casefold() for value in values):
        return Compositor.SWAY
    return Compositor.I3


def resolve_bar(bar: Bar, compositor: Compositor) -> Bar:
    """Resolve the default bar for a detected compositor."""
    if bar is not Bar.AUTO:
        return bar
    return Bar.WAYBAR if compositor is Compositor.SWAY else Bar.I3BAR


def program_name(window: object, compositor: Compositor) -> str | None:
    """Return a stable application identifier for an IPC window node."""
    window_class = getattr(window, "window_class", None)
    if compositor is Compositor.SWAY:
        return getattr(window, "app_id", None) or window_class
    return window_class


@dataclass(frozen=True)
class FontInstaller:
    """Install a generated font and refresh the selected bar."""

    bar: Bar
    fonts_dir: Path

    def install(self, source: Path) -> Path:
        """Copy a font into the user font directory and refresh consumers.

        This method performs a careful installation sequence to avoid bar crashes:
        1. Stop bar (prevents crash when modifying active font)
        2. Copy font to ~/.local/share/fonts
        3. Restart bar
        4. Refresh font cache with fc-cache
        5. Restart bar again (to load updated cache)

        This sequence is necessary because:
        - bar crashes if its active font file is modified
        - fc-cache is slow and shouldn't block bar restart
        - simply using a compository reload/restart is not enough to ensure fonts are reloaded properly
        """
        if not source.is_file():
            raise FileNotFoundError(f"Font file does not exist: {source}")

        self.fonts_dir.mkdir(parents=True, exist_ok=True)
        destination = self.fonts_dir / source.name
        devnull = subprocess.DEVNULL

        if self.bar is not Bar.NONE:
            subprocess.run(
                ["pkill", self.bar.value],
                check=False,
                stdout=devnull,
                stderr=devnull,
            )

        shutil.copy2(source, destination)

        # Restart the bar immediately so the slow font-cache refresh does not
        # leave it unavailable.
        if self.bar is not Bar.NONE:
            subprocess.Popen(
                [self.bar.value],
                start_new_session=True,
                stdout=devnull,
                stderr=devnull,
            )

        subprocess.run(
            ["fc-cache", "-f", str(self.fonts_dir)],
            check=True,
            stdout=devnull,
            stderr=devnull,
        )

        # Restart once more so the bar loads the newly refreshed font cache.
        if self.bar is not Bar.NONE:
            subprocess.run(
                ["pkill", self.bar.value],
                check=False,
                stdout=devnull,
                stderr=devnull,
            )
            subprocess.Popen(
                [self.bar.value],
                start_new_session=True,
                stdout=devnull,
                stderr=devnull,
            )

        logger.info("Installed icon font at %s", destination)
        return destination
