"""Compositor detection and font installation."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
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


def program_name(window: object, compositor: Compositor) -> str | None:
    """Return a stable application identifier for an IPC window node."""
    window_class = getattr(window, "window_class", None)
    if compositor is Compositor.SWAY:
        return getattr(window, "app_id", None) or window_class
    return window_class


@dataclass(frozen=True)
class FontInstaller:
    """Install a generated font."""

    fonts_dir: Path

    def install(self, source: Path) -> Path:
        """Atomically install a font and refresh fontconfig's cache.

        Already-running renderers keep using the font they loaded at session
        start.  In particular, this method deliberately never restarts a bar or
        compositor; the replacement becomes usable after the user's next login.
        """
        if not source.is_file():
            raise FileNotFoundError(f"Font file does not exist: {source}")

        self.fonts_dir.mkdir(parents=True, exist_ok=True)
        destination = self.fonts_dir / source.name
        # Do not truncate a font file while a renderer may have it mmap'ed.
        # Publish a fully written replacement as a new inode instead.
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{source.name}.", dir=self.fonts_dir
        )
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)
        try:
            shutil.copy2(source, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

        subprocess.run(
            ["fc-cache", "-f", str(self.fonts_dir)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        logger.info("Installed icon font at %s", destination)
        return destination
