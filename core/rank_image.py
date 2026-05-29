"""PNG renderer for the ``/rank`` slash command.

Sibling of :mod:`core.leaderboard_image` — same fonts, same atmospheric
background, same dark-aubergine palette — but composed as a single-
member "trophy card" rather than a ranked list.

Composition (ID-card layout)
----------------------------
* Top stripe: server icon + name on the left, a small "MEMBER CARD"
  tag on the right. (The rank position used to live here; it now
  lives in the stats grid below alongside the other fields.)
* Hero block: a large circular avatar with a tier-coloured glow ring
  and hairline ring; right of it the display name, the tier label, and
  the huge Fraunces ``LEVEL <n>`` figure as the visual hero.
* Stats grid: four boxed cells (RANK / JOINED SERVER / JOINED DISCORD
  / NEXT TIER), each with a rounded rectangle border and a tier-
  coloured corner accent — the ID-card "field" treatment.
* Footer block: a progress bar showing progress within the current
  level (filled in tier colour, with a soft inner glow) and a caption
  reading ``"<pct>% to Level <n+1>"`` (or ``"MAX LEVEL"`` at 100).

Tier colour drives the personality of each card. Freshie cards are soft
lavender, Yappatrons hot pink, Aura cards pure gold, etc. The accent
ripples through the glow ring, the rank chip, the LEVEL numeral, and
the progress-bar fill — so the whole card feels coordinated even though
every user sees a different one.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from core.leaderboard_image import (
    TEXT_DIM,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    make_atmospheric_background,
)
from core.leveling import (
    LEVEL_CAP,
    cumulative_xp_to_level,
    get_next_tier_for_level,
    get_tier_for_level,
)


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_SANS_PATH = _ASSETS_DIR / "BricolageGrotesque-VariableFont.ttf"
_FONT_SERIF_PATH = _ASSETS_DIR / "Fraunces-VariableFont.ttf"


# ---------------------------------------------------------------------------
# Tier accent colours
# ---------------------------------------------------------------------------

# Each tier gets a distinct accent that drives the card's personality.
# Tuned to read as bright on the deep-aubergine background.
TIER_COLORS: dict[str, tuple[int, int, int]] = {
    "Freshie": (199, 175, 255),     # soft lavender
    "Viber": (244, 114, 182),       # vibrant magenta
    "Innovator": (94, 234, 212),    # cyan-teal
    "Hustler": (251, 146, 60),      # warm orange
    "Yapper": (244, 63, 152),       # electric pink
    "Chud": (190, 242, 100),        # acid green-yellow
    "Yappatron": (250, 100, 180),   # hot pink-magenta
    "Challenger": (255, 99, 71),    # tomato red
    "Ascendant": (252, 211, 77),    # amber-gold
    "Aura": (255, 215, 0),          # pure trophy gold
}
# Fallback for level 0 (no tier yet).
_DEFAULT_ACCENT: tuple[int, int, int] = (199, 175, 255)


def _accent_for(level: int) -> tuple[int, int, int]:
    tier = get_tier_for_level(level)
    if tier is None:
        return _DEFAULT_ACCENT
    return TIER_COLORS.get(tier, _DEFAULT_ACCENT)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CANVAS_W = 1200
CANVAS_H = 780
H_PAD = 56

SERVER_ICON_SIZE = 64
SERVER_ICON_RADIUS = 14

AVATAR_SIZE = 248

PROGRESS_BAR_HEIGHT = 22
PROGRESS_BAR_RADIUS = 11

# Stats grid — four ID-card boxes in one horizontal row.
STATS_STRIP_Y = 504
STATS_STRIP_H = 108
STATS_CELL_GAP = 18


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankCardData:
    """All the inputs the renderer needs for one /rank card."""

    display_name: str
    avatar_bytes: Optional[bytes]
    guild_name: str
    guild_icon_bytes: Optional[bytes]
    rank: int
    total_members: int
    level: int
    total_xp: int
    # Stats-strip fields (added in the appealing-card pass).
    joined_at: Optional[datetime] = None      # member.joined_at
    account_created_at: Optional[datetime] = None  # member.created_at
    text_xp_total: int = 0
    voice_xp_total: int = 0


def render_rank_png(data: RankCardData) -> bytes:
    """Render the rank card to PNG bytes. Blocks ~50-150 ms; run in
    an executor from async contexts."""
    return _RankRenderer(data).render()


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class _RankRenderer:
    def __init__(self, data: RankCardData) -> None:
        self.data = data
        self.accent = _accent_for(data.level)
        self._fonts: dict[str, ImageFont.FreeTypeFont] = {}

    def render(self) -> bytes:
        # Tier-tinted atmospheric background — three orbs, one of which
        # is the accent colour so each card has a unique mood.
        canvas = make_atmospheric_background(
            CANVAS_W,
            CANVAS_H,
            orbs=[
                # Accent-tinted top-left orb (the card's personality).
                (
                    int(CANVAS_W * 0.04),
                    int(CANVAS_H * 0.08),
                    int(CANVAS_W * 0.50),
                    self.accent,
                    78,
                    150,
                ),
                # Magenta lower-right haze.
                (
                    int(CANVAS_W * 1.02),
                    int(CANVAS_H * 0.95),
                    int(CANVAS_W * 0.55),
                    (244, 114, 182),
                    52,
                    180,
                ),
                # Deep violet centre to anchor the eye.
                (
                    int(CANVAS_W * 0.50),
                    int(CANVAS_H * 0.55),
                    int(CANVAS_W * 0.30),
                    (90, 60, 160),
                    35,
                    200,
                ),
            ],
        )

        self._draw_top_stripe(canvas)
        self._draw_hero(canvas)
        self._draw_stats_strip(canvas)
        self._draw_progress(canvas)

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # ---------- fonts ----------

    def _sans(self, size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
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

    # ---------- top stripe ----------

    def _draw_top_stripe(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        y = 40

        # Server icon.
        if self.data.guild_icon_bytes is not None:
            icon = _rounded_square(
                self.data.guild_icon_bytes,
                SERVER_ICON_SIZE,
                SERVER_ICON_RADIUS,
            )
            canvas.alpha_composite(icon, (H_PAD, y))
            border = Image.new(
                "RGBA", (SERVER_ICON_SIZE, SERVER_ICON_SIZE), (0, 0, 0, 0)
            )
            ImageDraw.Draw(border).rounded_rectangle(
                (0, 0, SERVER_ICON_SIZE - 1, SERVER_ICON_SIZE - 1),
                radius=SERVER_ICON_RADIUS,
                outline=(255, 255, 255, 50),
                width=2,
            )
            canvas.alpha_composite(border, (H_PAD, y))
            text_x = H_PAD + SERVER_ICON_SIZE + 18
        else:
            text_x = H_PAD

        # Server name — Bricolage SemiBold, uppercased.
        name_font = self._sans(26, weight=700)
        draw.text(
            (text_x, y + 18),
            self.data.guild_name.upper(),
            font=name_font,
            fill=TEXT_SECONDARY,
        )

        # Right side: a small "MEMBER CARD" tag (ID-card flair).
        tag_font = self._sans(20, weight=700)
        tag_text = "MEMBER CARD"
        tag_w = _text_width(draw, tag_text, tag_font)
        tag_x0 = CANVAS_W - H_PAD - tag_w - 28
        tag_y0 = y + 16
        # Tier-accent pill chip.
        chip_h = 36
        chip_x1 = CANVAS_W - H_PAD
        chip_w = tag_w + 28
        chip_x0 = chip_x1 - chip_w
        chip_y0 = tag_y0 - 4
        chip_y1 = chip_y0 + chip_h
        draw.rounded_rectangle(
            (chip_x0, chip_y0, chip_x1, chip_y1),
            radius=chip_h // 2,
            outline=self.accent + (200,),
            width=2,
        )
        draw.text(
            (chip_x0 + 14, chip_y0 + 6),
            tag_text,
            font=tag_font,
            fill=self.accent + (255,),
        )

        # Hairline divider below the stripe.
        line_y = 130
        draw.line(
            [(H_PAD, line_y), (CANVAS_W - H_PAD, line_y)],
            fill=(160, 134, 215, 60),
            width=1,
        )

    # ---------- hero ----------

    def _draw_hero(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        # Avatar position.
        ax = H_PAD
        ay = 160

        # Soft glow halo behind avatar in accent colour.
        glow = Image.new(
            "RGBA", (AVATAR_SIZE + 80, AVATAR_SIZE + 80), (0, 0, 0, 0)
        )
        ImageDraw.Draw(glow).ellipse(
            (10, 10, AVATAR_SIZE + 70, AVATAR_SIZE + 70),
            fill=self.accent + (90,),
        )
        glow = glow.filter(ImageFilter.GaussianBlur(28))
        canvas.alpha_composite(glow, (ax - 40, ay - 40))

        # Avatar.
        if self.data.avatar_bytes is not None:
            avatar = _circular(self.data.avatar_bytes, AVATAR_SIZE)
        else:
            avatar = _avatar_placeholder(AVATAR_SIZE, self.accent)
        canvas.alpha_composite(avatar, (ax, ay))

        # Crisp hairline ring around the avatar in accent colour.
        ring = Image.new(
            "RGBA", (AVATAR_SIZE + 8, AVATAR_SIZE + 8), (0, 0, 0, 0)
        )
        ImageDraw.Draw(ring).ellipse(
            (0, 0, AVATAR_SIZE + 7, AVATAR_SIZE + 7),
            outline=self.accent + (235,),
            width=3,
        )
        canvas.alpha_composite(ring, (ax - 4, ay - 4))

        # Right-side identity block.
        rx = ax + AVATAR_SIZE + 56
        # Display name — Bricolage Black, sized to fit.
        name_max_w = CANVAS_W - rx - H_PAD - 24
        name_font, name_text = self._fit_text(
            self.data.display_name, name_max_w, (40, 72), weight=800
        )
        # y placement: align top with avatar's upper third.
        draw.text((rx, ay + 0), name_text, font=name_font, fill=TEXT_PRIMARY)

        nb = name_font.getbbox(name_text)
        name_h = nb[3] - nb[1]

        # Tier label — Fraunces small caps feel (real caps + tracking).
        tier = get_tier_for_level(self.data.level) or "UNRANKED"
        tier_label_font = self._serif(26, weight=700)
        tier_text = tier.upper()
        # Place under the name with a hairline strut on the left in accent.
        tier_y = ay + name_h + 30
        strut_y0 = tier_y + 6
        strut_y1 = tier_y + 28
        draw.line(
            [(rx, strut_y0), (rx, strut_y1)],
            fill=self.accent + (255,),
            width=3,
        )
        draw.text(
            (rx + 16, tier_y),
            tier_text,
            font=tier_label_font,
            fill=self.accent + (255,),
        )

        # "LEVEL" mini-label then giant Fraunces numeral.
        lvl_label_font = self._sans(22, weight=700)
        lvl_label_text = "LEVEL"
        lvl_label_y = tier_y + 64
        draw.text(
            (rx, lvl_label_y),
            lvl_label_text,
            font=lvl_label_font,
            fill=TEXT_DIM,
        )

        big_level_font = self._serif(132, weight=800)
        big_level_text = str(self.data.level)
        # Place tight under the label.
        draw.text(
            (rx - 4, lvl_label_y + 26),
            big_level_text,
            font=big_level_font,
            fill=self.accent + (255,),
        )

    # ---------- progress ----------

    # ---------- stats strip ----------

    def _draw_stats_strip(self, canvas: Image.Image) -> None:
        """Four-cell stat row: joined server, joined Discord, activity
        mix, next tier preview. Each cell has a tiny accent strut on
        the left, an uppercase dim label, and a bright primary value."""
        cells = self._build_stat_cells()
        if not cells:
            return

        usable_w = CANVAS_W - 2 * H_PAD - (len(cells) - 1) * STATS_CELL_GAP
        cell_w = usable_w // len(cells)

        label_font = self._sans(16, weight=700)
        value_font = self._sans(28, weight=700)

        for i, (label, value) in enumerate(cells):
            cx = H_PAD + i * (cell_w + STATS_CELL_GAP)
            self._draw_one_stat_cell(
                canvas, cx, STATS_STRIP_Y, cell_w, label, value,
                label_font=label_font, value_font=value_font,
            )

    def _build_stat_cells(self) -> list[tuple[str, str]]:
        """Return the (label, value) pairs for the ID-card stats grid.

        ACTIVITY was removed at user request; RANK moved here from the
        top stripe so the bottom row reads like a passport's field grid.
        """
        d = self.data

        def _month_year(dt: Optional[datetime]) -> str:
            if dt is None:
                return "—"
            return dt.strftime("%b %Y").upper()

        next_tier = get_next_tier_for_level(d.level)
        next_tier_label = next_tier.upper() if next_tier else "MAX TIER"

        rank_value = f"#{d.rank} / {d.total_members}"

        return [
            ("RANK", rank_value),
            ("JOINED SERVER", _month_year(d.joined_at)),
            ("JOINED DISCORD", _month_year(d.account_created_at)),
            ("NEXT TIER", next_tier_label),
        ]

    def _draw_one_stat_cell(
        self,
        canvas: Image.Image,
        x: int,
        y: int,
        width: int,
        label: str,
        value: str,
        *,
        label_font: ImageFont.FreeTypeFont,
        value_font: ImageFont.FreeTypeFont,
    ) -> None:
        """Render one ID-card field: rounded-rectangle box with a soft
        fill, a hairline tier-accent border, a tier-accent corner notch,
        the uppercase dim label, and the bright primary value.

        The fill is drawn on a separate RGBA layer and alpha-composited
        — drawing it directly on the canvas would overwrite pixels
        instead of blending, which renders the box opaque-white.
        """
        # Soft tinted fill on its own layer so alpha actually blends.
        fill_layer = Image.new("RGBA", (width, STATS_STRIP_H), (0, 0, 0, 0))
        ImageDraw.Draw(fill_layer).rounded_rectangle(
            (0, 0, width - 1, STATS_STRIP_H - 1),
            radius=14,
            fill=(255, 255, 255, 18),
        )
        canvas.alpha_composite(fill_layer, (x, y))

        draw = ImageDraw.Draw(canvas, "RGBA")
        x1 = x + width
        y1 = y + STATS_STRIP_H

        # Hairline border in the tier accent.
        draw.rounded_rectangle(
            (x, y, x1 - 1, y1 - 1),
            radius=14,
            outline=self.accent + (200,),
            width=2,
        )
        # Tier-accent corner notch (top-left): a tiny square that reads
        # as the "photo-ID corner" tell.
        notch = 8
        draw.rectangle(
            (x + 16, y + 16, x + 16 + notch, y + 16 + notch),
            fill=self.accent + (255,),
        )
        # Label sits to the right of the notch, baseline-aligned with it.
        draw.text(
            (x + 16 + notch + 8, y + 12),
            label,
            font=label_font,
            fill=TEXT_DIM,
        )
        # Value sits in the lower half of the box.
        value = _truncate(value, value_font, width - 36)
        draw.text(
            (x + 18, y + 56),
            value,
            font=value_font,
            fill=TEXT_PRIMARY,
        )

    def _draw_progress(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        bar_x = H_PAD
        bar_y = CANVAS_H - 88
        bar_w = CANVAS_W - 2 * H_PAD
        bar_h = PROGRESS_BAR_HEIGHT

        # Compute fill ratio.
        if self.data.level >= LEVEL_CAP:
            ratio = 1.0
            caption = "MAX LEVEL"
        else:
            level_floor = cumulative_xp_to_level(self.data.level)
            next_floor = cumulative_xp_to_level(self.data.level + 1)
            span = max(1, next_floor - level_floor)
            into = max(0, self.data.total_xp - level_floor)
            ratio = min(1.0, max(0.0, into / span))
            pct = int(round(ratio * 100))
            caption = f"{pct}% TO LEVEL {self.data.level + 1}"

        # Track (background of the bar).
        ImageDraw.Draw(canvas, "RGBA").rounded_rectangle(
            (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
            radius=PROGRESS_BAR_RADIUS,
            fill=(255, 255, 255, 28),
        )
        # Fill.
        fill_w = int(bar_w * ratio)
        if fill_w >= 4:
            # Soft outer glow behind the fill — subtle, matches accent.
            glow = Image.new(
                "RGBA", (fill_w + 80, bar_h + 80), (0, 0, 0, 0)
            )
            ImageDraw.Draw(glow).rounded_rectangle(
                (40, 40, 40 + fill_w, 40 + bar_h),
                radius=PROGRESS_BAR_RADIUS,
                fill=self.accent + (100,),
            )
            glow = glow.filter(ImageFilter.GaussianBlur(16))
            canvas.alpha_composite(glow, (bar_x - 40, bar_y - 40))
            ImageDraw.Draw(canvas, "RGBA").rounded_rectangle(
                (bar_x, bar_y, bar_x + fill_w, bar_y + bar_h),
                radius=PROGRESS_BAR_RADIUS,
                fill=self.accent + (255,),
            )

        # Caption beneath the bar.
        cap_font = self._sans(24, weight=700)
        cb = cap_font.getbbox(caption)
        cap_y = bar_y + bar_h + 18
        draw.text(
            (bar_x, cap_y),
            caption,
            font=cap_font,
            fill=TEXT_SECONDARY,
        )

    # ---------- text-fit helper ----------

    def _fit_text(
        self,
        text: str,
        max_width: int,
        size_bounds: tuple[int, int],
        *,
        weight: int = 700,
    ) -> tuple[ImageFont.FreeTypeFont, str]:
        """Pick the largest sans font in ``size_bounds`` that lets
        ``text`` fit ``max_width``, else min-size + ellipsis."""
        min_size, max_size = size_bounds
        for size in range(max_size, min_size - 1, -2):
            font = self._sans(size, weight=weight)
            bbox = font.getbbox(text)
            if bbox[2] - bbox[0] <= max_width:
                return font, text
        font = self._sans(min_size, weight=weight)
        return font, _truncate(text, font, max_width)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _circular(image_bytes: bytes, size: int) -> Image.Image:
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


def _rounded_square(image_bytes: bytes, size: int, radius: int) -> Image.Image:
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
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=(40, 24, 70, 255))
    d.ellipse(
        (4, 4, size - 4, size - 4),
        outline=accent[:3] + (200,),
        width=3,
    )
    return img


def _text_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
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
