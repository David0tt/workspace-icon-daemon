#!/usr/bin/env python3
"""Workspace and titlebar icons for i3 and Sway.

Creates custom icon font from all installed programs. After next restart those
are automatically used to dynamically set workspace names and window titles
on window events. 
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import FrameType

import i3ipc
import yaml
from fontTools.ttLib import TTFont

from .font_builder import FontBuilder
from .platform import (
    Compositor,
    FontInstaller,
    detect_compositor,
    program_name,
)


def get_xdg_config_home() -> Path:
    """Get the XDG config directory for user-specific configuration files.

    Returns the directory specified by $XDG_CONFIG_HOME or defaults to
    ~/.config according to the XDG Base Directory Specification.

    Returns:
        Path to XDG config home directory.
    """
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def get_xdg_cache_home() -> Path:
    """Get the XDG cache directory for user-specific cache data.

    Returns the directory specified by $XDG_CACHE_HOME or defaults to
    ~/.cache according to the XDG Base Directory Specification.

    Returns:
        Path to XDG cache home directory.
    """
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def get_xdg_data_home() -> Path:
    """Get the XDG directory for user-specific data files."""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def get_resource_path(filename: str) -> Path:
    """Get the absolute path to a resource file bundled with this module.

    This function looks for resource files (fonts, icons) in the same
    directory as this module, which works for both editable installs
    and regular package installations.

    Args:
        filename: Name of the resource file.

    Returns:
        Absolute path to the resource file.
    """
    module_dir = Path(__file__).parent.resolve()
    return module_dir / filename


# Application name for XDG directories
APP_NAME = "workspace-icon-daemon"

# XDG-compliant default paths
DEFAULT_CONFIG_DIR = get_xdg_config_home() / APP_NAME
DEFAULT_CACHE_DIR = get_xdg_cache_home() / APP_NAME

# User-specific configuration file (persists across updates)
DEFAULT_PROGRAM_ICON_MAP_PATH = DEFAULT_CONFIG_DIR / "program_icon_map.yaml"

# Generated font file (can be regenerated, so it's cache)
DEFAULT_FONT_OUTPUT_PATH = DEFAULT_CACHE_DIR / "WorkspaceIconDaemon.ttf"
DEFAULT_PID_PATH = DEFAULT_CACHE_DIR / "daemon.pid"

# Read-only resource files bundled with the package
DEFAULT_BASE_FONT_PATH = get_resource_path("NotoColorEmoji.ttf")
PLACEHOLDER_ICON_PATH = get_resource_path("placeholder_icon.svg")

# Manual program name corrections for icon lookup
PROGRAM_NAME_CORRECTIONS = {
    "thunderbird-esr": "thunderbird_thunderbird",
    "firefox-esr": "firefox_firefox",
}

# Other constants
DEFAULT_FONT_FAMILY_NAME = "WorkspaceIconDaemon"
PUA_START = 0xE000
PLACEHOLDER_CODEPOINT = PUA_START
PROGRAM_PUA_START = PUA_START + 1

logger = logging.getLogger(__name__)


class UniqueIconsMode(Enum):
    """Mode for displaying workspace icons."""

    NONUNIQUE = "nonunique"
    NUMBERS_SUPERSCRIPT = "numbers_superscript"
    NUMBERS_SUBSCRIPT = "numbers_subscript"
    UNIQUE = "unique"


@dataclass(frozen=True)
class ProgramIconEntry:
    """Represents a single program-to-icon mapping entry.

    Attributes:
        icon_path: Path to the icon file, or None if no icon was found.
        unicode_id: The Unicode codepoint assigned to this program.
    """

    icon_path: Path | None
    unicode_id: int


@dataclass
class WorkspaceInfo:
    """Information about a workspace and its programs."""

    num: int
    name: str
    programs: list[str]


class ProgramIconMap:
    """Manages the mapping between program names, icon paths, and Unicode IDs.

    This class handles persistence of program-to-icon mappings, assigning
    unique Unicode codepoints from the Private Use Area (PUA) to each program.

    Attributes:
        filepath: Path to the YAML file storing the mappings.
        programs: Mapping from program names to icon entries.
        next_unicode_id: The next available Unicode codepoint in the PUA.
    """

    def __init__(self, filepath: Path = DEFAULT_PROGRAM_ICON_MAP_PATH) -> None:
        """Initialize the program icon map.

        Args:
            filepath: Path to the YAML file storing the mappings.
        """
        self.filepath: Path = filepath
        self.programs: dict[str, ProgramIconEntry] = {}
        self.next_unicode_id: int = PROGRAM_PUA_START
        self.modified_at_load: bool = False

        if not self.filepath.exists():
            logger.debug(
                "Program icon map not found at %s, starting fresh", self.filepath
            )
        else:
            self.modified_at_load = self._load()

    def _load(self) -> bool:
        """Load the program icon map from the YAML file.

        Verifies that all icon paths exist (if not None). Removes entries with
        missing icon paths and saves the cleaned map if any entries were removed.

        Returns:
            True if any entries were removed (map was modified), False otherwise.

        Raises:
            ValueError: If an entry has invalid format.
        """
        self.programs.clear()

        with open(self.filepath, encoding="utf-8") as file:
            raw_data = yaml.safe_load(file) or {}

        removed_programs = []
        for program_name, entry in raw_data.items():
            if not isinstance(entry, dict) or "icon_path" not in entry:
                raise ValueError(f"Invalid entry for {program_name} in {self.filepath}")

            unicode_id = entry.get("unicode_id", -1)
            icon_path_str = entry["icon_path"]
            icon_path = Path(icon_path_str) if icon_path_str is not None else None

            if icon_path is not None and not icon_path.exists():
                logger.debug(
                    "Icon path for %s does not exist: %s. Removing entry.",
                    program_name,
                    icon_path,
                )
                removed_programs.append(program_name)
                continue

            self.programs[program_name] = ProgramIconEntry(
                icon_path=icon_path, unicode_id=unicode_id
            )

        # Calculate next_unicode_id from entries that have icons only
        if self.programs:
            valid_unicode_ids = [
                e.unicode_id for e in self.programs.values() if e.icon_path is not None
            ]
            if valid_unicode_ids:
                self.next_unicode_id = max(
                    PROGRAM_PUA_START, max(valid_unicode_ids) + 1
                )

        logger.debug("Loaded %d programs from %s", len(self.programs), self.filepath)

        if removed_programs:
            logger.debug(
                "Removed %d programs with missing icon paths: %s",
                len(removed_programs),
                ", ".join(removed_programs),
            )
            self.save()
            return True

        return False

    def save(self) -> None:
        """Save the program icon map to the YAML file.

        Raises:
            OSError: If file operations fail.
            yaml.YAMLError: If YAML serialization fails.
        """
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)

            with open(self.filepath, "w", encoding="utf-8") as file:
                yaml.safe_dump(
                    {
                        name: {
                            "icon_path": str(e.icon_path) if e.icon_path else None,
                            "unicode_id": e.unicode_id,
                        }
                        for name, e in self.programs.items()
                    },
                    file,
                    default_flow_style=False,
                    sort_keys=True,
                )

            logger.debug("Saved program icon map to %s", self.filepath)
        except (OSError, yaml.YAMLError) as exc:
            raise OSError(f"Failed to save program icon map: {exc}") from exc

    def add_program(
        self, program_name: str, icon_path: Path | None
    ) -> tuple[bool, int | None]:
        """Add a new program to the map and assign it a Unicode ID.

        Args:
            program_name: The name of the program.
            icon_path: Path to the program's icon file, or None if no icon exists.

        Returns:
            A tuple of (was_added, unicode_id) where was_added is True if the
            program was newly added, False if it already existed. unicode_id
            will be None if icon_path is None (no icon found).

        Raises:
            FileNotFoundError: If icon_path is not None and doesn't exist.
        """
        if program_name in self.programs:
            entry = self.programs[program_name]
            return False, entry.unicode_id if entry.icon_path is not None else None

        if icon_path is not None and not icon_path.exists():
            raise FileNotFoundError(f"Icon path does not exist: {icon_path}")

        # Only assign Unicode ID if we have an icon
        if icon_path is not None:
            unicode_id = self.next_unicode_id
            self.programs[program_name] = ProgramIconEntry(icon_path, unicode_id)
            self.next_unicode_id += 1
            logger.debug(
                "Added program: %s -> %s -> U+%04X", program_name, icon_path, unicode_id
            )
            return True, unicode_id
        else:
            # Store program without Unicode ID
            self.programs[program_name] = ProgramIconEntry(None, -1)
            logger.debug("Added program: %s -> (no icon, no Unicode ID)", program_name)
            return True, None

    def get_unicode_id(self, program_name: str) -> int | None:
        """Get the Unicode ID for a program.

        Args:
            program_name: The name of the program.

        Returns:
            The Unicode ID, or None if the program is not in the map or has no icon.
        """
        if program_name not in self.programs:
            return None
        entry = self.programs[program_name]
        # Return None if no icon exists (unicode_id will be -1)
        return entry.unicode_id if entry.icon_path is not None else None

    def get_icon_path(self, program_name: str) -> Path | None:
        """Get the icon path for a program.

        Args:
            program_name: The name of the program.

        Returns:
            The icon path (or None if no icon exists for the program), or None
            if the program is not in the map at all.
        """
        entry = self.programs.get(program_name)
        return entry.icon_path if entry else None

    def get_all_icon_paths_ordered(self) -> list[Path]:
        """Get all icon paths ordered by Unicode ID.

        Returns:
            Icon paths sorted by their assigned Unicode IDs. Entries with None
            icon_path are excluded. All returned paths are guaranteed to exist
            (verified at load/add time).
        """
        return [
            entry.icon_path
            for _, entry in sorted(self.programs.items(), key=lambda x: x[1].unicode_id)
            if entry.icon_path is not None
        ]

    def get_missing_programs(self, program_names: list[str]) -> list[str]:
        """Get list of programs that are not yet in the map.

        Args:
            program_names: List of program names to check.

        Returns:
            List of program names that are not in the map.
        """
        return [name for name in program_names if name not in self.programs]


class WorkspaceIconDaemon:
    """Manage application icons in i3 or Sway workspace names.

    Monitors compositor window events and dynamically updates workspace names with
    application icons. When new applications are detected, their icons are
    discovered, added to a custom font, and the font is rebuilt.

    Attributes:
        connection: i3ipc connection object.
        program_icon_map: Manager for program-to-icon mappings.
        base_font_path: Path to the base font file.
        font_output_path: Path where the custom font is saved.
        font_family_name: Name of the custom font family.
        unique_icons_mode: Mode for displaying workspace icons.
    """

    IGNORED_PROGRAMS = {
        "fzf",
        "tmux",
        "screen",
        "vim",
        "nano",
        "htop",
        "btop",
        "less",
        "man",
        "ssh",
    }

    # Unicode combining characters for superscript and subscript numbers
    SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
    SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"

    def __init__(
        self,
        connection: i3ipc.Connection,
        compositor: Compositor,
        font_installer: FontInstaller,
        program_icon_map_path: Path = DEFAULT_PROGRAM_ICON_MAP_PATH,
        base_font_path: Path = DEFAULT_BASE_FONT_PATH,
        font_output_path: Path = DEFAULT_FONT_OUTPUT_PATH,
        font_family_name: str = DEFAULT_FONT_FAMILY_NAME,
        unique_icons_mode: UniqueIconsMode = UniqueIconsMode.NUMBERS_SUBSCRIPT,
        use_placeholder_icon: bool = True,
        workspace_icons: bool = True,
        titlebar_icons: bool = False,
    ) -> None:
        """Initialize the workspace icon daemon.

        Args:
            connection: i3ipc connection object.
            compositor: Active compositor/window manager.
            font_installer: Installer used after rebuilding the icon font.
            program_icon_map_path: Path to the program icon map YAML file.
            base_font_path: Path to the base font file.
            font_output_path: Path where the custom font is saved.
            font_family_name: Name of the custom font family.
            unique_icons_mode: Mode for displaying workspace icons.
            use_placeholder_icon: If True (default), use a placeholder icon for
                programs without icons. If False, programs without icons are
                tracked but don't get Unicode IDs.
            workspace_icons: Add generated icons to workspace names.
            titlebar_icons: Add each application's generated icon to its window
                title using the compositor's title_format command.
        """
        self.connection = connection
        self.compositor = compositor
        self.font_installer = font_installer
        self.program_icon_map: ProgramIconMap = ProgramIconMap(program_icon_map_path)
        self.base_font_path: Path = base_font_path
        self.font_output_path: Path = font_output_path
        self.font_family_name: str = font_family_name
        self.unique_icons_mode: UniqueIconsMode = unique_icons_mode
        self.use_placeholder_icon: bool = use_placeholder_icon
        self.workspace_icons: bool = workspace_icons
        self.titlebar_icons: bool = titlebar_icons
        self._titlebar_icon_codepoints: dict[int, int] = {}
        # This is intentionally a snapshot.  Installing a replacement font does
        # not make its glyphs available to processes in the current session.
        self._active_program_codepoints: dict[str, int] = {}
        self._active_placeholder_available = False

    @staticmethod
    def _sort_windows_by_layout(windows: list[i3ipc.Con]) -> list[i3ipc.Con]:
        """Sort windows by their visual layout position (left-to-right, top-to-bottom).

        Args:
            windows: List of window containers.

        Returns:
            Windows sorted by visual position: top-to-bottom, then left-to-right.
        """
        # Sort by y-coordinate (top to bottom) first, then x-coordinate (left to right)
        # Using a small tolerance for y-coordinates to handle windows at similar heights
        return sorted(windows, key=lambda w: (w.rect.y // 10, w.rect.x))

    @staticmethod
    def get_programs_by_workspace(
        connection: i3ipc.Connection,
        compositor: Compositor,
        ignored_programs: set[str],
    ) -> list[WorkspaceInfo]:
        """Get workspace info with programs for all workspaces.

        Args:
            connection: The i3ipc connection object.
            compositor: Active compositor/window manager.
            ignored_programs: Set of program names to ignore.

        Returns:
            List of WorkspaceInfo objects with workspace number, name, and programs.
        """
        workspaces_info = []

        for workspace in connection.get_tree().workspaces():
            # Get all windows and sort them by visual layout position
            windows = workspace.leaves()
            sorted_windows = WorkspaceIconDaemon._sort_windows_by_layout(windows)

            programs = [
                application_name
                for window in sorted_windows
                if (
                    application_name := WorkspaceIconDaemon._get_window_name(
                        window, compositor
                    )
                )
                and application_name not in ignored_programs
            ]

            workspaces_info.append(
                WorkspaceInfo(num=workspace.num, name=workspace.name, programs=programs)
            )

        return workspaces_info

    @staticmethod
    def _get_window_name(
        window: i3ipc.Con, compositor: Compositor
    ) -> str | None:
        """Extract the program identifier from a window.

        Sway uses app_id for native Wayland windows and WM_CLASS for XWayland.
        i3 uses WM_CLASS.

        Args:
            window: The IPC window container.
            compositor: Active compositor/window manager.

        Returns:
            The app_id or window class name, or None if neither is available.
        """
        raw_name = program_name(window, compositor)
        if not raw_name:
            return None

        corrected_name = PROGRAM_NAME_CORRECTIONS.get(raw_name, raw_name)
        return corrected_name

    @staticmethod
    def find_icon_for_program(program_name: str) -> Path | None:
        """Find an icon file for a given program name.

        Search order:
        1. Find .desktop file for the program
        2. Extract Icon= entry from .desktop file
        3. Resolve icon path from Icon= entry (full path or icon name)

        Args:
            program_name: The name of the program.

        Returns:
            Path to the icon file, or None if not found.
        """
        desktop_file = WorkspaceIconDaemon._find_desktop_file(program_name)
        if not desktop_file:
            logger.warning("No .desktop file found for program: %s", program_name)
            return None

        icon_name = WorkspaceIconDaemon._parse_desktop_icon(desktop_file)
        if not icon_name:
            logger.debug(
                "Found desktop file for %s but no Icon= entry: %s",
                program_name,
                desktop_file,
            )
            return None

        logger.debug(
            "Program %s: desktop %s -> Icon=%s",
            program_name,
            desktop_file,
            icon_name,
        )

        icon_path = WorkspaceIconDaemon._resolve_icon_path(icon_name)
        if icon_path:
            logger.debug("Resolved icon for %s: %s", program_name, icon_path)
        else:
            logger.debug(
                "Could not resolve icon '%s' for program %s",
                icon_name,
                program_name,
            )

        return icon_path

    @staticmethod
    def _generate_name_variants(name: str) -> list[str]:
        """Generate name variants with different separator styles.

        Creates variations by replacing separators (-, _, .) with different
        combinations to handle inconsistent naming conventions.

        Args:
            name: The base name to generate variants from.

        Returns:
            List of unique name variants (deduplicated).
        """
        separators = ["-", "_", ".", " "]
        replacements = ["", "_", "-", "."]
        variants = [name]

        for rep in replacements:
            variant = name
            for sep in separators:
                variant = variant.replace(sep, rep)
            variants.append(variant)

        return list(dict.fromkeys(variants))

    @staticmethod
    def _find_desktop_file(class_name: str) -> Path | None:
        """Find a .desktop file for a given window class name.

        Searches in multiple locations with progressively more fuzzy matching:
        1. Exact match (case-sensitive)
        2. Exact match (case-insensitive)
        3. Substring/prefix/suffix match (case-sensitive)
        4. Substring/prefix/suffix match (case-insensitive)
        5. Separator-normalized variants

        Args:
            class_name: The window class name to search for.

        Returns:
            Path to the .desktop file, or None if not found.
        """
        search_paths = WorkspaceIconDaemon._desktop_application_paths()

        # 1. Exact match, case-sensitive
        for base in search_paths:
            if base.exists():
                candidate = base / f"{class_name}.desktop"
                if candidate.exists():
                    return candidate

        # 2. Exact match, case-insensitive
        lower_class = class_name.casefold()
        for base in search_paths:
            if base.exists():
                for path in base.rglob("*.desktop"):
                    if path.stem.casefold() == lower_class:
                        return path

        # 3. Fuzzy match, case-sensitive
        for base in search_paths:
            if base.exists():
                for pattern in [
                    f"**/*{class_name}*.desktop",
                    f"**/{class_name}*.desktop",
                    f"**/*{class_name}.desktop",
                ]:
                    for path in base.glob(pattern):
                        if path.is_file():
                            return path

        # 4. Fuzzy match, case-insensitive
        for base in search_paths:
            if base.exists():
                for path in base.rglob("*.desktop"):
                    stem_lower = path.stem.casefold()
                    if (
                        lower_class in stem_lower
                        or stem_lower.startswith(lower_class)
                        or stem_lower.endswith(lower_class)
                    ):
                        return path

        # 5. Separator-normalized variants
        variants = WorkspaceIconDaemon._generate_name_variants(class_name)
        for base in search_paths:
            if base.exists():
                for path in base.rglob("*.desktop"):
                    stem_lower = path.stem.casefold()
                    for variant in variants:
                        variant_lower = variant.casefold()
                        if (
                            stem_lower == variant_lower
                            or stem_lower.startswith(variant_lower)
                            or stem_lower.endswith(variant_lower)
                        ):
                            return path

        logger.warning("No .desktop file found for program: %s", class_name)
        return None

    @staticmethod
    def _parse_desktop_icon(desktop_path: Path) -> str | None:
        """Extract the Icon= value from a .desktop file.

        Parses only the [Desktop Entry] section and returns the first Icon= value.

        Args:
            desktop_path: Path to the .desktop file.

        Returns:
            The icon name or path, or None if not found or on error.
        """
        return WorkspaceIconDaemon._parse_desktop_value(desktop_path, "Icon")

    @staticmethod
    def _parse_desktop_value(desktop_path: Path, key: str) -> str | None:
        """Extract a value from a desktop file's ``[Desktop Entry]`` section."""
        try:
            with open(desktop_path, encoding="utf-8") as f:
                in_desktop_entry = False
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith("["):
                        in_desktop_entry = line == "[Desktop Entry]"
                        continue

                    if in_desktop_entry and line.startswith(f"{key}="):
                        icon_value = line.split("=", 1)[1].strip()
                        return icon_value if icon_value else None

        except (OSError, UnicodeError) as exc:
            logger.debug("Failed to read desktop file %s: %s", desktop_path, exc)

        return None

    @staticmethod
    def _search_icon_in_paths(icon_name: str, extension: str) -> Path | None:
        """Search for an icon file with given extension in standard paths.

        Args:
            icon_name: The icon name to search for.
            extension: File extension (e.g., 'svg' or 'png').

        Returns:
            Path to the icon file, or None if not found.
        """
        for search_path in WorkspaceIconDaemon._preferred_icon_search_paths(
            extension
        ):
            candidate = search_path / f"{icon_name}.{extension}"
            if candidate.exists():
                return candidate

        return None

    @staticmethod
    def _preferred_icon_search_paths(extension: str) -> list[Path]:
        """Return preferred application-icon directories in XDG precedence order."""
        if extension == "svg":
            relative_paths = [
                Path("icons/hicolor/scalable/apps"),
                Path("icons/Humanity/apps/16"),
                Path("icons/Humanity/apps/22"),
                Path("icons/Humanity/apps/24"),
                Path("icons/Humanity/apps/32"),
                Path("icons/Humanity/apps/48"),
                Path("icons/Humanity/apps/64"),
                Path("icons/Humanity/apps/128"),
                Path("icons/Humanity/apps/192"),
                Path("icons/HighContrast/scalable/apps"),
                Path("pixmaps"),
            ]
        elif extension == "png":
            # The bundled font's bitmap strike is 109px. Prefer the nearest
            # source that does not need upscaling, then larger sources, followed
            # by progressively smaller fallbacks.
            relative_paths = [
                Path("icons/hicolor/128x128/apps"),
                Path("icons/hicolor/192x192/apps"),
                Path("icons/hicolor/256x256/apps"),
                Path("icons/hicolor/512x512/apps"),
                Path("icons/hicolor/96x96/apps"),
                Path("icons/hicolor/72x72/apps"),
                Path("icons/hicolor/64x64/apps"),
                Path("icons/hicolor/48x48/apps"),
                Path("icons/hicolor/36x36/apps"),
                Path("icons/hicolor/32x32/apps"),
                Path("icons/hicolor/24x24/apps"),
                Path("icons/hicolor/22x22/apps"),
                Path("icons/hicolor/16x16/apps"),
                Path("pixmaps"),
            ]
        else:
            raise ValueError(f"Unsupported icon extension: {extension}")

        return [
            data_dir / relative_path
            for data_dir in WorkspaceIconDaemon._xdg_data_dirs()
            for relative_path in relative_paths
        ]

    @staticmethod
    def _global_icon_search(icon_name: str, extension: str) -> Path | None:
        """Perform a global filesystem search for an icon.

        Args:
            icon_name: The icon name to search for.
            extension: File extension (e.g., 'svg' or 'png').

        Returns:
            Path to the icon file, or None if not found.
        """
        search_dirs = WorkspaceIconDaemon._icon_search_roots()

        target_filename = f"{icon_name}.{extension}"

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue

            try:
                # Use rglob to recursively search for the file
                for candidate in search_dir.rglob(target_filename):
                    if candidate.is_file():
                        logger.debug(
                            "Found %s via global search: %s",
                            extension.upper(),
                            candidate,
                        )
                        return candidate
            except (OSError, PermissionError):
                continue

        return None

    @staticmethod
    def _icon_search_roots() -> list[Path]:
        """Return icon roots in user/system precedence order."""
        roots: list[Path] = []
        for directory in WorkspaceIconDaemon._xdg_data_dirs():
            roots.extend([directory / "icons", directory / "pixmaps"])
        return roots

    @staticmethod
    def _xdg_data_dirs() -> list[Path]:
        """Return de-duplicated XDG data directories in lookup order."""
        data_dirs = [get_xdg_data_home()]
        data_dirs.extend(
            Path(item)
            for item in os.environ.get(
                "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
            ).split(":")
            if item
        )
        return list(dict.fromkeys(data_dirs))

    @staticmethod
    def _installed_icon_index() -> dict[str, Path]:
        """Index icons once while preserving the lookup precedence."""
        icons: dict[str, Path] = {}

        # Use the same tiers as _resolve_icon_path: first check the curated
        # application directories for SVG, then PNG. In particular, a color
        # hicolor PNG must beat an unrelated theme's monochrome SVG.
        for extension in ("svg", "png"):
            for directory in WorkspaceIconDaemon._preferred_icon_search_paths(
                extension
            ):
                if not directory.is_dir():
                    continue
                try:
                    for path in sorted(directory.glob(f"*.{extension}")):
                        if path.is_file():
                            icons.setdefault(path.stem.casefold(), path)
                except OSError as exc:
                    logger.debug("Could not index icons in %s: %s", directory, exc)

        # Only after all preferred application directories have been indexed do
        # the recursive fallbacks used by _global_icon_search.
        for extension in ("svg", "png"):
            for root in WorkspaceIconDaemon._icon_search_roots():
                if not root.is_dir():
                    continue
                try:
                    for path in sorted(root.rglob(f"*.{extension}")):
                        if path.is_file():
                            icons.setdefault(path.stem.casefold(), path)
                except OSError as exc:
                    logger.debug("Could not index icons in %s: %s", root, exc)
        return icons

    @staticmethod
    def _resolve_icon_from_index(
        icon_name: str | None, icon_index: dict[str, Path]
    ) -> Path | None:
        """Resolve a desktop-entry icon using a precomputed index."""
        if not icon_name:
            return None
        path = Path(icon_name)
        if (
            path.is_absolute()
            and path.is_file()
            and path.suffix.casefold() in {".svg", ".png"}
        ):
            return path
        known_extensions = {".svg", ".png", ".xpm", ".ico", ".gif", ".jpg", ".jpeg"}
        search_name = (
            path.stem if path.suffix.casefold() in known_extensions else icon_name
        )
        return icon_index.get(search_name.casefold())

    @staticmethod
    def _resolve_icon_path(icon_name: str) -> Path | None:
        """Resolve an icon name or path to an actual icon file path.

        Args:
            icon_name: Either a full path or just an icon name.

        Returns:
            Path to the icon file, or None if not found.
        """
        # Check if it's already a full path
        icon_path = Path(icon_name)
        if icon_path.is_absolute() and icon_path.exists():
            return icon_path

        # Strip known image extensions so "foo.xpm" searches for "foo.svg"/"foo.png"
        known_extensions = {".svg", ".png", ".xpm", ".ico", ".gif", ".jpg", ".jpeg"}
        search_name = (
            icon_path.stem
            if icon_path.suffix.lower() in known_extensions
            else icon_name
        )

        # Search in standard paths (SVG first, then PNG)
        for extension in ["svg", "png"]:
            result = WorkspaceIconDaemon._search_icon_in_paths(search_name, extension)
            if result:
                return result

        # Global fallback searches
        for extension in ["svg", "png"]:
            result = WorkspaceIconDaemon._global_icon_search(search_name, extension)
            if result:
                return result

        return None

    @staticmethod
    def create_icon_font(
        program_icon_map: ProgramIconMap,
        base_font_path: Path,
        font_output_path: Path,
        font_family_name: str,
    ) -> None:
        """Create the icon font from the current program icon map.

        Args:
            program_icon_map: The program icon map containing icon paths.
            base_font_path: Path to the base font file.
            font_output_path: Path where the custom font is saved.
            font_family_name: Name of the custom font family.
        """
        logger.debug("Creating icon font...")

        entries = sorted(
            (
                entry
                for entry in program_icon_map.programs.values()
                if entry.icon_path is not None
            ),
            key=lambda entry: entry.unicode_id,
        )
        # Every generated font has a stable fallback glyph.  It is used during
        # the session in which a newly discovered application's real glyph has
        # been built but cannot yet be loaded by the current renderers.
        icon_paths = [PLACEHOLDER_ICON_PATH]
        icon_paths.extend(entry.icon_path for entry in entries)
        codepoints = [PLACEHOLDER_CODEPOINT]
        codepoints.extend(entry.unicode_id for entry in entries)

        builder = FontBuilder(
            base_font_path=base_font_path,
            output_font_path=font_output_path,
            image_paths=icon_paths,
            font_family_name=font_family_name,
            pua_start=PUA_START,
            remove_original_symbols=True,
            codepoints=codepoints,
            fallback_image_path=PLACEHOLDER_ICON_PATH,
        )
        try:
            builder.build_complete_font()
            builder.save()
        finally:
            if builder.ttfont is not None:
                builder.ttfont.close()

        logger.debug("Font created successfully: %s", font_output_path)

    def install_icon_font(self) -> None:
        """Install the generated font without restarting desktop processes."""
        self.font_installer.install(self.font_output_path)

    @staticmethod
    def notify(summary: str, body: str) -> None:
        """Send a best-effort desktop notification."""
        try:
            subprocess.run(
                ["notify-send", "--app-name", APP_NAME, summary, body],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("Could not send desktop notification: %s", exc)

    @staticmethod
    def _desktop_application_paths() -> list[Path]:
        """Return desktop-entry search roots in XDG precedence order."""
        data_dirs = [get_xdg_data_home()]
        data_dirs.extend(
            Path(item)
            for item in os.environ.get(
                "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
            ).split(":")
            if item
        )
        data_dirs.append(Path("/var/lib/snapd/desktop"))
        return [directory / "applications" for directory in data_dirs]

    def discover_installed_programs(self) -> bool:
        """Add every application represented by an installed desktop entry."""
        desktop_files: dict[str, Path] = {}
        for directory in self._desktop_application_paths():
            if not directory.is_dir():
                continue
            try:
                for path in sorted(directory.rglob("*.desktop")):
                    desktop_files.setdefault(path.stem, path)
            except OSError as exc:
                logger.warning(
                    "Could not scan desktop entries in %s: %s", directory, exc
                )

        added_any = False
        icon_index: dict[str, Path] | None = None
        for desktop_id, desktop_file in desktop_files.items():
            identifiers = [desktop_id, PROGRAM_NAME_CORRECTIONS.get(desktop_id)]
            startup_class = self._parse_desktop_value(desktop_file, "StartupWMClass")
            if startup_class:
                identifiers.extend(
                    [startup_class, PROGRAM_NAME_CORRECTIONS.get(startup_class)]
                )
            missing_identifiers = [
                program
                for program in dict.fromkeys(item for item in identifiers if item)
                if program not in self.program_icon_map.programs
            ]
            if not missing_identifiers:
                continue
            if icon_index is None:
                icon_index = self._installed_icon_index()
            icon_name = self._parse_desktop_icon(desktop_file)
            icon_path = self._resolve_icon_from_index(icon_name, icon_index)
            if icon_path is None and self.use_placeholder_icon:
                icon_path = PLACEHOLDER_ICON_PATH
            for program in missing_identifiers:
                was_added, _ = self.program_icon_map.add_program(program, icon_path)
                added_any = added_any or was_added

        if added_any:
            self.program_icon_map.save()
        logger.info("Discovered %d installed applications", len(desktop_files))
        return added_any

    def _add_missing_programs(self, missing_programs: list[str]) -> bool:
        """Add missing programs to the icon map.

        If use_placeholder_icon is True, programs without icons will use a
        placeholder icon. Otherwise, they will be added with None as icon_path
        and won't get a Unicode ID.

        Args:
            missing_programs: List of program names not in the icon map.

        Returns:
            True if any programs were successfully added.
        """
        added_any = False
        for program in missing_programs:
            icon_path = self.find_icon_for_program(program)

            if icon_path is None:
                if self.use_placeholder_icon:
                    # Use placeholder icon for debugging
                    assert (
                        PLACEHOLDER_ICON_PATH.exists()
                    ), "Placeholder icon is missing!"
                    icon_path = PLACEHOLDER_ICON_PATH
                    logger.warning(
                        "Could not find icon for program: %s, using placeholder",
                        program,
                    )
                else:
                    # Don't use placeholder, program will be tracked without icon
                    logger.warning(
                        "Could not find icon for program: %s, tracking without icon",
                        program,
                    )

            was_added, _ = self.program_icon_map.add_program(program, icon_path)
            added_any = added_any or was_added

        return added_any

    def process_new_programs(self) -> bool:
        """Check for new programs and update the font if necessary.

        Scans all workspaces for applications not yet in the icon map,
        finds their icons, and rebuilds the font if new programs are found.

        Returns:
            True if any new programs were added and the font was rebuilt.
        """
        if not self._add_running_programs():
            logger.debug("No new programs detected; skipping font rebuild")
            return False
        self._publish_font_update(new_application=True)
        return True

    def _add_running_programs(self) -> bool:
        """Discover programs represented by currently open windows."""
        workspaces_info = self.get_programs_by_workspace(
            self.connection, self.compositor, self.IGNORED_PROGRAMS
        )
        programs = sorted(
            {program for workspace in workspaces_info for program in workspace.programs}
        )
        missing = self.program_icon_map.get_missing_programs(programs)
        if not missing:
            return False
        added = self._add_missing_programs(missing)
        if added:
            self.program_icon_map.save()
        return added

    def _publish_font_update(self, *, new_application: bool) -> None:
        """Build and install a font which will become active next session."""
        self.create_icon_font(
            self.program_icon_map,
            self.base_font_path,
            self.font_output_path,
            self.font_family_name,
        )
        self.install_icon_font()
        if new_application:
            self.notify(
                "WorkspaceIconDaemon: New application icon installed",
                "Log out and back in again for the new application icon to be correctly shown",
            )

    def _active_unicode_id(self, program: str) -> int | None:
        """Return a glyph guaranteed to exist in the font loaded this session."""
        active = getattr(self, "_active_program_codepoints", None)
        if active is None:  # Supports lightweight embedders constructing the object.
            return self.program_icon_map.get_unicode_id(program)
        unicode_id = active.get(program)
        if unicode_id is not None:
            return unicode_id
        if self.use_placeholder_icon and self._active_placeholder_available:
            return PLACEHOLDER_CODEPOINT
        return None

    @staticmethod
    def rename_workspace(
        connection: i3ipc.Connection, workspace_name: str, new_name: str
    ) -> None:
        """Rename a workspace using the compositor IPC command.

        Args:
            connection: The i3ipc connection object.
            workspace_name: Current name of the workspace.
            new_name: New name for the workspace.
        """
        # quotes " need to be escaped
        old_escaped = workspace_name.replace('"', '\\"')
        new_escaped = new_name.replace('"', '\\"')
        connection.command(f'rename workspace "{old_escaped}" to "{new_escaped}"')

    def update_workspace_names(self) -> None:
        """Update all workspace names with application icons.

        Iterates through all workspaces, generates icon strings from running
        applications, and updates workspace names accordingly.
        """
        if not self.workspace_icons:
            return

        workspaces_info = self.get_programs_by_workspace(
            self.connection, self.compositor, self.IGNORED_PROGRAMS
        )

        for ws_info in workspaces_info:
            icons = [
                chr(unicode_id)
                for program in ws_info.programs
                if (unicode_id := self._active_unicode_id(program)) is not None
            ]

            # Process icons based on the unique_icons_mode
            processed_icons = self._process_icons(icons)

            new_name = self._construct_workspace_name(ws_info.num, processed_icons)

            if new_name != ws_info.name:
                self.rename_workspace(self.connection, ws_info.name, new_name)

    def update_window_titles(self) -> None:
        """Apply the mapped application icon to every window title.

        The generated font contains only icon glyphs, so the font family is
        scoped to the icon span. The ordinary ``%title`` text continues to use
        Sway/i3's configured title font.
        """
        if not self.titlebar_icons:
            return

        family = html.escape(self.font_family_name, quote=True)
        visible_container_ids: set[int] = set()
        for window in self.connection.get_tree().leaves():
            program = self._get_window_name(window, self.compositor)
            unicode_id = (
                self._active_unicode_id(program) if program else None
            )
            container_id = getattr(window, "id", None)
            if unicode_id is None or not isinstance(container_id, int):
                continue
            visible_container_ids.add(container_id)
            if self._titlebar_icon_codepoints.get(container_id) == unicode_id:
                continue

            title_format = (
                f"<span font_family='{family}'>&#x{unicode_id:X};</span> %title"
            )
            # Record this before sending the command: changing title_format may
            # itself cause a window::title event on some compositor versions.
            self._titlebar_icon_codepoints[container_id] = unicode_id
            self.connection.command(
                f'[con_id={container_id}] title_format "{title_format}"'
            )

        self._titlebar_icon_codepoints = {
            container_id: unicode_id
            for container_id, unicode_id in self._titlebar_icon_codepoints.items()
            if container_id in visible_container_ids
        }

    def reset_window_titles(self) -> None:
        """Restore the default title format on windows managed by the daemon."""
        if not self.titlebar_icons:
            return

        for window in self.connection.get_tree().leaves():
            container_id = getattr(window, "id", None)
            if isinstance(container_id, int):
                self.connection.command(
                    f'[con_id={container_id}] title_format "%title"'
                )
        self._titlebar_icon_codepoints.clear()

    def _process_icons(self, icons: list[str]) -> list[str]:
        """Process icons based on the unique_icons_mode.

        Args:
            icons: List of icon characters.

        Returns:
            Processed list of icon strings (potentially with count indicators).
        """
        if self.unique_icons_mode == UniqueIconsMode.NONUNIQUE:
            # Show all icons, including duplicates
            return icons

        elif self.unique_icons_mode == UniqueIconsMode.UNIQUE:
            # Show only unique icons, preserving order
            return list(dict.fromkeys(icons))

        elif self.unique_icons_mode in (
            UniqueIconsMode.NUMBERS_SUPERSCRIPT,
            UniqueIconsMode.NUMBERS_SUBSCRIPT,
        ):
            # Count occurrences and add subscript/superscript numbers
            icon_counts = Counter(icons)
            unique_icons = list(dict.fromkeys(icons))  # Preserve order

            result = []
            for icon in unique_icons:
                count = icon_counts[icon]
                if count > 1:
                    # Add count indicator
                    count_str = self._format_count(
                        count,
                        self.unique_icons_mode == UniqueIconsMode.NUMBERS_SUPERSCRIPT,
                    )
                    result.append(f"{icon}{count_str}")
                else:
                    result.append(icon)

            return result

        return icons

    def _format_count(self, count: int, use_superscript: bool) -> str:
        """Format a count as superscript or subscript digits.

        Args:
            count: The count to format.
            use_superscript: If True, use superscript; otherwise use subscript.

        Returns:
            The formatted count string using Unicode combining characters.
        """
        digits = self.SUPERSCRIPT_DIGITS if use_superscript else self.SUBSCRIPT_DIGITS
        return "".join(digits[int(d)] for d in str(count))

    @staticmethod
    def _construct_workspace_name(num: int, icons: list[str]) -> str:
        """Construct a workspace name from number and icons.

        Args:
            num: The workspace number.
            icons: List of icon strings.

        Returns:
            The formatted workspace name ("NUM: ICONS" or just "NUM").
        """
        return f"{num}: {''.join(icons)}" if icons else str(num)

    def on_window_event(
        self, _connection: i3ipc.Connection, event: i3ipc.Event
    ) -> None:
        """Handle compositor window events.

        Args:
            _connection: The connection (unused, required by i3ipc API).
            event: The window event.
        """
        if event.change in {"new", "close", "move", "title"}:
            self.process_new_programs()
            self.update_workspace_names()
            self.update_window_titles()

    def on_exit(self) -> None:
        """Clean up workspace names and exit gracefully."""
        logger.debug("Cleaning up workspace names...")

        self.reset_desktop_state()
        self.connection.main_quit()
        sys.exit(0)

    def reset_desktop_state(self) -> None:
        """Restore workspace names and title formats without stopping IPC."""

        self.reset_window_titles()

        if self.workspace_icons:
            for workspace in self.connection.get_tree().workspaces():
                new_name = self._construct_workspace_name(workspace.num, [])
                if new_name != workspace.name:
                    self.rename_workspace(self.connection, workspace.name, new_name)

    def run(self) -> None:
        """Run the daemon main loop."""
        logger.info("Starting workspace icon daemon...")

        if not self.ensure_startup_font():
            return

        self.update_workspace_names()
        self.update_window_titles()

        for event in (
            "window::new",
            "window::close",
            "window::move",
            "window::title",
            "workspace::move",
        ):
            self.connection.on(event, self.on_window_event)

        logger.info("Daemon is running. Press Ctrl+C to exit.")
        self.connection.main()

    def ensure_startup_font(self) -> bool:
        """Prepare the next-session font and report whether monitoring may start.

        The installed font is snapshotted before discovery.  That immutable
        snapshot is the only mapping used for the lifetime of this process.
        """
        destination = self.font_installer.fonts_dir / self.font_output_path.name
        active_font_available = self._snapshot_active_font(destination)
        map_was_repaired = self.program_icon_map.modified_at_load
        installed_added = self.discover_installed_programs()
        running_added = self._add_running_programs()

        expected = {
            entry.unicode_id
            for entry in self.program_icon_map.programs.values()
            if entry.icon_path is not None
        }
        installed_is_outdated = expected != set(
            self._active_program_codepoints.values()
        )

        if not active_font_available:
            logger.info("No usable preinstalled icon font; creating one for next login")
            self._publish_font_update(new_application=False)
            self.notify(
                "WorkspaceIconDaemon: Icon font installed",
                "Log out and back in again to show application icons"
            )
            return False

        if (
            installed_added
            or running_added
            or map_was_repaired
            or installed_is_outdated
        ):
            self._publish_font_update(new_application=True)

        self.program_icon_map.modified_at_load = False
        return True

    def _snapshot_active_font(self, installed_font: Path) -> bool:
        """Capture mappings which are actually present in the installed font."""
        self._active_program_codepoints.clear()
        self._active_placeholder_available = False
        if not installed_font.is_file():
            return False
        try:
            with TTFont(installed_font) as font:
                cmap = font.getBestCmap() or {}
                if font["name"].getDebugName(1) != self.font_family_name:
                    return False
                font["CBLC"]
                bitmaps = font["CBDT"].strikeData[0]
                if (
                    PLACEHOLDER_CODEPOINT not in cmap
                    or cmap[PLACEHOLDER_CODEPOINT] not in bitmaps
                ):
                    return False
                self._active_placeholder_available = True
                for program, entry in self.program_icon_map.programs.items():
                    if (
                        entry.icon_path is not None
                        and entry.unicode_id in cmap
                        and cmap[entry.unicode_id] in bitmaps
                    ):
                        self._active_program_codepoints[program] = entry.unicode_id
                return True
        except Exception as exc:
            logger.warning("Cannot use installed icon font: %s", exc)
            return False


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "workspace icon daemon for i3 and Sway - dynamically create icon fonts "
            "and update workspace names"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--compositor",
        choices=[value.value for value in Compositor],
        default=Compositor.AUTO.value,
        help="Compositor/window manager to use (default: detect from IPC)",
    )
    parser.add_argument(
        "--program-icon-map",
        type=Path,
        default=DEFAULT_PROGRAM_ICON_MAP_PATH,
        help=(
            "Path to the program icon map YAML file "
            f"(default: {DEFAULT_PROGRAM_ICON_MAP_PATH})"
        ),
    )
    parser.add_argument(
        "--base-font",
        type=Path,
        default=DEFAULT_BASE_FONT_PATH,
        help=f"Path to the base font file (default: {DEFAULT_BASE_FONT_PATH})",
    )
    parser.add_argument(
        "--font-output",
        type=Path,
        default=DEFAULT_FONT_OUTPUT_PATH,
        help=(
            "Path where the custom font is saved "
            f"(default: {DEFAULT_FONT_OUTPUT_PATH})"
        ),
    )
    parser.add_argument(
        "--font-family-name",
        type=str,
        default=DEFAULT_FONT_FAMILY_NAME,
        help=(
            "Name of the custom font family " f"(default: {DEFAULT_FONT_FAMILY_NAME})"
        ),
    )
    parser.add_argument(
        "--unique-icons",
        type=str,
        choices=["nonunique", "numbers_superscript", "numbers_subscript", "unique"],
        default="numbers_subscript",
        help=(
            "Mode for displaying workspace icons. "
            "nonunique: show all icons including duplicates; "
            "numbers_superscript: show one icon per program with count in superscript; "
            "numbers_subscript: show one icon per program with count in "
            "subscript (default); "
            "unique: show only unique icons without count"
        ),
    )
    parser.add_argument(
        "--no-placeholder-icon",
        action="store_true",
        help=(
            "Don't use a placeholder icon for programs where no icon is found. "
            "Programs without icons will be tracked but won't get a Unicode ID "
            "or appear in the workspace names. By default, placeholder icons are used."
        ),
    )
    parser.add_argument(
        "--workspace-icons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Add generated application icons to workspace names "
            "(default: enabled)."
        ),
    )
    parser.add_argument(
        "--titlebar-icons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Add generated application icons to window titlebars. Pango markup "
            "must be enabled for the compositor title font (default: enabled)."
        ),
    )
    reset_group = parser.add_mutually_exclusive_group()
    reset_group.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Stop a running daemon, restore default workspace/titlebar names, "
            "remove generated state and exit."
        ),
    )
    reset_group.add_argument(
        "--reset-and-rebuild",
        action="store_true",
        help=(
            "Perform --reset, then discover installed applications and install "
            "their font for the next login."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args(argv)


def _process_start_time(pid: int) -> str | None:
    """Read Linux's stable process start-time field for PID-reuse protection."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return fields[21]
    except (OSError, IndexError):
        return None


def write_pid_file(path: Path) -> None:
    """Publish this daemon's PID and kernel start time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    start_time = _process_start_time(os.getpid())
    path.write_text(f"{os.getpid()} {start_time or ''}\n", encoding="utf-8")


def remove_own_pid_file(path: Path) -> None:
    """Remove the PID file only if it still identifies this process."""
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
        if pid == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, IndexError):
        pass


def stop_running_daemon(path: Path) -> bool:
    """Ask the daemon recorded in *path* to terminate gracefully."""
    try:
        parts = path.read_text(encoding="utf-8").split()
        pid, recorded_start = int(parts[0]), parts[1]
    except (OSError, ValueError, IndexError):
        path.unlink(missing_ok=True)
        return False

    if pid == os.getpid() or _process_start_time(pid) != recorded_start:
        path.unlink(missing_ok=True)
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return False
    logger.info("Asked running daemon (PID %d) to exit", pid)
    for _ in range(20):
        if not path.exists() or _process_start_time(pid) is None:
            break
        time.sleep(0.1)
    return True


def remove_generated_state(
    program_icon_map: Path, font_output: Path, installed_font: Path, pid_path: Path
) -> None:
    """Remove all generated daemon state."""
    for path in (program_icon_map, font_output, installed_font, pid_path):
        if path.exists():
            path.unlink()
            logger.info("Removed %s", path)


def main() -> None:
    """Main entry point for the daemon."""
    args = parse_arguments()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    connection = i3ipc.Connection()
    requested_compositor = Compositor(args.compositor)
    compositor = (
        detect_compositor(connection)
        if requested_compositor is Compositor.AUTO
        else requested_compositor
    )
    logger.info("Using %s", compositor.value)

    daemon = WorkspaceIconDaemon(
        connection=connection,
        compositor=compositor,
        font_installer=FontInstaller(fonts_dir=get_xdg_data_home() / "fonts"),
        program_icon_map_path=args.program_icon_map,
        base_font_path=args.base_font,
        font_output_path=args.font_output,
        font_family_name=args.font_family_name,
        unique_icons_mode=UniqueIconsMode(args.unique_icons),
        use_placeholder_icon=not args.no_placeholder_icon,
        workspace_icons=args.workspace_icons,
        titlebar_icons=args.titlebar_icons,
    )

    pid_path = args.font_output.parent / DEFAULT_PID_PATH.name
    installed_font = daemon.font_installer.fonts_dir / args.font_output.name

    if args.reset or args.reset_and_rebuild:
        stop_running_daemon(pid_path)
        # Do this in the reset process too: it covers a stale/missing PID file
        # and makes the operation idempotent.
        daemon.workspace_icons = True
        daemon.titlebar_icons = True
        daemon.reset_desktop_state()
        remove_generated_state(
            args.program_icon_map, args.font_output, installed_font, pid_path
        )
        subprocess.run(
            ["fc-cache", "-f", str(daemon.font_installer.fonts_dir)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if args.reset_and_rebuild:
            daemon.program_icon_map = ProgramIconMap(args.program_icon_map)
            daemon.discover_installed_programs()
            daemon._add_running_programs()
            daemon._publish_font_update(new_application=False)
            daemon.notify(
                "WorkspaceIconDaemon: Icon font rebuilt",
                "Log out and back in again to show application icons",
            )
        return

    def signal_handler(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %d, exiting gracefully...", signum)
        daemon.on_exit()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal_handler)

    # ``exec_always`` starts this command again whenever the compositor config
    # is reloaded.  Reuse the reset path's PID/start-time-aware shutdown so the
    # replacement does not leave multiple event loops managing the same
    # workspaces and title formats.
    stop_running_daemon(pid_path)
    write_pid_file(pid_path)
    try:
        daemon.run()
    finally:
        remove_own_pid_file(pid_path)


if __name__ == "__main__":
    main()
