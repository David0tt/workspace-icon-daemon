from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from workspace_icon_daemon.platform import (
    Bar,
    Compositor,
    FontInstaller,
    detect_compositor,
    program_name,
    resolve_bar,
)


class FakeConnection:
    def __init__(self, human_readable: str, config: str = "") -> None:
        self.version = SimpleNamespace(
            human_readable=human_readable,
            loaded_config_file_name=config,
        )

    def get_version(self) -> object:
        return self.version


class PlatformTests(TestCase):
    def test_detect_compositor(self) -> None:
        cases = [
            ("sway version 1.11", "", Compositor.SWAY),
            ("4.24", "/home/user/.config/sway/config", Compositor.SWAY),
            ("4.24", "/home/user/.config/i3/config", Compositor.I3),
        ]
        for version, config, expected in cases:
            with self.subTest(version=version, config=config):
                self.assertIs(
                    detect_compositor(FakeConnection(version, config)), expected
                )

    def test_sway_prefers_app_id_and_supports_xwayland(self) -> None:
        native = SimpleNamespace(app_id="foot", window_class=None)
        xwayland = SimpleNamespace(app_id=None, window_class="Firefox")
        self.assertEqual(program_name(native, Compositor.SWAY), "foot")
        self.assertEqual(program_name(xwayland, Compositor.SWAY), "Firefox")

    def test_i3_uses_window_class(self) -> None:
        window = SimpleNamespace(app_id="ignored", window_class="Alacritty")
        self.assertEqual(program_name(window, Compositor.I3), "Alacritty")

    def test_auto_bar_follows_compositor(self) -> None:
        self.assertIs(resolve_bar(Bar.AUTO, Compositor.I3), Bar.I3BAR)
        self.assertIs(resolve_bar(Bar.AUTO, Compositor.SWAY), Bar.WAYBAR)
        self.assertIs(resolve_bar(Bar.NONE, Compositor.SWAY), Bar.NONE)

    @patch("workspace_icon_daemon.platform.subprocess.Popen")
    @patch("workspace_icon_daemon.platform.subprocess.run")
    def test_font_install_restarts_bar_before_and_after_cache_refresh(
        self, run, popen
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "generated.ttf"
            source.write_bytes(b"font")
            fonts_dir = root / "fonts"

            destination = FontInstaller(Bar.WAYBAR, fonts_dir).install(source)

        self.assertEqual(destination, fonts_dir / source.name)
        self.assertEqual(
            [invocation.args[0] for invocation in run.call_args_list],
            [
                ["pkill", "waybar"],
                ["fc-cache", "-f", str(fonts_dir)],
                ["pkill", "waybar"],
            ],
        )
        self.assertEqual(
            [invocation.args[0] for invocation in popen.call_args_list],
            [["waybar"], ["waybar"]],
        )

    @patch("workspace_icon_daemon.platform.subprocess.run")
    def test_font_install_replaces_existing_file_atomically(self, run) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "generated.ttf"
            source.write_bytes(b"new font")
            fonts_dir = root / "fonts"
            fonts_dir.mkdir()
            destination = fonts_dir / source.name
            destination.write_bytes(b"old font")
            old_inode = destination.stat().st_ino

            FontInstaller(Bar.NONE, fonts_dir).install(source)

            self.assertEqual(destination.read_bytes(), b"new font")
            self.assertNotEqual(destination.stat().st_ino, old_inode)
