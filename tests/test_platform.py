from types import SimpleNamespace
from unittest import TestCase

from workspace_icon_daemon.platform import (
    Bar,
    Compositor,
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
