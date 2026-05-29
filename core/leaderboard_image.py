"""PNG renderer for the persistent leaderboard embed.

Produces a single sharp PNG card depicting the top-N users for a guild.
The card is rendered at 1200px wide (2x logical) so it stays crisp on
Discord's retina displays.

Design language — "editorial scoreboard"
----------------------------------------
* One sans-serif family (Bricolage Grotesque variable) for *everything*.
  Hierarchy comes from weight + size contrast, not from font swapping.
* Deep aubergine vertical gradient. No grain, no vignette — flat
  surface so text contrast is maximal.
* Server icon at the top-left in a rounded square (coat-of-arms inset).
* Title "LEADERBOARD" in heavy weight, tight tracking. Server name
  immediately below as the matching subtitle.
* Top-3 podium emphasis is purely structural: each top-3 row has a
  6px medal-coloured stripe on its left edge plus a hairline ring on
  the avatar. No micro-text "chips" — those were unreadable in v1.
* Rank numerals are the loudest element: giant in the top 3, large in
  the rest. Tabular figures + medal colour for 1/2/3.
* Footer is a single subtle line — wordmark + "UPDATED at HH:MM UTC".
  No "LIVE" indicator.

The renderer is pure synchronous Pillow code — call ``render_png`` from
an executor so it never blocks the gateway loop.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_PATH = _ASSETS_DIR / "BricolageGrotesque-VariableFont.ttf"


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

# Background gradient endpoints (top -> bottom).
BG_TOP = (14, 7, 23)
BG_BOTTOM = (27, 14, 50)

# Surface tints for row cards (low-alpha overlays on the gradient).
SURFACE_TOP3 = (255, 255, 255, 22)
SURFACE_STD = (255, 255, 255, 12)

# Hairline keylines.
KEYLINE = (160, 134, 215, 70)
KEYLINE_STRONG = (180, 156, 230, 130)

# Text colours — much higher contrast than v1.
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (215, 203, 240, 255)
TEXT_DIM = (160, 142, 200, 255)

# Medal colours — pulled brighter so they read on dark backgrounds.
GOLD = (255, 209, 102, 255)
SILVER = (232, 236, 244, 255)
BRONZE = (232, 148, 93, 255)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CANVAS_W = 1200
H_PAD = 56            # left/right outer padding
HEADER_H = 220
PODIUM_ROW_H = 200
STANDARD_ROW_H = 104
FOOTER_H = 76
CARD_RADIUS = 24
STRIPE_WIDTH = 6
STRIPE_INSET = 18     # how far in from the card's left edge the stripe sits

# Avatar sizes per rank tier
AVATAR_TOP1 = 132
AVATAR_TOP23 = 110
AVATAR_STD = 64

# Server icon at top-left.
SERVER_ICON_SIZE = 108
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

    Blocks for ~50-200 ms depending on row count and image sizes — run
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

    def _font(self, size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
        """Return a cached Bricolage Grotesque face at the given size/weight.

        Bricolage is a variable font (axes: wght 200-800, wdth 75-100,
        opsz 12-96). We set the weight axis explicitly so semibold and
        heavy render as intended.
        """
        key = f"{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = ImageFont.truetype(str(_FONT_PATH), size=size)
        try:
            # Axis order matches the font's fvar table: wght, wdth, opsz.
            font.set_variation_by_axes([weight, 100, min(max(size, 12), 96)])
        except Exception:
            # Fall back silently if Pillow is missing variable-font support;
            # the default instance will still render legibly.
            pass
        self._fonts[key] = font
        return font

    # ---------- background ----------

    def _make_background(self, w: int, h: int) -> Image.Image:
        """Vertical gradient only — no grain, no vignette."""
        base = Image.new("RGBA", (w, h), BG_TOP + (255,))
        draw = ImageDraw.Draw(base)
        for y in range(h):
            t = y / max(1, h - 1)
            # Smoothstep so the midband sits a touch darker than linear.
            t2 = t * t * (3 - 2 * t)
            r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t2)
            g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t2)
            b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t2)
            draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
        return base

    # ---------- header ----------

    def _draw_header(self, canvas: Image.Image) -> int:
        draw = ImageDraw.Draw(canvas, "RGBA")
        x = H_PAD
        y_top = 52

        # Server icon (or placeholder mark).
        if self.guild_icon_bytes is not None:
            icon = _rounded_square_image(
                self.guild_icon_bytes, SERVER_ICON_SIZE, SERVER_ICON_RADIUS
            )
        else:
            icon = _icon_placeholder(SERVER_ICON_SIZE, SERVER_ICON_RADIUS, self._font(64, 800))
        canvas.alpha_composite(icon, (x, y_top))
        # Subtle inset border around the icon.
        border = Image.new("RGBA", (SERVER_ICON_SIZE, SERVER_ICON_SIZE), (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle(
            (0, 0, SERVER_ICON_SIZE - 1, SERVER_ICON_SIZE - 1),
            radius=SERVER_ICON_RADIUS,
            outline=(255, 255, 255, 50),
            width=2,
        )
        canvas.alpha_composite(border, (x, y_top))

        # Title block.
        title_x = x + SERVER_ICON_SIZE + 32
        title_font = self._font(64, weight=800)
        draw.text(
            (title_x, y_top - 6),
            "LEADERBOARD",
            font=title_font,
            fill=TEXT_PRIMARY,
        )

        # Server name (clipped if very long).
        subtitle_font = self._font(28, weight=600)
        subtitle = _truncate(
            self.guild_name,
            subtitle_font,
            CANVAS_W - title_x - H_PAD - 280,
        )
        draw.text(
            (title_x, y_top + 78),
            subtitle,
            font=subtitle_font,
            fill=TEXT_SECONDARY,
        )

        # Top-right rendered-at tag.
        ts_font = self._font(22, weight=600)
        ts_text = self.rendered_at.strftime("%b %d  ·  %H:%M UTC").upper()
        tw = _text_width(draw, ts_text, ts_font)
        draw.text(
            (CANVAS_W - H_PAD - tw, y_top + 12),
            ts_text,
            font=ts_font,
            fill=TEXT_DIM,
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

        # Medal-coloured left stripe — the *only* signal for medal tier.
        _draw_left_stripe(canvas, y, row_h, color=medal_color)

        draw = ImageDraw.Draw(canvas, "RGBA")

        # Rank numeral — giant medal-coloured numbers, tabular spacing.
        rank_font = self._font(110 if is_first else 92, weight=800)
        rank_text = f"{entry.rank:02d}"
        rb = rank_font.getbbox(rank_text)
        rh = rb[3] - rb[1]
        rank_x = H_PAD + STRIPE_INSET + STRIPE_WIDTH + 22
        rank_y = y + (row_h - rh) // 2 - rb[1]
        draw.text((rank_x, rank_y), rank_text, font=rank_font, fill=medal_color)

        # Avatar position.
        rank_w = rb[2] - rb[0]
        ax = rank_x + rank_w + 40
        ay = y + (row_h - avatar_size) // 2

        # Avatar.
        if entry.avatar_bytes is not None:
            avatar = _circular_image(entry.avatar_bytes, avatar_size)
        else:
            avatar = _avatar_placeholder(avatar_size, medal_color)
        canvas.alpha_composite(avatar, (ax, ay))

        # Hairline medal ring around the avatar.
        ring_inset = 3
        ring_size = avatar_size + 2 * ring_inset
        ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, ring_size - 1, ring_size - 1),
            outline=medal_color[:3] + (200,),
            width=2,
        )
        canvas.alpha_composite(ring, (ax - ring_inset, ay - ring_inset))

        # Name + meta block.
        text_x = ax + avatar_size + 36
        name_size = 50 if is_first else 44
        name_font = self._font(name_size, weight=700)
        meta_font = self._font(26, weight=500)

        # Generous right gutter — no chip to worry about now.
        name_max = CANVAS_W - text_x - H_PAD - 32
        name = _truncate(entry.display_name, name_font, name_max)
        name_bbox = name_font.getbbox(name)
        name_h = name_bbox[3] - name_bbox[1]
        meta_bbox = meta_font.getbbox("Ay")
        meta_h = meta_bbox[3] - meta_bbox[1]
        gap = 14
        block_h = name_h + gap + meta_h
        block_y = y + (row_h - block_h) // 2 - name_bbox[1]

        draw.text((text_x, block_y), name, font=name_font, fill=TEXT_PRIMARY)

        meta_y = block_y + name_h + gap
        level_text = f"Level {entry.level}"
        xp_text = f"{entry.xp:,} XP"
        draw.text((text_x, meta_y), level_text, font=meta_font, fill=TEXT_SECONDARY)
        lw = _text_width(draw, level_text, meta_font)
        # Soft pill separator dot in medal colour.
        sep_x = text_x + lw + 18
        dot_r = 4
        dot_y = meta_y + (meta_h // 2)
        draw.ellipse(
            (sep_x - dot_r, dot_y - dot_r, sep_x + dot_r, dot_y + dot_r),
            fill=medal_color,
        )
        draw.text((sep_x + 16, meta_y), xp_text, font=meta_font, fill=TEXT_DIM)

        return y + row_h

    # ---------- standard rows (ranks 4..10) ----------

    def _draw_standard_row(
        self, canvas: Image.Image, entry: LeaderboardEntry, y: int
    ) -> int:
        row_h = STANDARD_ROW_H
        _draw_row_card(canvas, y, row_h, fill=SURFACE_STD, keyline=KEYLINE)

        draw = ImageDraw.Draw(canvas, "RGBA")

        # Rank.
        rank_font = self._font(56, weight=700)
        rank_text = f"{entry.rank:02d}"
        rb = rank_font.getbbox(rank_text)
        rh = rb[3] - rb[1]
        rank_x = H_PAD + 26
        ry = y + (row_h - rh) // 2 - rb[1]
        draw.text((rank_x, ry), rank_text, font=rank_font, fill=TEXT_SECONDARY)

        # Avatar.
        rank_w = rb[2] - rb[0]
        ax = rank_x + rank_w + 28
        ay = y + (row_h - AVATAR_STD) // 2
        if entry.avatar_bytes is not None:
            avatar = _circular_image(entry.avatar_bytes, AVATAR_STD)
        else:
            avatar = _avatar_placeholder(AVATAR_STD, (160, 134, 215, 255))
        canvas.alpha_composite(avatar, (ax, ay))
        ring = Image.new("RGBA", (AVATAR_STD + 4, AVATAR_STD + 4), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, AVATAR_STD + 3, AVATAR_STD + 3),
            outline=KEYLINE_STRONG,
            width=1,
        )
        canvas.alpha_composite(ring, (ax - 2, ay - 2))

        # Name + right-aligned meta.
        name_font = self._font(34, weight=600)
        meta_font = self._font(24, weight=500)

        # Right-aligned meta: "Level X" (bright) · "XX,XXX XP" (dim).
        lv_part = f"Level {entry.level}"
        xp_part = f"{entry.xp:,} XP"
        lv_w = _text_width(draw, lv_part, meta_font)
        xp_w = _text_width(draw, xp_part, meta_font)
        right_edge = CANVAS_W - H_PAD - 24
        xp_x = right_edge - xp_w
        sep_x = xp_x - 16
        lv_x = sep_x - 16 - lv_w

        # Name.
        name_x = ax + AVATAR_STD + 28
        name_max = lv_x - name_x - 32
        name = _truncate(entry.display_name, name_font, name_max)
        nb = name_font.getbbox(name)
        nh = nb[3] - nb[1]
        ny = y + (row_h - nh) // 2 - nb[1]
        draw.text((name_x, ny), name, font=name_font, fill=TEXT_PRIMARY)

        # Vertical-centre the meta.
        mb = meta_font.getbbox("Ay")
        mh = mb[3] - mb[1]
        my = y + (row_h - mh) // 2 - mb[1]
        draw.text((lv_x, my), lv_part, font=meta_font, fill=TEXT_SECONDARY)
        draw.ellipse(
            (sep_x - 3, y + row_h // 2 - 3, sep_x + 3, y + row_h // 2 + 3),
            fill=TEXT_DIM[:3] + (220,),
        )
        draw.text((xp_x, my), xp_part, font=meta_font, fill=TEXT_DIM)

        return y + row_h

    # ---------- footer ----------

    def _draw_footer(self, canvas: Image.Image, y: int) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.line(
            [(H_PAD, y), (CANVAS_W - H_PAD, y)],
            fill=KEYLINE,
            width=1,
        )
        cy = y + FOOTER_H // 2

        # Left wordmark.
        mark_font = self._font(28, weight=800)
        mark_text = "prog"
        mb = mark_font.getbbox(mark_text)
        mh = mb[3] - mb[1]
        draw.text(
            (H_PAD, cy - mh // 2 - mb[1]),
            mark_text,
            font=mark_font,
            fill=TEXT_PRIMARY,
        )

        # Right timestamp.
        body_font = self._font(22, weight=600)
        timestamp = self.rendered_at.strftime("UPDATED %b %d · %H:%M UTC").upper()
        tw = _text_width(draw, timestamp, body_font)
        body_bbox = body_font.getbbox(timestamp)
        body_h = body_bbox[3] - body_bbox[1]
        draw.text(
            (CANVAS_W - H_PAD - tw, cy - body_h // 2 - body_bbox[1]),
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
    d.ellipse((0, 0, size, size), fill=(45, 28, 80, 255))
    d.ellipse(
        (3, 3, size - 3, size - 3),
        outline=accent[:3] + (180,),
        width=2,
    )
    return img


def _icon_placeholder(
    size: int, radius: int, font: ImageFont.FreeTypeFont
) -> Image.Image:
    """Rounded-square placeholder used when the guild has no icon set."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (0, 0, size, size), radius=radius, fill=(45, 28, 80, 255)
    )
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
    return img


def _draw_row_card(
    canvas: Image.Image,
    y: int,
    h: int,
    *,
    fill: tuple,
    keyline: tuple,
) -> None:
    """Soft tinted card surface for one leaderboard row + hairline bottom."""
    card_w = CANVAS_W - 2 * H_PAD
    card = Image.new("RGBA", (card_w, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(
        (0, 0, card_w - 1, h - 1),
        radius=CARD_RADIUS,
        fill=fill,
    )
    canvas.alpha_composite(card, (H_PAD, y))
    ImageDraw.Draw(canvas, "RGBA").line(
        [(H_PAD + CARD_RADIUS, y + h - 1), (CANVAS_W - H_PAD - CARD_RADIUS, y + h - 1)],
        fill=keyline,
        width=1,
    )


def _draw_left_stripe(
    canvas: Image.Image,
    y: int,
    h: int,
    *,
    color: tuple,
) -> None:
    """Draw the medal-coloured vertical edge stripe on a top-3 row card."""
    x0 = H_PAD + STRIPE_INSET
    x1 = x0 + STRIPE_WIDTH
    y0 = y + 16
    y1 = y + h - 16
    stripe = Image.new("RGBA", (STRIPE_WIDTH, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(stripe).rounded_rectangle(
        (0, 0, STRIPE_WIDTH - 1, y1 - y0 - 1),
        radius=STRIPE_WIDTH // 2,
        fill=color[:3] + (255,),
    )
    canvas.alpha_composite(stripe, (x0, y0))


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
