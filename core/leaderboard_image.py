"""PNG renderer for the persistent leaderboard embed.

Produces a single sharp PNG card depicting the top-10 users for a guild.
The card is rendered at 1200px wide (2x logical) so it stays crisp on
Discord's retina displays.

Design language — "editorial scoreboard"
----------------------------------------
Two type families, carefully partitioned:

* **Fraunces** (variable, OFL) — a high-contrast display serif with
  optical-size + weight + softness + wonk axes. Used *only* for the
  ``LEADERBOARD`` masthead and the top-3 ordinal labels (``1st`` /
  ``2nd`` / ``3rd``). This is the "trophy" voice — premium and
  editorial, but reserved.
* **Bricolage Grotesque** (variable, OFL) — clean modern sans-serif.
  Used for every user-facing string: the server name, all user
  display names (top-3 and field alike), every ``Level X`` tag, and
  the ordinals on rows 4-10. Sans is more legible for names; that
  reverts a misstep from v4.

Podium hierarchy is physical, not just typographic — the top-3 rows
have *different* heights (1st > 2nd > 3rd) so the medal positions
feel like an actual podium when you scan the card.

Other rules:

* Deep aubergine vertical gradient. No grain, no vignette — flat
  surface so text contrast is maximal.
* Server icon at the top-left in a rounded square (coat-of-arms inset).
* Top-3 podium emphasis is structural: each top-3 row has a 6px
  medal-coloured stripe on its left edge plus a hairline medal ring
  on the avatar.
* Names are the heroes — they auto-size *per row* to fill the
  available width. Short names render giant; long names downsize and
  finally truncate.
* The only number besides the rank is the user's Level. XP is omitted.
* No footer, no timestamp. The card is just the leaderboard.

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
_FONT_SANS_PATH = _ASSETS_DIR / "BricolageGrotesque-VariableFont.ttf"
_FONT_SERIF_PATH = _ASSETS_DIR / "Fraunces-VariableFont.ttf"


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
HEADER_H = 240
STANDARD_ROW_H = 118
BOTTOM_PAD = 24       # space below the last row (replaces the footer)
CARD_RADIUS = 24
STRIPE_WIDTH = 6
STRIPE_INSET = 18     # how far in from the card's left edge the stripe sits

# Per-rank podium dimensions. The hierarchy is physical: 1st > 2nd > 3rd
# in row height, ordinal-label size, avatar size, name-bounds, and Level
# size. Numbers were tuned so a 14-char name on each row reads as
# roughly the same "presence" — bigger on 1st, smaller on 3rd.
_PODIUM_DIMS: dict[int, dict] = {
    1: {
        "row_h": 244,
        "ordinal_size": 132,
        "avatar": 156,
        "name_fit": (52, 96),
        "level_size": 52,
    },
    2: {
        "row_h": 218,
        "ordinal_size": 106,
        "avatar": 130,
        "name_fit": (44, 80),
        "level_size": 42,
    },
    3: {
        "row_h": 196,
        "ordinal_size": 88,
        "avatar": 108,
        "name_fit": (38, 66),
        "level_size": 36,
    },
}

# Avatar size for ranks 4-10.
AVATAR_STD = 74

# Server icon at top-left.
SERVER_ICON_SIZE = 116
SERVER_ICON_RADIUS = 22

# Adaptive name-fit bounds for standard rows (4-10). Same family as the
# podium names (Bricolage) so visual rhythm carries through.
NAME_FIT_STD = (32, 62)


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
        podium_h = sum(_PODIUM_DIMS[e.rank]["row_h"] for e in self.top3)
        self.height = (
            HEADER_H
            + podium_h
            + len(self.rest) * STANDARD_ROW_H
            + BOTTOM_PAD
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

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # ---------- fonts ----------

    def _sans(self, size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
        """Return a cached Bricolage Grotesque face at the given size/weight.

        Bricolage axes (fvar order): wght 200-800, wdth 75-100, opsz 12-96.
        """
        key = f"sans:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = ImageFont.truetype(str(_FONT_SANS_PATH), size=size)
        try:
            font.set_variation_by_axes([weight, 100, min(max(size, 12), 96)])
        except Exception:
            pass
        self._fonts[key] = font
        return font

    def _serif(self, size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
        """Return a cached Fraunces face at the given size/weight.

        Fraunces axes (fvar order): opsz 9-144, wght 100-900, SOFT 0-100,
        WONK 0-1. We pin opsz to the rendered size for proper optical
        sizing, run SOFT=0 (sharp serifs), and WONK=0 (clean letterforms
        — no whimsical alternates).
        """
        key = f"serif:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = ImageFont.truetype(str(_FONT_SERIF_PATH), size=size)
        try:
            font.set_variation_by_axes(
                [min(max(size, 9), 144), weight, 0, 0]
            )
        except Exception:
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
            icon = _icon_placeholder(
                SERVER_ICON_SIZE,
                SERVER_ICON_RADIUS,
                self._sans(72, weight=800),
            )
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

        # Title block — Fraunces, magazine-masthead scale.
        title_x = x + SERVER_ICON_SIZE + 34
        title_font = self._serif(96, weight=800)
        # Optical-baseline nudge: Fraunces sits a bit higher in the bbox
        # than Bricolage, so we hand-tune the y offset rather than rely
        # on the bbox top alone.
        draw.text(
            (title_x, y_top - 14),
            "LEADERBOARD",
            font=title_font,
            fill=TEXT_PRIMARY,
        )

        # Server name (clipped if very long). Bricolage SemiBold.
        subtitle_font = self._sans(32, weight=600)
        subtitle = _truncate(
            self.guild_name,
            subtitle_font,
            CANVAS_W - title_x - H_PAD - 40,
        )
        draw.text(
            (title_x, y_top + 116),
            subtitle,
            font=subtitle_font,
            fill=TEXT_SECONDARY,
        )

        # Bottom hairline of the header.
        line_y = HEADER_H - 1
        draw.line([(H_PAD, line_y), (CANVAS_W - H_PAD, line_y)], fill=KEYLINE, width=1)

        return HEADER_H

    # ---------- adaptive name-fit ----------

    def _fit_name(
        self,
        name: str,
        max_width: int,
        size_bounds: tuple[int, int],
        *,
        family: str = "sans",
        weight: int = 700,
    ) -> tuple[ImageFont.FreeTypeFont, str]:
        """Pick the largest weight-``weight`` size in ``size_bounds`` that
        lets ``name`` fit in ``max_width``. ``family`` is "sans"
        (Bricolage) or "serif" (Fraunces). If even the minimum size
        overflows, returns the min-size font and an ellipsized name.
        """
        loader = self._serif if family == "serif" else self._sans
        min_size, max_size = size_bounds
        for size in range(max_size, min_size - 1, -2):
            font = loader(size, weight=weight)
            bbox = font.getbbox(name)
            if bbox[2] - bbox[0] <= max_width:
                return font, name
        font = loader(min_size, weight=weight)
        return font, _truncate(name, font, max_width)

    # ---------- podium rows (ranks 1..3) ----------

    def _draw_podium_row(
        self, canvas: Image.Image, entry: LeaderboardEntry, y: int
    ) -> int:
        dims = _PODIUM_DIMS[entry.rank]
        row_h = dims["row_h"]
        medal_color = {1: GOLD, 2: SILVER, 3: BRONZE}[entry.rank]
        avatar_size = dims["avatar"]

        _draw_row_card(canvas, y, row_h, fill=SURFACE_TOP3, keyline=KEYLINE)
        _draw_left_stripe(canvas, y, row_h, color=medal_color)

        draw = ImageDraw.Draw(canvas, "RGBA")

        # Ordinal label — Fraunces, medal-coloured, podium-scaled.
        ordinal_text = _ordinal(entry.rank)
        rank_font = self._serif(dims["ordinal_size"], weight=800)
        rb = rank_font.getbbox(ordinal_text)
        rh = rb[3] - rb[1]
        rank_x = H_PAD + STRIPE_INSET + STRIPE_WIDTH + 22
        rank_y = y + (row_h - rh) // 2 - rb[1]
        draw.text((rank_x, rank_y), ordinal_text, font=rank_font, fill=medal_color)

        # Avatar.
        rank_w = rb[2] - rb[0]
        ax = rank_x + rank_w + 40
        ay = y + (row_h - avatar_size) // 2
        if entry.avatar_bytes is not None:
            avatar = _circular_image(entry.avatar_bytes, avatar_size)
        else:
            avatar = _avatar_placeholder(avatar_size, medal_color)
        canvas.alpha_composite(avatar, (ax, ay))
        ring_inset = 3
        ring_size = avatar_size + 2 * ring_inset
        ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, ring_size - 1, ring_size - 1),
            outline=medal_color[:3] + (200,),
            width=2,
        )
        canvas.alpha_composite(ring, (ax - ring_inset, ay - ring_inset))

        # Right-anchored Level tag — Bricolage, medal-coloured.
        level_font = self._sans(dims["level_size"], weight=700)
        level_text = f"Level {entry.level}"
        level_w = _text_width(draw, level_text, level_font)
        level_x = CANVAS_W - H_PAD - 32 - level_w
        lb = level_font.getbbox(level_text)
        lh = lb[3] - lb[1]
        level_y = y + (row_h - lh) // 2 - lb[1]
        draw.text((level_x, level_y), level_text, font=level_font, fill=medal_color)

        # Name — Bricolage sans-serif (matches the rest of the field).
        text_x = ax + avatar_size + 36
        name_max = level_x - text_x - 40
        name_font, name = self._fit_name(
            entry.display_name, name_max, dims["name_fit"], family="sans", weight=700
        )
        nb = name_font.getbbox(name)
        nh = nb[3] - nb[1]
        ny = y + (row_h - nh) // 2 - nb[1]
        draw.text((text_x, ny), name, font=name_font, fill=TEXT_PRIMARY)

        return y + row_h

    # ---------- standard rows (ranks 4..10) ----------

    def _draw_standard_row(
        self, canvas: Image.Image, entry: LeaderboardEntry, y: int
    ) -> int:
        row_h = STANDARD_ROW_H
        _draw_row_card(canvas, y, row_h, fill=SURFACE_STD, keyline=KEYLINE)

        draw = ImageDraw.Draw(canvas, "RGBA")

        # Rank (Bricolage; serif is reserved for the podium).
        rank_font = self._sans(56, weight=700)
        rank_text = _ordinal(entry.rank)
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

        # Right-anchored Level tag — bigger, brighter.
        level_font = self._sans(32, weight=700)
        level_text = f"Level {entry.level}"
        level_w = _text_width(draw, level_text, level_font)
        level_x = CANVAS_W - H_PAD - 28 - level_w
        lb = level_font.getbbox(level_text)
        lh = lb[3] - lb[1]
        level_y = y + (row_h - lh) // 2 - lb[1]
        draw.text((level_x, level_y), level_text, font=level_font, fill=TEXT_PRIMARY)

        # Name — Bricolage, adaptive, fills the middle.
        name_x = ax + AVATAR_STD + 28
        name_max = level_x - name_x - 32
        name_font, name = self._fit_name(entry.display_name, name_max, NAME_FIT_STD)
        nb = name_font.getbbox(name)
        nh = nb[3] - nb[1]
        ny = y + (row_h - nh) // 2 - nb[1]
        draw.text((name_x, ny), name, font=name_font, fill=TEXT_PRIMARY)

        return y + row_h

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


def _ordinal(n: int) -> str:
    """Return ``n`` as an English ordinal string: 1 -> 1st, 2 -> 2nd,
    3 -> 3rd, 4 -> 4th, ..., 11 -> 11th, 21 -> 21st, etc.
    """
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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
