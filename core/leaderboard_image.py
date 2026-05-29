"""PNG renderer for the persistent leaderboard embed.

Produces a single sharp PNG card depicting the top-N users for a guild.
The card is intentionally portrait-oriented and rendered at 2x logical
resolution (1200px wide) so it stays crisp on Discord's retina displays.

Design language:
* Deep aubergine vertical gradient base + faint vertical grain.
* Rounded-square server icon at the top left ("coat of arms" inset).
* Display monospace title and rank numerals (broadcast-scoreboard feel).
* Bricolage Grotesque body for names and XP figures.
* Top-3 podium emphasis: glow-ringed avatar + medal chip for rank 1,
  inset metallic chips for ranks 2 and 3.
* Hairline keylines (1px alpha) instead of heavy dividers.
* Footer with timestamp + magenta "LIVE" dot.

The renderer is pure synchronous Pillow code — call ``render_png`` from
an executor so it never blocks the gateway loop.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PIL import (
    Image,
    ImageDraw,
    ImageFilter,
    ImageFont,
)

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_DISPLAY = _ASSETS_DIR / "MajorMonoDisplay-Regular.ttf"
_FONT_BODY = _ASSETS_DIR / "BricolageGrotesque-VariableFont.ttf"


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

# Background gradient endpoints (top -> bottom, deep aubergine -> mid violet).
BG_TOP = (15, 7, 28)
BG_BOTTOM = (32, 16, 56)

# Surface tints for rows (applied as low-alpha overlays on the gradient).
SURFACE_TOP3 = (255, 255, 255, 14)
SURFACE_STD = (255, 255, 255, 6)

# Hairline keylines.
KEYLINE = (137, 110, 200, 70)
KEYLINE_STRONG = (137, 110, 200, 120)

# Text colours.
TEXT_PRIMARY = (245, 240, 255, 255)
TEXT_SECONDARY = (190, 175, 225, 255)
TEXT_DIM = (140, 122, 175, 255)

# Accent + medals.
ACCENT_MAGENTA = (244, 114, 182, 255)
GOLD = (251, 191, 36, 255)
SILVER = (220, 220, 232, 255)
BRONZE = (217, 119, 60, 255)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CANVAS_W = 1200
H_PAD = 48           # left/right outer padding
HEADER_H = 220
PODIUM_ROW_H = 170
STANDARD_ROW_H = 86
FOOTER_H = 84
CARD_RADIUS = 22

# Avatar sizes per rank tier
AVATAR_TOP1 = 110
AVATAR_TOP23 = 88
AVATAR_STD = 56

# Server icon at top-left
SERVER_ICON_SIZE = 104
SERVER_ICON_RADIUS = 22


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaderboardEntry:
    """One row of leaderboard data."""

    rank: int
    display_name: str
    level: int
    xp: int
    avatar_bytes: Optional[bytes]  # raw PNG/JPG, None -> placeholder


def render_png(
    *,
    guild_name: str,
    guild_icon_bytes: Optional[bytes],
    entries: list[LeaderboardEntry],
    rendered_at: datetime,
) -> bytes:
    """Render the leaderboard card to PNG bytes.

    Blocks for ~50-200ms depending on row count and image sizes — run
    via ``loop.run_in_executor`` from async contexts.
    """
    return _Renderer(
        guild_name=guild_name,
        guild_icon_bytes=guild_icon_bytes,
        entries=entries,
        rendered_at=rendered_at,
    ).render()


# ---------------------------------------------------------------------------
# Renderer (private)
# ---------------------------------------------------------------------------


class _Renderer:
    def __init__(
        self,
        *,
        guild_name: str,
        guild_icon_bytes: Optional[bytes],
        entries: list[LeaderboardEntry],
        rendered_at: datetime,
    ) -> None:
        self.guild_name = guild_name
        self.guild_icon_bytes = guild_icon_bytes
        self.entries = entries
        self.rendered_at = rendered_at

        self.top3 = entries[:3]
        self.rest = entries[3:]
        self.height = (
            HEADER_H
            + len(self.top3) * PODIUM_ROW_H
            + len(self.rest) * STANDARD_ROW_H
            + FOOTER_H
        )

        # Font handles — loaded lazily inside render so that constructing
        # a Renderer is cheap (helpful for tests).
        self._fonts: dict[str, ImageFont.FreeTypeFont] = {}

    # ---------- entry point ----------

    def render(self) -> bytes:
        canvas = self._make_background(CANVAS_W, self.height)

        y = self._draw_header(canvas)
        for entry in self.top3:
            y = self._draw_podium_row(canvas, entry, y)
        for entry in self.rest:
            y = self._draw_standard_row(canvas, entry, y)
        self._draw_footer(canvas, y)

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # ---------- fonts ----------

    def _font(self, family: str, size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
        """Return a cached ImageFont. ``family`` is 'display' or 'body'.

        For the variable body font we set the axis via ``set_variation_by_axes``
        when supported; otherwise we fall back to the default instance.
        """
        key = f"{family}:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached

        path = _FONT_DISPLAY if family == "display" else _FONT_BODY
        font = ImageFont.truetype(str(path), size=size)
        if family == "body":
            try:
                font.set_variation_by_axes([weight, 100, 12])  # wght, wdth, opsz
            except Exception:
                # Older Pillow or non-variable build — silently fall back.
                pass
        self._fonts[key] = font
        return font

    # ---------- background ----------

    def _make_background(self, w: int, h: int) -> Image.Image:
        """Vertical gradient + a faint vertical-line grain overlay."""
        base = Image.new("RGBA", (w, h), BG_TOP + (255,))
        draw = ImageDraw.Draw(base)

        # Per-row gradient fill.
        for y in range(h):
            t = y / max(1, h - 1)
            # Ease the gradient a touch so the midband sits darker.
            t2 = t * t * (3 - 2 * t)
            r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t2)
            g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t2)
            b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t2)
            draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

        # Faint vertical grain: every 4 px draw a 1 px translucent line.
        grain = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grain)
        for x in range(0, w, 4):
            gd.line([(x, 0), (x, h)], fill=(255, 255, 255, 6))
        base = Image.alpha_composite(base, grain)

        # Soft radial-ish vignette: a darker overlay that fades from edges
        # to the centre, approximated by two horizontal gradient strips.
        vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vignette)
        edge = 220
        for x in range(edge):
            a = int(70 * (1 - x / edge))
            vd.line([(x, 0), (x, h)], fill=(0, 0, 0, a))
            vd.line([(w - 1 - x, 0), (w - 1 - x, h)], fill=(0, 0, 0, a))
        base = Image.alpha_composite(base, vignette)

        return base

    # ---------- header ----------

    def _draw_header(self, canvas: Image.Image) -> int:
        draw = ImageDraw.Draw(canvas, "RGBA")
        x = H_PAD
        y_top = 56

        # Server icon (or placeholder mark).
        if self.guild_icon_bytes is not None:
            icon = _rounded_square_image(
                self.guild_icon_bytes, SERVER_ICON_SIZE, SERVER_ICON_RADIUS
            )
        else:
            icon = _icon_placeholder(SERVER_ICON_SIZE, SERVER_ICON_RADIUS)

        # Subtle white inset border around the icon.
        border = Image.new("RGBA", (SERVER_ICON_SIZE, SERVER_ICON_SIZE), (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle(
            (0, 0, SERVER_ICON_SIZE - 1, SERVER_ICON_SIZE - 1),
            radius=SERVER_ICON_RADIUS,
            outline=(255, 255, 255, 35),
            width=2,
        )
        canvas.paste(icon, (x, y_top), icon)
        canvas.paste(border, (x, y_top), border)

        # Title block.
        title_x = x + SERVER_ICON_SIZE + 28
        title_font = self._font("display", 56)
        draw.text(
            (title_x, y_top + 6),
            "LEADERBOARD",
            font=title_font,
            fill=TEXT_PRIMARY,
        )

        # Server name (clipped if very long).
        subtitle_font = self._font("body", 30, weight=700)
        subtitle = _truncate(
            self.guild_name.upper(),
            subtitle_font,
            CANVAS_W - title_x - H_PAD - 220,
        )
        draw.text(
            (title_x, y_top + 76),
            subtitle,
            font=subtitle_font,
            # Magenta-tinted secondary so the title hierarchy is loud.
            fill=(225, 195, 240, 255),
        )

        # Top-right rendered-at tag.
        ts_font = self._font("body", 22, weight=600)
        ts_text = self.rendered_at.strftime("%b %d · %H:%M UTC")
        tw = _text_width(draw, ts_text, ts_font)
        draw.text(
            (CANVAS_W - H_PAD - tw, y_top + 14),
            ts_text,
            font=ts_font,
            fill=TEXT_SECONDARY,
        )

        # Bottom hairline of the header.
        line_y = HEADER_H - 1
        draw.line([(H_PAD, line_y), (CANVAS_W - H_PAD, line_y)], fill=KEYLINE, width=1)

        return HEADER_H

    # ---------- podium rows (ranks 1..3) ----------

    def _draw_podium_row(
        self, canvas: Image.Image, entry: LeaderboardEntry, y: int
    ) -> int:
        is_first = entry.rank == 1
        medal_color = {1: GOLD, 2: SILVER, 3: BRONZE}[entry.rank]
        avatar_size = AVATAR_TOP1 if is_first else AVATAR_TOP23
        row_h = PODIUM_ROW_H

        # Card surface.
        _draw_row_card(canvas, y, row_h, fill=SURFACE_TOP3, keyline=KEYLINE)

        draw = ImageDraw.Draw(canvas, "RGBA")

        # Rank numeral (display mono).
        rank_size = 86 if is_first else 64
        rank_font = self._font("display", rank_size)
        rank_text = f"{entry.rank:02d}"
        rank_x = H_PAD + 22
        # Vertically centre the numeral inside the row.
        rb = rank_font.getbbox(rank_text)
        rh = rb[3] - rb[1]
        rank_y = y + (row_h - rh) // 2 - rb[1]
        # Medal-coloured fill for ranks 1-3.
        draw.text((rank_x, rank_y), rank_text, font=rank_font, fill=medal_color)

        # Avatar position.
        ax = H_PAD + 220
        ay = y + (row_h - avatar_size) // 2

        # Glow ring (rank 1 only) - blurred medal-coloured ring under the avatar.
        if is_first:
            glow = _glow_ring(avatar_size + 36, medal_color, ring_width=4, blur=14)
            canvas.alpha_composite(
                glow, (ax - 18, ay - 18)
            )

        # Avatar.
        if entry.avatar_bytes is not None:
            avatar = _circular_image(entry.avatar_bytes, avatar_size)
        else:
            avatar = _avatar_placeholder(avatar_size, medal_color)
        canvas.alpha_composite(avatar, (ax, ay))

        # Hairline ring around the avatar (always for top 3).
        ring = Image.new("RGBA", (avatar_size + 6, avatar_size + 6), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, avatar_size + 5, avatar_size + 5),
            outline=medal_color[:3] + (180,),
            width=2,
        )
        canvas.alpha_composite(ring, (ax - 3, ay - 3))

        # Medal chip is drawn last (so we know its left edge for truncation).
        chip_text = {1: "GOLD", 2: "SILVER", 3: "BRONZE"}[entry.rank]
        chip_font = self._font("display", 22)
        chip_bbox = chip_font.getbbox(chip_text)
        chip_w = (chip_bbox[2] - chip_bbox[0]) + 32  # text + horizontal padding
        chip_right = CANVAS_W - H_PAD - 28
        chip_left = chip_right - chip_w

        # Name + level + XP block.
        text_x = ax + avatar_size + 32
        name_size = 44 if is_first else 38
        name_font = self._font("body", name_size, weight=700)
        meta_font = self._font("body", 24, weight=500)

        # Leave a 32px gutter between the longest meta line and the chip.
        name_max = chip_left - text_x - 32
        name = _truncate(entry.display_name, name_font, name_max)
        # Stack: name above, level + xp below.
        name_bbox = name_font.getbbox(name)
        name_h = name_bbox[3] - name_bbox[1]
        meta_bbox = meta_font.getbbox("Ay")
        meta_h = meta_bbox[3] - meta_bbox[1]
        gap = 10
        block_h = name_h + gap + meta_h
        block_y = y + (row_h - block_h) // 2 - name_bbox[1]

        draw.text(
            (text_x, block_y),
            name,
            font=name_font,
            fill=TEXT_PRIMARY,
        )

        meta_y = block_y + name_h + gap
        # Level (small chip) + XP (right of it)
        level_text = f"LV {entry.level}"
        xp_text = f"{entry.xp:,} XP"
        # Two-tone meta line: level brighter, xp dim.
        draw.text((text_x, meta_y), level_text, font=meta_font, fill=TEXT_PRIMARY)
        lw = _text_width(draw, level_text, meta_font)
        sep_x = text_x + lw + 16
        # Magenta dot separator.
        dot_r = 4
        dot_y = meta_y + (meta_h // 2)
        draw.ellipse(
            (sep_x - dot_r, dot_y - dot_r, sep_x + dot_r, dot_y + dot_r),
            fill=ACCENT_MAGENTA,
        )
        draw.text((sep_x + 14, meta_y), xp_text, font=meta_font, fill=TEXT_SECONDARY)

        # Medal chip on the right (uses chip_text/chip_font computed above).
        _draw_medal_chip(
            canvas,
            x_right=chip_right,
            y_centre=y + row_h // 2,
            text=chip_text,
            color=medal_color,
            font=chip_font,
        )

        return y + row_h

    # ---------- standard rows (ranks 4..10) ----------

    def _draw_standard_row(
        self, canvas: Image.Image, entry: LeaderboardEntry, y: int
    ) -> int:
        row_h = STANDARD_ROW_H
        _draw_row_card(canvas, y, row_h, fill=SURFACE_STD, keyline=KEYLINE)

        draw = ImageDraw.Draw(canvas, "RGBA")

        # Rank.
        rank_font = self._font("display", 40)
        rank_text = f"{entry.rank:02d}"
        rb = rank_font.getbbox(rank_text)
        rh = rb[3] - rb[1]
        ry = y + (row_h - rh) // 2 - rb[1]
        draw.text(
            (H_PAD + 22, ry),
            rank_text,
            font=rank_font,
            fill=TEXT_SECONDARY,
        )

        # Avatar.
        ax = H_PAD + 152
        ay = y + (row_h - AVATAR_STD) // 2
        if entry.avatar_bytes is not None:
            avatar = _circular_image(entry.avatar_bytes, AVATAR_STD)
        else:
            avatar = _avatar_placeholder(AVATAR_STD, (137, 110, 200, 255))
        canvas.alpha_composite(avatar, (ax, ay))
        # Subtle hairline ring.
        ring = Image.new("RGBA", (AVATAR_STD + 4, AVATAR_STD + 4), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, AVATAR_STD + 3, AVATAR_STD + 3),
            outline=KEYLINE_STRONG,
            width=1,
        )
        canvas.alpha_composite(ring, (ax - 2, ay - 2))

        # Name on the left of the right-anchored level/xp block.
        name_font = self._font("body", 28, weight=600)
        meta_font = self._font("body", 22, weight=500)

        # Right block: "LV X · XXX,XXX XP" right-aligned.
        right_text = f"LV {entry.level}   {entry.xp:,} XP"
        right_w = _text_width(draw, right_text, meta_font)
        right_x = CANVAS_W - H_PAD - 16 - right_w

        # Name (truncate to fit between avatar and right block).
        name_x = ax + AVATAR_STD + 28
        name_max = right_x - name_x - 24
        name = _truncate(entry.display_name, name_font, name_max)
        nb = name_font.getbbox(name)
        nh = nb[3] - nb[1]
        ny = y + (row_h - nh) // 2 - nb[1]
        draw.text((name_x, ny), name, font=name_font, fill=TEXT_PRIMARY)

        # Right-aligned meta.
        mb = meta_font.getbbox(right_text)
        mh = mb[3] - mb[1]
        my = y + (row_h - mh) // 2 - mb[1]
        # Split the meta into LV (bright) and XP (dim) for hierarchy.
        lv_part = f"LV {entry.level}"
        xp_part = f"{entry.xp:,} XP"
        lv_w = _text_width(draw, lv_part, meta_font)
        # Right-anchor xp, then place lv before it.
        xp_w = _text_width(draw, xp_part, meta_font)
        xp_x = CANVAS_W - H_PAD - 16 - xp_w
        sep_x = xp_x - 16
        lv_x = sep_x - 16 - lv_w
        draw.text((lv_x, my), lv_part, font=meta_font, fill=TEXT_PRIMARY)
        draw.ellipse(
            (sep_x - 3, y + row_h // 2 - 3, sep_x + 3, y + row_h // 2 + 3),
            fill=ACCENT_MAGENTA,
        )
        draw.text((xp_x, my), xp_part, font=meta_font, fill=TEXT_DIM)

        return y + row_h

    # ---------- footer ----------

    def _draw_footer(self, canvas: Image.Image, y: int) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        # Strong-ish keyline above the footer.
        draw.line(
            [(H_PAD, y), (CANVAS_W - H_PAD, y)],
            fill=KEYLINE,
            width=1,
        )

        cy = y + FOOTER_H // 2

        # Left: "prog" wordmark in display font.
        mark_font = self._font("display", 26)
        mark_text = "prog"
        mb = mark_font.getbbox(mark_text)
        mh = mb[3] - mb[1]
        draw.text(
            (H_PAD, cy - mh // 2 - mb[1]),
            mark_text,
            font=mark_font,
            fill=TEXT_PRIMARY,
        )

        # Right: LIVE pulse dot + "updated at HH:MM"
        live_font = self._font("display", 20)
        body_font = self._font("body", 22, weight=500)
        live_text = "LIVE"
        timestamp = self.rendered_at.strftime("UPDATED %H:%M UTC")

        lw = _text_width(draw, live_text, live_font)
        tw = _text_width(draw, timestamp, body_font)

        right_edge = CANVAS_W - H_PAD
        ts_x = right_edge - tw
        live_x = ts_x - 16 - lw
        dot_x = live_x - 18
        dot_r = 6

        # Pulse dot (solid + blurred outer glow).
        glow = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((10, 10, 30, 30), fill=ACCENT_MAGENTA)
        glow = glow.filter(ImageFilter.GaussianBlur(6))
        canvas.alpha_composite(glow, (dot_x - 20, cy - 20))
        draw.ellipse(
            (dot_x - dot_r, cy - dot_r, dot_x + dot_r, cy + dot_r),
            fill=ACCENT_MAGENTA,
        )

        live_bbox = live_font.getbbox(live_text)
        live_h = live_bbox[3] - live_bbox[1]
        draw.text(
            (live_x, cy - live_h // 2 - live_bbox[1]),
            live_text,
            font=live_font,
            fill=ACCENT_MAGENTA,
        )

        body_bbox = body_font.getbbox(timestamp)
        body_h = body_bbox[3] - body_bbox[1]
        draw.text(
            (ts_x, cy - body_h // 2 - body_bbox[1]),
            timestamp,
            font=body_font,
            fill=TEXT_DIM,
        )


# ---------------------------------------------------------------------------
# Drawing helpers (module-private; pure functions over Image)
# ---------------------------------------------------------------------------


def _circular_image(image_bytes: bytes, size: int) -> Image.Image:
    """Decode raw image bytes and crop to a circle at the given pixel size."""
    img = (
        Image.open(io.BytesIO(image_bytes))
        .convert("RGBA")
        .resize((size, size), Image.LANCZOS)
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _rounded_square_image(image_bytes: bytes, size: int, radius: int) -> Image.Image:
    """Decode raw image bytes into a rounded-square."""
    img = (
        Image.open(io.BytesIO(image_bytes))
        .convert("RGBA")
        .resize((size, size), Image.LANCZOS)
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size, size), radius=radius, fill=255
    )
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _avatar_placeholder(size: int, accent: tuple) -> Image.Image:
    """A circular avatar placeholder when a user has no avatar bytes."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=(40, 24, 70, 255))
    # Subtle inner ring.
    d.ellipse(
        (3, 3, size - 3, size - 3),
        outline=accent[:3] + (180,),
        width=2,
    )
    return img


def _icon_placeholder(size: int, radius: int) -> Image.Image:
    """A rounded-square placeholder used when the guild has no icon set."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (0, 0, size, size), radius=radius, fill=(40, 24, 70, 255)
    )
    # Decorative "p" mark — keeps the spot from feeling empty when no icon.
    try:
        font = ImageFont.truetype(str(_FONT_DISPLAY), size=size // 2)
        text = "p"
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        d.text(
            ((size - tw) // 2 - bbox[0], (size - th) // 2 - bbox[1]),
            text,
            font=font,
            fill=TEXT_PRIMARY,
        )
    except Exception:
        pass
    return img


def _glow_ring(
    size: int,
    color: tuple,
    *,
    ring_width: int = 3,
    blur: float = 8.0,
) -> Image.Image:
    """A blurred coloured ring used as a glow halo behind the rank-1 avatar."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse(
        (ring_width, ring_width, size - ring_width, size - ring_width),
        outline=color[:3] + (220,),
        width=ring_width,
    )
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _draw_row_card(
    canvas: Image.Image,
    y: int,
    h: int,
    *,
    fill: tuple,
    keyline: tuple,
) -> None:
    """Soft tinted card surface for one leaderboard row + hairline bottom."""
    card = Image.new("RGBA", (CANVAS_W - 2 * H_PAD, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(
        (0, 0, CANVAS_W - 2 * H_PAD - 1, h - 1),
        radius=CARD_RADIUS,
        fill=fill,
    )
    canvas.alpha_composite(card, (H_PAD, y))

    # Hairline divider at the bottom of the row.
    ImageDraw.Draw(canvas, "RGBA").line(
        [(H_PAD + CARD_RADIUS, y + h - 1), (CANVAS_W - H_PAD - CARD_RADIUS, y + h - 1)],
        fill=keyline,
        width=1,
    )


def _draw_medal_chip(
    canvas: Image.Image,
    *,
    x_right: int,
    y_centre: int,
    text: str,
    color: tuple,
    font: ImageFont.FreeTypeFont,
) -> int:
    """A solid metallic chip badge anchored to the right edge of a row.

    Returns the left-edge x coordinate of the chip so callers can size
    name-truncation rules to whatever room is left.
    """
    pad_x = 16
    pad_y = 10
    d = ImageDraw.Draw(canvas, "RGBA")
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    chip_w = tw + pad_x * 2
    chip_h = th + pad_y * 2
    x1 = x_right
    x0 = x1 - chip_w
    y0 = y_centre - chip_h // 2
    y1 = y0 + chip_h
    # Solid medal-colour fill with a slightly darker rim for definition.
    d.rounded_rectangle(
        (x0, y0, x1, y1), radius=chip_h // 2, fill=color[:3] + (255,)
    )
    d.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=chip_h // 2,
        outline=tuple(max(0, c - 60) for c in color[:3]) + (200,),
        width=1,
    )
    # Dark text (matches the page bg) for crisp contrast on the metal.
    d.text(
        (x0 + pad_x - bbox[0], y0 + pad_y - bbox[1]),
        text,
        font=font,
        fill=BG_TOP + (255,),
    )
    return x0


def _text_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont
) -> int:
    """Return the rendered pixel width of ``text``."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> str:
    """Trim ``text`` with an ellipsis until it fits ``max_width`` pixels."""
    if max_width <= 0:
        return ""
    bbox = font.getbbox(text)
    if bbox[2] - bbox[0] <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed:
        candidate = trimmed + ellipsis
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_width:
            return candidate
        trimmed = trimmed[:-1]
    return ellipsis


# ---------------------------------------------------------------------------
# Convenience for callers that don't want to build LeaderboardEntry tuples
# ---------------------------------------------------------------------------


def entries_from_iter(
    rows: Iterable[tuple[int, str, int, int, Optional[bytes]]],
) -> list[LeaderboardEntry]:
    """Adapter: build a list of entries from ``(rank, name, level, xp, avatar)``."""
    return [
        LeaderboardEntry(
            rank=rank,
            display_name=name,
            level=level,
            xp=xp,
            avatar_bytes=avatar,
        )
        for rank, name, level, xp, avatar in rows
    ]
