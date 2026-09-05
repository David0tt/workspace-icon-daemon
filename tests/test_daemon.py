from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from fontTools.ttLib import TTFont
from PIL import Image

from workspace_icon_daemon.daemon import (
    DEFAULT_BASE_FONT_PATH,
    ProgramIconMap,
    UniqueIconsMode,
    WorkspaceIconDaemon,
    parse_arguments,
)
from workspace_icon_daemon.font_builder import FontBuilder
from workspace_icon_daemon.platform import Compositor


class FakeWorkspace:
    num = 2
    name = "2"

    def leaves(self) -> list[object]:
        return [
            SimpleNamespace(
                app_id="foot",
                window_class=None,
                rect=SimpleNamespace(x=500, y=0),
            ),
            SimpleNamespace(
                app_id=None,
                window_class="Firefox",
                rect=SimpleNamespace(x=0, y=0),
            ),
        ]


class FakeConnection:
    def get_tree(self) -> object:
        return SimpleNamespace(workspaces=lambda: [FakeWorkspace()])


class DaemonTests(TestCase):
    def test_font_rebuild_preserves_codepoints_after_icon_removal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            icon_map = ProgramIconMap(root / "programs.yaml")
            for name, color in [("removed", "red"), ("retained", "blue")]:
                path = root / f"{name}.png"
                Image.new("RGBA", (32, 32), color).save(path)
                icon_map.add_program(name, path)
            icon_map.add_program("iconless", None)
            icon_map.save()
            (root / "removed.png").unlink()

            restored = ProgramIconMap(icon_map.filepath)
            self.assertTrue(restored.modified_at_load)
            self.assertEqual(restored.get_unicode_id("retained"), 0xE001)
            # Shared icon paths must still receive their own assigned glyphs.
            restored.add_program("another", root / "retained.png")
            output = root / "font.ttf"
            WorkspaceIconDaemon.create_icon_font(
                restored, DEFAULT_BASE_FONT_PATH, output, "TestIcons"
            )

            with TTFont(output) as font:
                cmap = font.getBestCmap()
                self.assertEqual(
                    {cp for cp in cmap if 0xE000 <= cp <= 0xF8FF},
                    {0xE001, 0xE002},
                )
                target_px = font["CBLC"].strikes[0].bitmapSizeTable.ppemY
                expected = FontBuilder.collect_image(root / "retained.png", target_px)
                for name in ("retained", "another"):
                    glyph = cmap[restored.get_unicode_id(name)]
                    self.assertEqual(font["CBDT"].strikeData[0][glyph].imageData, expected)

    def test_program_icon_map_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            icon = root / "app.svg"
            icon.write_text("<svg/>", encoding="utf-8")
            map_path = root / "programs.yaml"

            icon_map = ProgramIconMap(map_path)
            added, codepoint = icon_map.add_program("example", icon)
            icon_map.save()

            self.assertTrue(added)
            self.assertEqual(codepoint, 0xE000)
            restored = ProgramIconMap(map_path)
            self.assertEqual(restored.get_icon_path("example"), icon)
            self.assertEqual(restored.get_unicode_id("example"), 0xE000)

    def test_sway_collects_native_and_xwayland_windows_in_layout_order(self) -> None:
        workspaces = WorkspaceIconDaemon.get_programs_by_workspace(
            FakeConnection(), Compositor.SWAY, set()
        )
        self.assertEqual(workspaces[0].programs, ["Firefox", "foot"])

    def test_icon_count_modes_and_workspace_name(self) -> None:
        daemon = object.__new__(WorkspaceIconDaemon)
        daemon.unique_icons_mode = UniqueIconsMode.NUMBERS_SUBSCRIPT
        self.assertEqual(daemon._process_icons(["a", "b", "a"]), ["a₂", "b"])
        self.assertEqual(daemon._construct_workspace_name(2, ["a₂", "b"]), "2: a₂b")

    def test_new_cli_identity_and_platform_options(self) -> None:
        args = parse_arguments(["--compositor", "sway", "--bar", "none"])
        self.assertEqual(args.compositor, "sway")
        self.assertEqual(args.bar, "none")
