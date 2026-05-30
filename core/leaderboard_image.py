"""Podium-pill leaderboard image for the ``/leaderboard`` command.

Design language
---------------
A bold editorial "podium" composition rendered on a deep aubergine
backdrop:

* A lowercase, heavily-slanted ``leaderboard`` masthead in the top-left
  (Bricolage Grotesque, simulated oblique).
* The top three sit in big rounded capsule "pills" arranged in a
  descending staircase — 1st is a gold→amber gradient (shifted right and
  largest), 2nd an indigo gradient, 3rd a lighter violet gradient. Each
  pill carries the member's display name in a slanted serif (Fraunces).
* Ranks 4-10 live in a translucent purple side panel to the right, one
  name per row, top-to-bottom = rank 4→10. Vertical order conveys the
  rank — there are no numbers, matching the source design.
* If the viewer is in the top 10 their pill/row is subtly highlighted
  (a hairline ring on a pill, a brighter band on a side row).

Both typefaces are variable fonts shipped in ``assets/fonts``; italics
are simulated with a horizontal shear so the look matches the mockup
even though neither font ships an italic axis.

Everything is rendered at 2x logical resolution for retina-sharp output
on Discord, then returned as PNG bytes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_SANS_PATH = _ASSETS_DIR / "BricolageGrotesque-VariableFont.ttf"
_FONT_SERIF_PATH = _ASSETS_DIR / "Fraunces-VariableFont.ttf"

# ---------------------------------------------------------------------------
# Canvas + layout
# ---------------------------------------------------------------------------
CANVAS_W = 1200
CANVAS_H = 1080

# Slant applied to all "italic" text (top leans right by SLANT * height).
SLANT = 0.20

# Pill bounding boxes (x0, y0, x1, y1) for ranks 1-3 — stacked vertically
# down the left of the canvas. 1st is on top and largest; 2nd and 3rd step
# down in size below it.
PILL_BOXES = {
    1: (72, 250, 612, 452),
    2: (72, 492, 560, 662),
    3: (72, 700, 512, 850),
}

# Side panel (ranks 4-10) — sits in the bottom-right quadrant beside the
# stacked pills.
SIDE_X0 = 668
SIDE_X1 = 1128
SIDE_Y0 = 470
SIDE_Y1 = 1040

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
# Pill gradients: (left colour, right colour).
GRAD_FIRST = ((247, 203, 118), (193, 124, 52))   # gold → amber
GRAD_SECOND = ((124, 100, 232), (72, 58, 166))    # indigo
GRAD_THIRD = ((158, 128, 240), (101, 80, 198))    # lighter violet
PILL_GRADS = {1: GRAD_FIRST, 2: GRAD_SECOND, 3: GRAD_THIRD}

# Names are white on every pill (matches the reference).
PILL_TEXT = {1: (255, 255, 255), 2: (255, 255, 255), 3: (255, 255, 255)}
# Avatar ring colour per pill (a touch of the medal tone).
PILL_RING = {1: (120, 74, 18), 2: (40, 28, 96), 3: (60, 44, 120)}

# Side panel.
SIDE_FILL = (108, 84, 188, 235)        # solid violet block (matches mock)
SIDE_DIVIDER = (255, 255, 255, 30)     # hairline between rows
SIDE_HIGHLIGHT = (255, 255, 255, 64)   # viewer's row band
SIDE_TEXT = (238, 232, 255)
SIDE_RANK_TEXT = (210, 198, 245)       # dim rank numeral on each row

# Highlight (viewer) accent.
ACCENT_HIGHLIGHT = (255, 255, 255)

TITLE_COLOR = (244, 240, 252)

# Shared with core.rank_image (kept stable for that module's import).
BG_TOP = (14, 7, 23)
BG_BOTTOM = (27, 14, 50)
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (215, 203, 240, 255)
TEXT_DIM = (160, 142, 200, 255)


class _FontBook:
    """Lazily-loaded, size-and-weight-cached variable font accessor."""

    def __init__(self) -> None:
        self._fonts: dict[str, ImageFont.FreeTypeFont] = {}

    def _load(self, path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
        font = ImageFont.truetype(str(path), size=size)
        try:
            font.set_variation_by_axes([weight])
        except Exception:
            pass
        return font

    def sans(self, size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
        key = f"sans:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = self._load(_FONT_SANS_PATH, size, weight)
        self._fonts[key] = font
        return font

    def serif(self, size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
        key = f"serif:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = self._load(_FONT_SERIF_PATH, size, weight)
        self._fonts[key] = font
        return font


_fonts = _FontBook()


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    display_name: str
    level: int
    xp: int
    avatar_bytes: bytes | None


# ---------------------------------------------------------------------------
# Slanted ("italic") text
# ---------------------------------------------------------------------------
def _slanted_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    slant: float = SLANT,
) -> Image.Image:
    """Render ``text`` and shear it so the top leans right (faux italic).

    Returns a tightly-cropped RGBA image."""
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = tmp.textbbox((0, 0), text, font=font)
    tw = max(bbox[2] - bbox[0], 1)
    th = max(bbox[3] - bbox[1], 1)
    pad = 8
    base = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(base).text(
        (pad - bbox[0], pad - bbox[1]), text, font=font, fill=fill + (255,)
    )
    h = base.height
    extra = int(abs(slant) * h) + 1
    sheared = base.transform(
        (base.width + extra, h),
        Image.AFFINE,
        (1, slant, -slant * h, 0, 1, 0),
        resample=Image.BICUBIC,
    )
    return sheared.crop(sheared.getbbox() or (0, 0, 1, 1))


def _fit_name(
    text: str,
    fill: tuple[int, int, int],
    *,
    max_w: int,
    max_size: int,
    min_size: int,
    weight: int = 700,
) -> Image.Image:
    """Slanted bold sans name shrunk to fit ``max_w``; ellipsised if needed.

    All names on the board (pills + side column) use the same Bricolage
    sans face as the masthead, faux-italicised, to match the reference."""
    size = max_size
    while size >= min_size:
        img = _slanted_text(text, _fonts.sans(size, weight=weight), fill)
        if img.width <= max_w:
            return img
        size -= 3
    # Still too wide at the minimum size — truncate with an ellipsis.
    font = _fonts.sans(min_size, weight=weight)
    trimmed = text
    while trimmed and _slanted_text(trimmed + "…", font, fill).width > max_w:
        trimmed = trimmed[:-1]
    return _slanted_text((trimmed + "…") if trimmed else "…", font, fill)


def _paste_v_centre(canvas: Image.Image, img: Image.Image, x: int, cy: int) -> None:
    canvas.paste(img, (x, cy - img.height // 2), img)


def _circular(image_bytes: bytes, size: int) -> Image.Image:
    """Decode raw image bytes and crop to a circle at ``size`` px."""
    img = (
        Image.open(io.BytesIO(image_bytes))
        .convert("RGBA")
        .resize((size, size), Image.LANCZOS)
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _circle_placeholder(size: int, accent: tuple[int, int, int]) -> Image.Image:
    """Circular fallback avatar when a member has no avatar bytes."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=(38, 24, 64, 255))
    d.ellipse(
        (3, 3, size - 4, size - 4), outline=accent + (150,), width=2
    )
    return img


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------
def _draw_background(width: int, height: int) -> Image.Image:
    """Deep aubergine vertical gradient with a soft diagonal light streak."""
    top = (20, 11, 31)
    bottom = (11, 6, 19)
    column = Image.new("RGB", (1, height))
    cpx = column.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        cpx[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    base = column.resize((width, height)).convert("RGBA")

    # Atmospheric glow: a faint diagonal streak + a couple of soft orbs.
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse(
        (int(width * 0.55), int(-height * 0.15),
         int(width * 1.25), int(height * 0.45)),
        fill=(120, 96, 200, 30),
    )
    odraw.ellipse(
        (int(width * -0.1), int(height * 0.6),
         int(width * 0.4), int(height * 1.1)),
        fill=(64, 48, 120, 26),
    )
    overlay = overlay.filter(ImageFilter.GaussianBlur(150))
    return Image.alpha_composite(base, overlay)


def make_atmospheric_background(
    w: int,
    h: int,
    *,
    orbs: list[tuple[int, int, int, tuple, int, int]],
) -> Image.Image:
    """Deep-aubergine gradient canvas with floating glowing orbs.

    Each orb is ``(cx, cy, radius, rgb, alpha, blur)``. Kept here because
    :mod:`core.rank_image` shares this helper for its own backdrop.
    """
    base = Image.new("RGBA", (w, h), BG_TOP + (255,))
    draw = ImageDraw.Draw(base)
    for y in range(h):
        t = y / max(1, h - 1)
        t2 = t * t * (3 - 2 * t)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t2)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t2)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t2)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    for cx, cy, radius, rgb, alpha, blur in orbs:
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=rgb[:3] + (alpha,),
        )
        if blur > 0:
            layer = layer.filter(ImageFilter.GaussianBlur(blur))
        base = Image.alpha_composite(base, layer)

    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_png(
    *,
    guild_name: str,
    guild_icon_bytes: bytes | None,
    entries: list[LeaderboardEntry],
    rendered_at=None,
    highlight_rank: int | None = None,
) -> bytes:
    return _Renderer(
        entries=entries,
        highlight_rank=highlight_rank,
    ).render()


class _Renderer:
    def __init__(
        self,
        *,
        entries: list[LeaderboardEntry],
        highlight_rank: int | None,
    ) -> None:
        self.entries = sorted(entries, key=lambda e: e.rank)
        self.highlight_rank = highlight_rank
        self._fonts = _fonts

    def render(self) -> bytes:
        canvas = _draw_background(CANVAS_W, CANVAS_H)
        self._draw_title(canvas)

        by_rank = {e.rank: e for e in self.entries}
        for rank in (1, 2, 3):
            entry = by_rank.get(rank)
            if entry is not None:
                self._draw_pill(canvas, entry)

        side = [e for e in self.entries if 4 <= e.rank <= 10]
        if side:
            self._draw_side_panel(canvas, side)

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # -- title -----------------------------------------------------------
    def _draw_title(self, canvas: Image.Image) -> None:
        img = _slanted_text(
            "leaderboard", self._fonts.sans(104, weight=800), TITLE_COLOR
        )
        canvas.paste(img, (52, 48), img)

    # -- podium pills ----------------------------------------------------
    def _draw_pill(self, canvas: Image.Image, entry: LeaderboardEntry) -> None:
        x0, y0, x1, y1 = PILL_BOXES[entry.rank]
        w, h = x1 - x0, y1 - y0
        radius = h // 2
        c_left, c_right = PILL_GRADS[entry.rank]

        # Horizontal gradient masked to a capsule.
        row = Image.new("RGB", (w, 1))
        rpx = row.load()
        for x in range(w):
            t = x / max(w - 1, 1)
            rpx[x, 0] = (
                int(c_left[0] + (c_right[0] - c_left[0]) * t),
                int(c_left[1] + (c_right[1] - c_left[1]) * t),
                int(c_left[2] + (c_right[2] - c_left[2]) * t),
            )
        pill = row.resize((w, h)).convert("RGBA")
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=radius, fill=255
        )
        pill.putalpha(mask)
        canvas.paste(pill, (x0, y0), pill)

        # Viewer highlight: a hairline ring around the capsule.
        if self.highlight_rank == entry.rank:
            ring = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle(
                (2, 2, w - 3, h - 3),
                radius=radius - 2,
                outline=ACCENT_HIGHLIGHT + (235,),
                width=5,
            )
            canvas.paste(ring, (x0, y0), ring)

        # Circular avatar tucked into the pill's rounded left cap.
        cy = (y0 + y1) // 2
        av_d = int(h * 0.72)
        av_x = x0 + radius - av_d // 2
        av_y = cy - av_d // 2
        if entry.avatar_bytes is not None:
            avatar = _circular(entry.avatar_bytes, av_d)
        else:
            avatar = _circle_placeholder(av_d, PILL_TEXT[entry.rank])
        canvas.paste(avatar, (av_x, av_y), avatar)
        ring = Image.new("RGBA", (av_d, av_d), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (1, 1, av_d - 2, av_d - 2),
            outline=PILL_RING[entry.rank] + (180,),
            width=3,
        )
        canvas.paste(ring, (av_x, av_y), ring)

        # Display name (slanted bold sans), vertically centred after avatar.
        text_x = av_x + av_d + int(h * 0.16)
        max_w = (x1 - text_x) - int(radius * 0.55)
        max_size = {1: 96, 2: 76, 3: 68}[entry.rank]
        name = _fit_name(
            entry.display_name,
            PILL_TEXT[entry.rank],
            max_w=max_w,
            max_size=max_size,
            min_size=32,
            weight=780,
        )
        _paste_v_centre(canvas, name, text_x, cy)

    # -- side panel (ranks 4-10) -----------------------------------------
    def _draw_side_panel(
        self, canvas: Image.Image, side: list[LeaderboardEntry]
    ) -> None:
        n = len(side)
        row_h = (SIDE_Y1 - SIDE_Y0) // 7  # rows sized for a full 4-10 field
        panel_h = row_h * n
        radius = 28

        # The panel + every row tint is built on one transparent overlay,
        # then alpha-composited once. ImageDraw on an RGBA canvas does not
        # blend low-alpha fills the way alpha_composite does, so going
        # through an overlay is what keeps the tints translucent.
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            (SIDE_X0, SIDE_Y0, SIDE_X1, SIDE_Y0 + panel_h),
            radius=radius,
            fill=SIDE_FILL,
        )

        # Viewer highlight: a brighter band on the matching row, clipped to
        # the panel's rounded silhouette so corners stay clean.
        panel_mask = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(panel_mask).rounded_rectangle(
            (SIDE_X0, SIDE_Y0, SIDE_X1, SIDE_Y0 + panel_h),
            radius=radius,
            fill=255,
        )
        for i, entry in enumerate(side):
            if self.highlight_rank != entry.rank:
                continue
            ry0 = SIDE_Y0 + i * row_h
            ry1 = ry0 + row_h
            band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(band).rectangle(
                (SIDE_X0, ry0, SIDE_X1, ry1), fill=SIDE_HIGHLIGHT
            )
            band.putalpha(
                Image.composite(band.getchannel("A"),
                                Image.new("L", canvas.size, 0), panel_mask)
            )
            overlay.alpha_composite(band)

        # Hairline dividers between rows (skip the top edge).
        for i in range(1, n):
            ly = SIDE_Y0 + i * row_h
            od.line(
                [(SIDE_X0 + 18, ly), (SIDE_X1 - 18, ly)],
                fill=SIDE_DIVIDER,
                width=1,
            )

        canvas.alpha_composite(overlay)

        # Row contents: small circular avatar, then the rank numeral, then
        # the name. Names are deliberately small here so the column stays
        # tidy next to the big podium pills.
        pad_x = 18
        av_d = min(int(row_h * 0.62), 52)
        rank_font = self._fonts.sans(28, weight=700)
        for i, entry in enumerate(side):
            ry0 = SIDE_Y0 + i * row_h
            cy = ry0 + row_h // 2

            av_x = SIDE_X0 + pad_x
            av_y = cy - av_d // 2
            if entry.avatar_bytes is not None:
                avatar = _circular(entry.avatar_bytes, av_d)
            else:
                avatar = _circle_placeholder(av_d, SIDE_TEXT[:3])
            canvas.paste(avatar, (av_x, av_y), avatar)

            # Rank numeral, in a fixed gutter after the avatar.
            rank_img = _slanted_text(str(entry.rank), rank_font, SIDE_RANK_TEXT)
            rank_gutter_x = av_x + av_d + 14
            _paste_v_centre(canvas, rank_img, rank_gutter_x, cy)

            text_x = rank_gutter_x + 44
            max_w = (SIDE_X1 - pad_x) - text_x
            name = _fit_name(
                entry.display_name,
                SIDE_TEXT,
                max_w=max_w,
                max_size=28,
                min_size=16,
                weight=700,
            )
            _paste_v_centre(canvas, name, text_x, cy)
