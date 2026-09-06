import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PIL import Image

from workspace_icon_daemon.daemon import (
    DEFAULT_BASE_FONT_PATH,
    PLACEHOLDER_CODEPOINT,
    ProgramIconMap,
    WorkspaceIconDaemon,
)
from workspace_icon_daemon.platform import Compositor, FontInstaller


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
        self.connection.get_tree.return_value.leaves.return_value = []
        self.daemon = WorkspaceIconDaemon(
            self.connection,
            Compositor.SWAY,
            FontInstaller(self.root / "fonts"),
            program_icon_map_path=mapping.filepath,
            font_output_path=self.root / "cached.ttf",
        )
        self.daemon.create_icon_font(
            mapping,
            DEFAULT_BASE_FONT_PATH,
            self.daemon.font_output_path,
            self.daemon.font_family_name,
        )
        self.destination = self.root / "fonts" / "cached.ttf"
        self.destination.parent.mkdir()
        shutil.copy2(self.daemon.font_output_path, self.destination)

    def test_second_startup_uses_preinstalled_font_without_rebuilding(self) -> None:
        with patch.object(
            self.daemon, "discover_installed_programs", return_value=False
        ), patch.object(self.daemon, "create_icon_font") as build, patch.object(
            self.daemon, "install_icon_font"
        ) as install:
            self.daemon.run()

        build.assert_not_called()
        install.assert_not_called()
        self.connection.main.assert_called_once()
        self.assertEqual(self.daemon._active_unicode_id("app"), 0xE001)

    def test_first_startup_builds_notifies_and_exits_without_renaming(self) -> None:
        self.destination.unlink()
        with patch.object(
            self.daemon, "discover_installed_programs", return_value=False
        ), patch.object(self.daemon, "install_icon_font") as install, patch.object(
            self.daemon, "notify"
        ) as notify:
            self.daemon.run()

        install.assert_called_once()
        notify.assert_called_once()
        self.connection.main.assert_not_called()
        self.connection.command.assert_not_called()

    def test_new_program_is_installed_but_uses_loaded_placeholder(self) -> None:
        with patch.object(
            self.daemon, "discover_installed_programs", return_value=False
        ):
            self.assertTrue(self.daemon.ensure_startup_font())

        workspace = Mock(num=1, name="1")
        window = SimpleNamespace(
            id=42,
            app_id="new-app",
            window_class=None,
            rect=SimpleNamespace(x=0, y=0),
        )
        workspace.leaves.return_value = [window]
        self.connection.get_tree.return_value.workspaces.return_value = [workspace]
        self.connection.get_tree.return_value.leaves.return_value = [window]

        with patch.object(
            self.daemon, "find_icon_for_program", return_value=self.icon
        ), patch.object(self.daemon, "install_icon_font") as install, patch.object(
            self.daemon, "notify"
        ) as notify:
            self.daemon.on_window_event(
                self.connection, SimpleNamespace(change="new")
            )

        install.assert_called_once()
        notify.assert_called_once()
        self.assertEqual(
            self.daemon._active_unicode_id("new-app"), PLACEHOLDER_CODEPOINT
        )
        self.assertNotEqual(
            self.daemon.program_icon_map.get_unicode_id("new-app"),
            PLACEHOLDER_CODEPOINT,
        )

    def test_installed_desktop_entries_and_startup_class_are_prebuilt(self) -> None:
        applications = self.root / "applications"
        applications.mkdir()
        desktop = applications / "org.example.App.desktop"
        desktop.write_text(
            "[Desktop Entry]\nIcon=example\nStartupWMClass=ExampleClass\n",
            encoding="utf-8",
        )
        empty_map = ProgramIconMap(self.root / "desktop-map.yaml")
        self.daemon.program_icon_map = empty_map

        with patch.object(
            self.daemon, "_desktop_application_paths", return_value=[applications]
        ), patch.object(
            self.daemon, "_installed_icon_index", return_value={"example": self.icon}
        ):
            self.assertTrue(self.daemon.discover_installed_programs())

        self.assertIsNotNone(empty_map.get_unicode_id("org.example.App"))
        self.assertIsNotNone(empty_map.get_unicode_id("ExampleClass"))

    def test_bulk_index_preserves_old_application_icon_precedence(self) -> None:
        preferred_svg = self.root / "hicolor" / "scalable" / "apps"
        preferred_png = self.root / "hicolor" / "128x128" / "apps"
        fallback = self.root / "char-white" / "apps" / "16"
        for directory in (preferred_svg, preferred_png, fallback):
            directory.mkdir(parents=True)

        color_png = preferred_png / "example.png"
        color_png.write_bytes(b"color")
        monochrome_svg = fallback / "example.svg"
        monochrome_svg.write_text("<svg/>", encoding="utf-8")

        def preferred(extension: str) -> list[Path]:
            return [preferred_svg] if extension == "svg" else [preferred_png]

        with patch.object(
            WorkspaceIconDaemon,
            "_preferred_icon_search_paths",
            side_effect=preferred,
        ), patch.object(
            WorkspaceIconDaemon, "_icon_search_roots", return_value=[fallback]
        ):
            icon_index = self.daemon._installed_icon_index()

        self.assertEqual(icon_index["example"], color_png)

    def test_png_precedence_prefers_128_over_larger_and_smaller_icons(self) -> None:
        directories = {
            size: self.root / "hicolor" / f"{size}x{size}" / "apps"
            for size in (16, 128, 512)
        }
        for size, directory in directories.items():
            directory.mkdir(parents=True)
            (directory / "example.png").write_bytes(str(size).encode())

        ordered = [directories[size] for size in (128, 512, 16)]

        def preferred(extension: str) -> list[Path]:
            return [] if extension == "svg" else ordered

        with patch.object(
            WorkspaceIconDaemon,
            "_preferred_icon_search_paths",
            side_effect=preferred,
        ), patch.object(WorkspaceIconDaemon, "_icon_search_roots", return_value=[]):
            icon_index = self.daemon._installed_icon_index()

        self.assertEqual(icon_index["example"], directories[128] / "example.png")
