#!/usr/bin/env python3
"""Create PUA icon fonts by extending CBDT/CBLC bitmap color fonts.

This module provides functionality to build custom icon fonts by adding
images (PNG/SVG) to the Private Use Area of a base CBDT/CBLC font like
NotoColorEmoji.ttf.
"""
from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import cairosvg
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.C_B_D_T_ import (
    SmallGlyphMetrics,
    cbdt_bitmap_format_17,
)
from PIL import Image

PUA_START: int = 0xE000

logger = logging.getLogger(__name__)


class FontBuilder:
    """Builder class for creating PUA icon fonts from images.

    This class encapsulates all functionality needed to build a CBDT/CBLC
    bitmap color font by extending a base font (like NotoColorEmoji.ttf)
    with custom icons mapped to the Private Use Area.
    """

    def __init__(
        self,
        base_font_path: Path,
        output_font_path: Path,
        image_paths: list[Path],
        font_family_name: str = "MyCreatedIconFont",
        pua_start: int = PUA_START,
        remove_original_symbols: bool = False,
        codepoints: list[int] | None = None,
    ) -> None:
        """Initialize the FontBuilder.

        Args:
            base_font_path: Path to the base CBDT/CBLC font.
            output_font_path: Path where the output font will be saved.
            image_paths: List of image file paths to add as glyphs.
            font_family_name: Name for the font family in metadata.
            pua_start: Starting code point in the Private Use Area.
            remove_original_symbols: If True, remove all original symbols from
                the base font, keeping only the newly added icons.
            codepoints: Explicit PUA codepoints in image_paths order. If omitted,
                allocate sequentially from pua_start.
        """
        self.base_font_path = base_font_path
        self.output_font_path = output_font_path
        self.image_paths = image_paths
        self.font_family_name = font_family_name
        self.pua_start = pua_start
        self.remove_original_symbols = remove_original_symbols
        if codepoints is not None:
            if len(codepoints) != len(image_paths):
                raise ValueError("Each image must have exactly one codepoint")
            if any(type(cp) is not int or not 0xE000 <= cp <= 0xF8FF for cp in codepoints):
                raise ValueError("Codepoints must be integers in U+E000..U+F8FF")
            if len(set(codepoints)) != len(codepoints):
                raise ValueError("Codepoints must be unique")
        self.codepoints = list(codepoints) if codepoints is not None else None
        self.ttfont: TTFont | None = None
        self.strike_index: int = 0
        self.ppem_x: int = 0
        self.ppem_y: int = 0
        self.added_count: int = 0
        self.assigned_codepoints: list[int] = []

    @staticmethod
    def clamp_int8(value: int | float) -> int:
        """Clamp a number to the int8 range [-128, 127].

        This is required for fields in SmallGlyphMetrics.

        Args:
            value: The input number to clamp.

        Returns:
            The integer value clamped to the valid int8 range.
        """
        value_int = int(round(value))
        if value_int < -128:
            return -128
        if value_int > 127:
            return 127
        return value_int

    @staticmethod
    def clamp_uint8(value: int | float) -> int:
        """Clamp a number to the uint8 range [0, 255].

        This is required for fields in SmallGlyphMetrics.

        Args:
            value: The input number to clamp.

        Returns:
            The integer value clamped to the valid uint8 range.
        """
        value_int = int(round(value))
        if value_int < 0:
            return 0
        if value_int > 255:
            return 255
        return value_int

    @staticmethod
    def png_size(data: bytes) -> tuple[int, int]:
        """Return the (width, height) of a PNG image from its bytes.

        Args:
            data: Raw PNG bytes.

        Returns:
            A tuple (width, height) in pixels.

        Raises:
            ValueError: If the data does not look like a valid PNG or is
                truncated.
        """
        signature = b"\x89PNG\r\n\x1a\n"
        index = data.find(signature)
        if index < 0:
            raise ValueError("Not a PNG")
        start = index + 8
        if len(data) < start + 24:
            raise ValueError("Truncated PNG")
        width = int.from_bytes(data[start + 8 : start + 12], "big")
        height = int.from_bytes(data[start + 12 : start + 16], "big")
        return width, height

    @staticmethod
    def collect_image(image_path: Path, target_px: int = 128) -> bytes:
        """Collect and normalize a single PNG or SVG image.

        PNGs are verified for size and rescaled when needed; SVGs are
        rasterized at target_px using CairoSVG.

        Args:
            image_path: Path to the image file.
            target_px: Target raster size in pixels (width and height).

        Returns:
            PNG image bytes at the requested size.

        Raises:
            AssertionError: If image_path is not a PNG or SVG file.
        """
        assert image_path.is_file() and image_path.suffix.lower() in {
            ".png",
            ".svg",
        }, "image_path must be a PNG or SVG file"

        if image_path.suffix.lower() == ".png":
            data = image_path.read_bytes()
            width, height = FontBuilder.png_size(data)
            if (width, height) != (target_px, target_px):
                logger.info(
                    "%s: PNG is %dx%d; rescaling to %dx%d",
                    image_path.name,
                    width,
                    height,
                    target_px,
                    target_px,
                )
                image = Image.open(BytesIO(data)).convert("RGBA")
                image = image.resize((target_px, target_px), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
            return data
        else:
            png_data = cairosvg.svg2png(
                url=str(image_path),
                output_width=target_px,
                output_height=target_px,
            )
            return png_data

    @staticmethod
    def collect_images_from_paths(
        paths: list[Path], target_px: int = 128
    ) -> list[tuple[Path, bytes]]:
        """Collect PNG and SVG images from a list of file paths.

        PNGs are verified for size and rescaled when needed; SVGs are
        rasterized at target_px using CairoSVG. Files are processed in
        the order provided.

        Args:
            paths: List of image file paths.
            target_px: Target raster size in pixels (width and height).

        Returns:
            A list of (path, image_bytes) pairs. The image bytes are PNGs
            at the requested size.
        """
        result: list[tuple[Path, bytes]] = []
        for path in paths:
            image_data = FontBuilder.collect_image(path, target_px)
            if image_data is not None:
                result.append((path, image_data))
        return result

    def load_base_font(self) -> None:
        """Load the base font and validate it has CBDT/CBLC tables."""
        if not self.base_font_path.exists():
            raise FileNotFoundError(f"Base font not found: {self.base_font_path}")

        self.ttfont = TTFont(str(self.base_font_path))

        if "CBDT" not in self.ttfont or "CBLC" not in self.ttfont:
            raise ValueError("Base font must be CBDT/CBLC (like NotoColorEmoji.ttf)")

        cblc = self.ttfont["CBLC"]
        strike = cblc.strikes[self.strike_index]
        cblc_subtable = strike.indexSubTables[-1]

        if not hasattr(cblc_subtable, "names"):
            raise ValueError("Unsupported CBLC subtable (no names list exposed)")

        bitmap_size_table = strike.bitmapSizeTable
        self.ppem_x = bitmap_size_table.ppemX
        self.ppem_y = bitmap_size_table.ppemY

        if self.ppem_x != self.ppem_y:
            raise ValueError("Non-square CBDT strike not supported")

        logger.debug("Loaded base font: %s", self.base_font_path)
        logger.debug("Strike ppemX, ppemY: %d, %d", self.ppem_x, self.ppem_y)

    def remove_original_glyphs(self) -> None:
        """Remove all original glyphs from the base font.

        This keeps only the essential glyphs needed for font structure
        (like .notdef and space) and removes all other original symbols.
        This results in a minimal icon-only font.

        Raises:
            RuntimeError: If the font has not been loaded.
        """
        if not self.ttfont:
            raise RuntimeError("Font not loaded. Call load_base_font() first.")

        logger.debug("Removing original symbols from base font...")

        # Get the current glyph order and cmap
        glyph_order = self.ttfont.getGlyphOrder()
        best_cmap = self.ttfont["cmap"].getBestCmap()

        # Essential glyphs to keep for font structure
        essential_glyphs = {".notdef", "space"}

        # Identify glyphs to remove (all except essential ones)
        glyphs_to_remove = [g for g in glyph_order if g not in essential_glyphs]

        # Remove from CBDT/CBLC tables
        cblc = self.ttfont["CBLC"]
        cbdt = self.ttfont["CBDT"]
        strike = cblc.strikes[self.strike_index]
        strike_data = cbdt.strikeData[self.strike_index]

        # Clear strike data completely, keep only essential glyphs
        glyphs_to_keep_in_strike = {}
        for glyph_name in essential_glyphs:
            if glyph_name in strike_data:
                glyphs_to_keep_in_strike[glyph_name] = strike_data[glyph_name]

        strike_data.clear()
        strike_data.update(glyphs_to_keep_in_strike)

        # Remove all index subtables except the last one (which we'll use for PUA)
        # Keep only the last subtable and clear its names, keeping only essentials
        if len(strike.indexSubTables) > 0:
            last_subtable = strike.indexSubTables[-1]
            strike.indexSubTables = [last_subtable]
            if hasattr(last_subtable, "names"):
                # Keep only essential glyphs that exist in strike data
                new_names = [n for n in essential_glyphs if n in strike_data]
                last_subtable.names.clear()
                last_subtable.names.extend(new_names)

        # Clear all cmap entries (we'll only have PUA entries later)
        for subtable in self.ttfont["cmap"].tables:
            # Keep only essential glyphs that might be referenced
            subtable.cmap = {
                cp: gn for cp, gn in subtable.cmap.items() if gn in essential_glyphs
            }

        # Update best_cmap to match
        best_cmap.clear()
        for subtable in self.ttfont["cmap"].tables:
            best_cmap.update(subtable.cmap)

        # Update glyph order to only include essential glyphs
        new_glyph_order = [g for g in glyph_order if g in essential_glyphs]
        self.ttfont.setGlyphOrder(new_glyph_order)
        self.ttfont["maxp"].numGlyphs = len(new_glyph_order)

        # Clean up hmtx table - remove metrics for removed glyphs
        hmtx = self.ttfont["hmtx"]
        new_metrics = {}
        for glyph_name in essential_glyphs:
            if glyph_name in hmtx.metrics:
                new_metrics[glyph_name] = hmtx.metrics[glyph_name]
        hmtx.metrics.clear()
        hmtx.metrics.update(new_metrics)

        # Update hhea
        self.ttfont["hhea"].numberOfHMetrics = len(new_glyph_order)

        logger.debug(
            "Removed %d original glyphs, kept %d essential glyphs",
            len(glyphs_to_remove),
            len(essential_glyphs),
        )

    def _next_pua(self) -> Iterator[int]:
        """Yield available PUA code points not present in the base cmap.

        Yields:
            Unicode code points in the Private Use Area that are not already
            mapped in the font.
        """
        best_cmap = self.ttfont["cmap"].getBestCmap()
        codepoint = self.pua_start
        while codepoint <= 0xF8FF:
            if codepoint not in best_cmap:
                yield codepoint
            codepoint += 1

    def add_images(self, images: list[tuple[Path, bytes]]) -> None:
        """Add images to the font as PUA glyphs.

        Args:
            images: List of (path, image_bytes) pairs where image_bytes
                are PNG data at the correct size.

        Raises:
            RuntimeError: If the font has not been loaded.
            ValueError: If an image has incorrect dimensions or a glyph name
                already exists.
        """
        if not self.ttfont:
            raise RuntimeError("Font not loaded. Call load_base_font() first.")

        if not images:
            logger.debug("No images to add")
            return

        cblc = self.ttfont["CBLC"]
        cbdt = self.ttfont["CBDT"]
        strike = cblc.strikes[self.strike_index]
        strike_data = cbdt.strikeData[self.strike_index]
        cblc_subtable = strike.indexSubTables[-1]

        best_cmap = self.ttfont["cmap"].getBestCmap()
        glyph_order = self.ttfont.getGlyphOrder()
        upem = self.ttfont["head"].unitsPerEm

        # Get reference advance width
        # If we removed original symbols, space will have advance=0, so we need
        # to calculate a proper advance based on the icon size
        space_advance = self.ttfont["hmtx"].metrics["space"][0]
        if space_advance == 0 or self.remove_original_symbols:
            # Calculate advance as the icon width in font units
            # ppem is pixels per em, so we scale to match the upem (units per em)
            ref_advance = int(round(upem * (self.ppem_x / self.ppem_x)))
            # This essentially gives us upem units as advance, but we can adjust
            # to match the actual icon size if needed
            ref_advance = upem
        else:
            ref_advance = space_advance

        os2_table = self.ttfont["OS/2"]
        s_typo_ascender = os2_table.sTypoAscender
        s_typo_descender = os2_table.sTypoDescender

        pua_iter = iter(self.codepoints) if self.codepoints is not None else self._next_pua()
        if self.codepoints is not None:
            if len(self.codepoints) != len(images):
                raise ValueError("Each image must have exactly one codepoint")
            if any(cp in best_cmap for cp in self.codepoints):
                raise ValueError("Requested codepoint is already mapped in the font")

        for path, data in images:
            actual_size = self.png_size(data)
            if actual_size != (self.ppem_y, self.ppem_y):
                raise ValueError(
                    f"Image {path.name} is {actual_size[0]}x{actual_size[1]} "
                    f"but expected {self.ppem_y}x{self.ppem_y}"
                )

            codepoint = next(pua_iter)

            if codepoint <= 0xFFFF:
                glyph_name = "uni%04X" % codepoint
            else:
                glyph_name = "u%04X" % codepoint

            if glyph_name in glyph_order:
                raise ValueError(
                    f"{glyph_name} already in glyph order. " f"This should not happen!"
                )

            glyph_order.append(glyph_name)
            self.ttfont.setGlyphOrder(glyph_order)
            self.ttfont["maxp"].numGlyphs = len(glyph_order)

            for subtable in self.ttfont["cmap"].tables:
                subtable.cmap[codepoint] = glyph_name
            best_cmap[codepoint] = glyph_name

            self.ttfont["hmtx"].metrics[glyph_name] = (int(ref_advance), 0)
            self.ttfont["hhea"].numberOfHMetrics = len(glyph_order)

            bitmap = cbdt_bitmap_format_17(b"", self.ttfont)
            metrics = SmallGlyphMetrics()
            metrics.width = int(self.ppem_y)
            metrics.height = int(self.ppem_y)

            advance_pixels = max(
                1, int(round(ref_advance * (self.ppem_x / float(upem))))
            )
            bearing_x = max(0, int(round((advance_pixels - metrics.width) / 2)))

            ascender_pixels = int(round(s_typo_ascender * (self.ppem_y / float(upem))))
            descender_pixels = int(
                round(abs(s_typo_descender) * (self.ppem_y / float(upem)))
            )
            line_center_from_baseline = (ascender_pixels - descender_pixels) / 2.0
            bearing_y = line_center_from_baseline + (metrics.height / 2.0)

            metrics.BearingX = self.clamp_int8(bearing_x)
            metrics.BearingY = self.clamp_int8(bearing_y)
            metrics.Advance = self.clamp_uint8(advance_pixels)
            bitmap.metrics = metrics
            bitmap.imageData = data

            strike_data[glyph_name] = bitmap
            cblc_subtable.names.append(glyph_name)
            self.added_count += 1
            self.assigned_codepoints.append(codepoint)
            logger.info("[+] %s -> U+%04X", path.name, codepoint)

    def update_name_table(self, family_name: str = "MyCreatedIconFont") -> None:
        """Update the font's name table with custom metadata.

        Args:
            family_name: The font family name to use.

        Raises:
            RuntimeError: If the font has not been loaded.
        """
        if not self.ttfont:
            raise RuntimeError("Font not loaded. Call load_base_font() first.")

        name_table = self.ttfont["name"]
        name_table.names = [
            record
            for record in list(name_table.names)
            if getattr(record, "platformID", None) == 0
        ]

        subfamily = "Regular"
        full_name = f"{family_name} {subfamily}"
        postscript_name = f"{family_name}-{subfamily}"
        sample_text = (
            " ".join(chr(cp) for cp in self.assigned_codepoints[:64])
            or "Private Use Area"
        )
        unique_id = f"{family_name} {subfamily}; {os.getpid()}"

        for name_id, name_string in [
            (1, family_name),
            (2, subfamily),
            (3, unique_id),
            (4, full_name),
            (6, postscript_name),
            (16, family_name),
            (17, subfamily),
            (19, sample_text),
        ]:
            name_table.setName(name_string, name_id, 0, 4, 0)

        logger.debug("Updated name table with family: %s", family_name)

    def build_complete_font(self) -> FontBuilder:
        """Build the complete font.

        This method loads the base font, optionally removes original symbols,
        adds images as glyphs, and updates the font metadata.

        Returns:
            The FontBuilder instance for method chaining.
        """
        self.load_base_font()

        if self.remove_original_symbols:
            self.remove_original_glyphs()

        images = FontBuilder.collect_images_from_paths(
            self.image_paths, target_px=int(self.ppem_y)
        )

        if not images:
            raise ValueError("No valid images found")

        self.add_images(images)
        self.update_name_table(family_name=self.font_family_name)

        return self

    def save(self) -> None:
        """Save the modified font to the output path.

        Raises:
            RuntimeError: If the font has not been loaded.
        """
        if not self.ttfont:
            raise RuntimeError("Font not loaded. Call load_base_font() first.")

        # Ensure output directory exists
        self.output_font_path.parent.mkdir(parents=True, exist_ok=True)

        self.ttfont.save(str(self.output_font_path))
        logger.info(
            "Wrote %s with %d icons starting at U+%04X",
            self.output_font_path,
            self.added_count,
            self.pua_start,
        )


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argument namespace containing all command-line options.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Create a PUA icon font from images by extending " "NotoColorEmoji.ttf"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input-folder",
        type=Path,
        help="Directory containing icon files (PNGs/SVGs). "
        "Icons will be sorted alphabetically by filename. "
        "Default: ./input_symbols",
    )
    input_group.add_argument(
        "--icon-paths",
        type=Path,
        nargs="+",
        metavar="PATH",
        help="List of icon file paths. Icons will be mapped to Unicode "
        "code points in the order specified.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default="./MyCreatedIconFont.ttf",
        help="Output font file path. Default: ./MyCreatedIconFont.ttf",
    )
    parser.add_argument(
        "--base-font",
        type=Path,
        default="NotoColorEmoji.ttf",
        help="Path to the base CBDT/CBLC font (e.g., NotoColorEmoji.ttf). "
        "Default: ./NotoColorEmoji.ttf",
    )
    parser.add_argument(
        "--family-name",
        type=str,
        default="MyCreatedIconFont",
        help="Font family name. Default: MyCreatedIconFont",
    )
    parser.add_argument(
        "--pua-start",
        type=lambda x: int(x, 16 if x.startswith("0x") else 10),
        default=PUA_START,
        metavar="CODEPOINT",
        help=(
            f"Starting Unicode code point in the Private Use Area. "
            f"Default: 0x{PUA_START:04X}"
        ),
    )

    parser.add_argument(
        "--remove-original-symbols",
        action="store_true",
        help=(
            "Remove all original symbols from the base font, keeping only "
            "essential glyphs (.notdef and space) and the newly added icons. "
            "Use this to produce a minimal icon-only font."
        ),
    )

    return parser.parse_args()


def main() -> None:
    """Build the output font by extending a CBDT/CBLC base font.

    This function processes command-line arguments, loads a base font,
    collects and normalizes input images, and appends them as PNG bitmaps
    mapped to the Private Use Area.
    """
    args = parse_arguments()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s - %(message)s",
    )

    if args.icon_paths:
        image_paths = args.icon_paths
    else:
        input_folder = args.input_folder if args.input_folder else Path("input_symbols")
        image_paths = [
            path
            for path in input_folder.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".svg"}
        ]
        image_paths.sort(key=lambda path: path.name)

    builder = FontBuilder(
        base_font_path=args.base_font,
        output_font_path=args.output,
        pua_start=args.pua_start,
        remove_original_symbols=args.remove_original_symbols,
        image_paths=image_paths,
        font_family_name=args.family_name,
    ).build_complete_font()
    builder.save()


if __name__ == "__main__":
    main()
