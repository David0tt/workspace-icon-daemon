import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from fontTools.ttLib import TTFont
from PIL import Image

from workspace_icon_daemon.daemon import (
    DEFAULT_BASE_FONT_PATH, ProgramIconEntry, ProgramIconMap, WorkspaceIconDaemon,
)
from workspace_icon_daemon.platform import Bar, Compositor, FontInstaller


class StartupTests(TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.icon = self.root / "app.png"
        Image.new("RGBA", (32, 32), "blue").save(self.icon)
        mapping = ProgramIconMap(self.root / "map.yaml")
        mapping.add_program("app", self.icon)
        mapping.save()
        self.connection = Mock()
        self.connection.get_tree.return_value.workspaces.return_value = []
        self.daemon = WorkspaceIconDaemon(
            self.connection, Compositor.SWAY,
            FontInstaller(Bar.NONE, self.root / "fonts"),
            program_icon_map_path=mapping.filepath,
            font_output_path=self.root / "cached.ttf",
        )
        self.daemon.create_icon_font(
            mapping, DEFAULT_BASE_FONT_PATH, self.daemon.font_output_path,
            self.daemon.font_family_name,
        )
        self.destination = self.root / "fonts" / "cached.ttf"
        self.destination.parent.mkdir()
        shutil.copy2(self.daemon.font_output_path, self.destination)

    def recover(self, rebuild: bool, install: bool = True) -> None:
        with patch.object(
            self.daemon, "create_icon_font", wraps=self.daemon.create_icon_font,
        ) as build, patch("workspace_icon_daemon.platform.subprocess.run") as run:
            self.daemon.run()
        self.assertEqual(build.call_count, int(rebuild))
        self.assertEqual(run.call_count, int(install))
        self.assertEqual(self.destination.read_bytes(), self.daemon.font_output_path.read_bytes())
        self.connection.main.assert_called_once()

    def test_unchanged_startup_does_no_font_work(self) -> None:
        self.recover(False, False)

    def test_missing_cache_is_rebuilt(self) -> None:
        self.daemon.font_output_path.unlink()
        self.recover(True)

    def test_missing_install_is_restored_without_rebuild(self) -> None:
        self.destination.unlink()
        self.recover(False)

    def test_different_installed_bytes_are_replaced(self) -> None:
        self.destination.write_bytes(b"stale")
        self.recover(False)

    def test_corrupt_cache_is_rebuilt(self) -> None:
        self.daemon.font_output_path.write_bytes(b"broken font")
        self.recover(True)

    def test_changed_inputs_trigger_rebuild(self) -> None:
        for source in (self.icon, self.daemon.program_icon_map.filepath):
            with self.subTest(source=source):
                timestamp = self.daemon.font_output_path.stat().st_mtime_ns + 1
                os.utime(source, ns=(timestamp, timestamp))
                self.connection.main.reset_mock()
                self.recover(True)

    def test_changed_family_triggers_rebuild(self) -> None:
        self.daemon.font_family_name = "DifferentFamily"
        self.recover(True)
        with TTFont(self.destination) as font:
            self.assertEqual(font["name"].getDebugName(1), "DifferentFamily")

    def test_mapping_mismatch_is_detected_without_newer_timestamp(self) -> None:
        mapping = self.daemon.program_icon_map
        mapping.programs["app"] = ProgramIconEntry(self.icon, 0xE005)
        mapping.save()
        timestamp = self.daemon.font_output_path.stat().st_mtime_ns
        os.utime(mapping.filepath, ns=(timestamp, timestamp))
        self.recover(True)
        with TTFont(self.destination) as font:
            self.assertIn(0xE005, font.getBestCmap())
            self.assertNotIn(0xE000, font.getBestCmap())

    def test_new_program_is_built_only_once(self) -> None:
        workspace = Mock()
        workspace.num = 1
        workspace.name = "1"
        window = Mock()
        window.app_id = "new-app"
        window.rect.x = window.rect.y = 0
        workspace.leaves.return_value = [window]
        self.connection.get_tree.return_value.workspaces.return_value = [workspace]
        with patch.object(self.daemon, "find_icon_for_program", return_value=self.icon):
            self.recover(True)

    def test_map_cleanup_rebuilds_before_install(self) -> None:
        removed = self.root / "removed.png"
        shutil.copy2(self.icon, removed)
        mapping = self.daemon.program_icon_map
        mapping.add_program("removed", removed)
        mapping.save()
        self.daemon.create_icon_font(
            mapping, DEFAULT_BASE_FONT_PATH, self.daemon.font_output_path,
            self.daemon.font_family_name,
        )
        removed.unlink()
        self.daemon.program_icon_map = ProgramIconMap(mapping.filepath)
        self.recover(True)
        with TTFont(self.destination) as font:
            self.assertNotIn(0xE001, font.getBestCmap())

    def test_empty_map_needs_no_font(self) -> None:
        self.daemon.program_icon_map.programs.clear()
        self.daemon.program_icon_map.save()
        self.daemon.font_output_path.unlink()
        with patch.object(self.daemon, "install_icon_font") as install:
            self.daemon.run()
        install.assert_not_called()
        self.assertFalse(self.daemon.font_output_path.exists())
