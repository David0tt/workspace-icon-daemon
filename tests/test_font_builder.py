from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from workspace_icon_daemon.daemon import DEFAULT_BASE_FONT_PATH, PLACEHOLDER_ICON_PATH
from workspace_icon_daemon.font_builder import FontBuilder


class FontBuilderTests(TestCase):
    def test_default_allocation_remains_sequential(self) -> None:
        with TemporaryDirectory() as directory:
            builder = FontBuilder(
                DEFAULT_BASE_FONT_PATH,
                Path(directory) / "font.ttf",
                [PLACEHOLDER_ICON_PATH, PLACEHOLDER_ICON_PATH],
                remove_original_symbols=True,
            ).build_complete_font()
            try:
                self.assertEqual(builder.assigned_codepoints, [0xE000, 0xE001])
            finally:
                builder.ttfont.close()

    def test_rejects_invalid_explicit_codepoints(self) -> None:
        for codepoints in ([], [0xE000, 0xE001], [-1], [0xF900], ["0xE000"]):
            with self.subTest(codepoints=codepoints), self.assertRaises(ValueError):
                FontBuilder(
                    DEFAULT_BASE_FONT_PATH, Path("unused.ttf"),
                    [PLACEHOLDER_ICON_PATH], codepoints=codepoints,
                )
        with self.assertRaises(ValueError):
            FontBuilder(
                DEFAULT_BASE_FONT_PATH, Path("unused.ttf"),
                [PLACEHOLDER_ICON_PATH, PLACEHOLDER_ICON_PATH],
                codepoints=[0xE000, 0xE000],
            )

    def test_invalid_svg_can_be_replaced_without_losing_its_codepoint(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "entities.svg"
            invalid.write_text(
                '<!DOCTYPE svg [<!ENTITY ns_extend "x">]>'
                '<svg xmlns="http://www.w3.org/2000/svg">&ns_extend;</svg>',
                encoding="utf-8",
            )
            builder = FontBuilder(
                DEFAULT_BASE_FONT_PATH,
                root / "font.ttf",
                [invalid],
                remove_original_symbols=True,
                codepoints=[0xE001],
                fallback_image_path=PLACEHOLDER_ICON_PATH,
            ).build_complete_font()
            try:
                self.assertEqual(builder.assigned_codepoints, [0xE001])
            finally:
                builder.ttfont.close()
