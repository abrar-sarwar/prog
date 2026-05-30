"""Leaderboard image built by compositing onto the design template.

This does NOT redraw the leaderboard from scratch. It loads the actual
``assets/leaderboard_template.png`` (the supplied Canva design) and
overlays live data onto it: each placeholder word ("first", "second",
… "tenth") is covered by reconstructing the pill/row background beneath
it, then the real member name + level + avatar are drawn in its place.
No XP is shown.

Slot geometry (pill boxes, glyph regions, right-column rows) is traced
from the 1080×1080 template; if the template art changes, re-measure the
``_PILLS`` / ``_SIDE_ROWS`` coordinates below.

``make_atmospheric_background`` and the ``TEXT_*`` / ``BG_*`` constants
are kept exported because :mod:`core.rank_image` imports them.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_FONTS_DIR = _ASSETS_DIR / "fonts"
_FONT_SANS_PATH = _FONTS_DIR / "BricolageGrotesque-VariableFont.ttf"
_FONT_SERIF_PATH = _FONTS_DIR / "Fraunces-VariableFont.ttf"
_TEMPLATE_PATH = _ASSETS_DIR / "leaderboard_template.png"

# Faux-italic shear, matching the slant of the template's own lettering.
SLANT = 0.20

# ---------------------------------------------------------------------------
# Slot geometry — traced from the 1080×1080 template.
# ---------------------------------------------------------------------------
# Each pill: the capsule bounds, the glyph band to erase (x0, y0, x1, y1),
# and two candidate "clean" sample rows (above / below the glyphs). The
# erase copies, per column, whichever sample row is actually pill-coloured
# (caps are rounded, so one side can fall on the dark background).
_PILLS = {
    1: {"pill": (380, 220, 878, 539),
        "band": (430, 252, 815, 432), "above": 236, "below": 452},
    2: {"pill": (150, 538, 622, 754),
        "band": (150, 588, 612, 700), "above": 560, "below": 724},
    3: {"pill": (128, 756, 560, 967),
        "band": (130, 814, 480, 918), "above": 782, "below": 944},
}

# Right column rows 4-10. The panel spans x≈794-974; each row's placeholder
# word sits in a measured y-band. Erase samples from the inter-row gaps.
_SIDE_X0 = 794
_SIDE_X1 = 974
# (rank: (band_y0, band_y1)) measured from the template — the white glyph
# extent of each placeholder word.
_SIDE_BANDS = {
    4: (627, 654),
    5: (690, 717),
    6: (757, 783),
    7: (819, 846),
    8: (880, 913),
    9: (944, 970),
    10: (1006, 1032),
}
# Guaranteed glyph-free sample rows (the gaps above/below each word band)
# used to rebuild the panel colour when erasing. Picked from the measured
# inter-row gaps so no sample ever lands on lettering.
_SIDE_SAMPLES = {
    4: (618, 672),
    5: (672, 737),
    6: (737, 801),
    7: (801, 863),
    8: (863, 928),
    9: (928, 988),
    10: (988, 1040),
}

# Text colours.
PILL_TEXT = (255, 255, 255)
PILL_LEVEL = (255, 255, 255)
SIDE_TEXT = (240, 234, 255)
SIDE_LEVEL = (214, 203, 244)
TITLE_COLOR = (244, 240, 252)

# Avatar ring tints per pill (subtle, picked to sit on gold/indigo/violet).
_PILL_RING = {1: (150, 96, 24), 2: (54, 38, 120), 3: (74, 54, 140)}
# Shared left x for all three pill avatars so they align vertically.
_PILL_AVATAR_LEFT = 150

# Highlight (viewer) accent.
ACCENT_HIGHLIGHT = (255, 255, 255)

# ---------------------------------------------------------------------------
# Shared with core.rank_image (kept stable for that module's import).
# ---------------------------------------------------------------------------
BG_TOP = (14, 7, 23)
BG_BOTTOM = (27, 14, 50)
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (215, 203, 240, 255)
TEXT_DIM = (160, 142, 200, 255)


class _FontBook:
    """Lazily-loaded, size-and-weight-cached variable font accessor."""

    def __init__(self) -> None:
        self._fonts: dict[str, ImageFont.FreeTypeFont] = {}

    def sans(self, size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
        # Bricolage Grotesque variable axes, in this fvar order:
        #   [Optical size (12-96), Weight (200-800), Width (75-100)].
        # The masthead in the template is the heavy 800 weight; matching
        # that here is what makes the names read as the same typeface.
        key = f"sans:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = ImageFont.truetype(str(_FONT_SANS_PATH), size=size)
        try:
            font.set_variation_by_axes(
                [min(max(size, 12), 96), max(min(weight, 800), 200), 100]
            )
        except Exception:
            pass
        self._fonts[key] = font
        return font

    def serif(self, size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
        key = f"serif:{size}:{weight}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = ImageFont.truetype(str(_FONT_SERIF_PATH), size=size)
        try:
            font.set_variation_by_axes([min(max(size, 9), 144), weight, 0, 0])
        except Exception:
            pass
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
# Text + image helpers
# ---------------------------------------------------------------------------
def _slanted_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    slant: float = SLANT,
) -> Image.Image:
    """Render ``text`` and shear it so the top leans right (faux italic)."""
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
    weight: int = 760,
) -> Image.Image:
    """Slanted bold-sans name shrunk to fit ``max_w``; ellipsised if needed."""
    size = max_size
    while size >= min_size:
        img = _slanted_text(text, _fonts.sans(size, weight=weight), fill)
        if img.width <= max_w:
            return img
        size -= 3
    font = _fonts.sans(min_size, weight=weight)
    trimmed = text
    while trimmed and _slanted_text(trimmed + "…", font, fill).width > max_w:
        trimmed = trimmed[:-1]
    return _slanted_text((trimmed + "…") if trimmed else "…", font, fill)


def _paste_v_centre(canvas: Image.Image, img: Image.Image, x: int, cy: int) -> None:
    canvas.paste(img, (x, cy - img.height // 2), img)


def _paste_right_v_centre(
    canvas: Image.Image, img: Image.Image, x_right: int, cy: int
) -> None:
    canvas.paste(img, (x_right - img.width, cy - img.height // 2), img)


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
    d.ellipse((3, 3, size - 4, size - 4), outline=accent + (180,), width=3)
    return img


def _avatar_with_ring(
    avatar_bytes: bytes | None, size: int, ring: tuple[int, int, int]
) -> Image.Image:
    if avatar_bytes is not None:
        av = _circular(avatar_bytes, size)
    else:
        av = _circle_placeholder(size, ring)
    out = av.copy()
    ImageDraw.Draw(out).ellipse(
        (1, 1, size - 2, size - 2), outline=ring + (200,), width=3
    )
    return out


def _is_bg(c) -> bool:
    """True for the dark aubergine backdrop (so we don't sample it)."""
    return (c[0] + c[1] + c[2]) < 95


def _median_sample(px, x: int, y: int, span: int = 5):
    """Median RGB of a short vertical run centred on ``(x, y)``.

    Taking a median over a few rows (rather than copying one pixel) avoids
    propagating a single noisy/edge pixel into a visible vertical streak
    when we fill the glyph band below.
    """
    rs, gs, bs = [], [], []
    half = span // 2
    for dy in range(-half, half + 1):
        c = px[x, y + dy]
        rs.append(c[0]); gs.append(c[1]); bs.append(c[2])
    rs.sort(); gs.sort(); bs.sort()
    m = len(rs) // 2
    return (rs[m], gs[m], bs[m])


def _erase_band(
    canvas: Image.Image,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    above_y: int,
    below_y: int,
    *,
    skip_bg: bool = True,
) -> None:
    """Cover a placeholder word by rebuilding the art beneath it.

    The pill gradient/fade and the side-panel violet are essentially
    constant down any single column, so for each column ``x`` we fill the
    glyph band with a clean per-column colour. The colour is the median of
    a short vertical run at ``above_y`` (falling back to ``below_y`` when
    the upper sample lands on the dark background, e.g. a pill's rounded
    cap), which prevents single stray pixels from becoming vertical lines.

    ``skip_bg`` (default) leaves columns where both samples are background
    untouched — correct for the pills, whose rounded caps shouldn't be
    squared off. The rectangular side panel passes ``skip_bg=False`` so its
    genuinely-dark left edge is rebuilt too (otherwise the left half of
    each word survives).
    """
    px = canvas.load()
    for x in range(x0, x1):
        src = _median_sample(px, x, above_y)
        if _is_bg(src):
            src = _median_sample(px, x, below_y)
        if skip_bg and _is_bg(src):
            continue
        for y in range(y0, y1):
            px[x, y] = src


def _remove_vlines(canvas: Image.Image, x0: int, y0: int, x1: int, y1: int) -> None:
    """Erase thin dark *decorative* vertical lines inside a pill region.

    The template art carries a faint accent line down each pill. We find
    columns markedly darker than their neighbours 8px away (where both
    neighbours are clearly pill-coloured, so the rounded caps and the
    fade-to-black edges are left alone), then replace each flagged column
    by horizontally interpolating between the nearest clean columns on
    either side — which preserves the gradient.
    """
    px = canvas.load()
    h = y1 - y0

    def col_lum(x: int) -> int:
        return sum(sum(px[x, y]) for y in range(y0, y1)) // h

    flagged: list[int] = []
    for x in range(x0 + 8, x1 - 8):
        c = col_lum(x)
        ln, lr = col_lum(x - 8), col_lum(x + 8)
        if c < ln - 28 and c < lr - 28 and ln > 200 and lr > 200:
            flagged.append(x)
    if not flagged:
        return

    flset = set(flagged)
    for x in flagged:
        # Nearest clean columns to the left and right of this line.
        lx = x - 1
        while lx in flset and lx > x0:
            lx -= 1
        rx = x + 1
        while rx in flset and rx < x1 - 1:
            rx += 1
        span = max(rx - lx, 1)
        t = (x - lx) / span
        for y in range(y0, y1):
            cl, cr = px[lx, y], px[rx, y]
            px[x, y] = (
                int(cl[0] + (cr[0] - cl[0]) * t),
                int(cl[1] + (cr[1] - cl[1]) * t),
                int(cl[2] + (cr[2] - cl[2]) * t),
            )


# ---------------------------------------------------------------------------
# Background (shared with rank_image)
# ---------------------------------------------------------------------------
def make_atmospheric_background(
    w: int,
    h: int,
    *,
    orbs: list[tuple[int, int, int, tuple, int, int]],
) -> Image.Image:
    """Deep-aubergine gradient canvas with floating glowing orbs."""
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
        guild_icon_bytes=guild_icon_bytes,
    ).render()


class _Renderer:
    def __init__(
        self,
        *,
        entries: list[LeaderboardEntry],
        highlight_rank: int | None,
        guild_icon_bytes: bytes | None = None,
    ) -> None:
        self.by_rank = {e.rank: e for e in entries}
        self.highlight_rank = highlight_rank
        self.guild_icon_bytes = guild_icon_bytes

    def render(self) -> bytes:
        canvas = Image.open(_TEMPLATE_PATH).convert("RGB")

        # Erase every placeholder word first, then draw all overlays. Doing
        # the erases up front means a tall name never gets clipped by a
        # neighbouring row's erase pass.
        for rank in (1, 2, 3):
            slot = _PILLS[rank]
            bx0, by0, bx1, by1 = slot["band"]
            _erase_band(canvas, bx0, by0, bx1, by1, slot["above"], slot["below"])
            # Remove the template's decorative accent line inside the pill.
            px0, py0, px1, py1 = slot["pill"]
            _remove_vlines(canvas, px0 + 30, py0 + 24, px1 - 30, py1 - 24)
        for rank, (by0, by1) in _SIDE_BANDS.items():
            s_above, s_below = _SIDE_SAMPLES[rank]
            _erase_band(
                canvas, _SIDE_X0, by0 - 8, _SIDE_X1, by1 + 8,
                s_above, s_below, skip_bg=False,
            )

        if self.guild_icon_bytes is not None:
            self._draw_server_icon(canvas)

        for rank in (1, 2, 3):
            entry = self.by_rank.get(rank)
            if entry is not None:
                self._draw_pill(canvas, rank, entry)

        for rank in range(4, 11):
            entry = self.by_rank.get(rank)
            if entry is not None:
                self._draw_side_row(canvas, rank, entry)

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # -- server icon (top-right) -----------------------------------------
    def _draw_server_icon(self, canvas: Image.Image) -> None:
        """Circular server icon in the top-right, aligned with the masthead."""
        d = 96
        cx_right = canvas.width - 56
        cy = 92
        icon = _avatar_with_ring(self.guild_icon_bytes, d, (120, 100, 180))
        canvas.paste(icon, (cx_right - d, cy - d // 2), icon)

    # -- podium pills (1-3) ----------------------------------------------
    def _draw_pill(self, canvas: Image.Image, rank: int, entry: LeaderboardEntry) -> None:
        slot = _PILLS[rank]
        px0, py0, px1, py1 = slot["pill"]
        cy = (py0 + py1) // 2
        pill_h = py1 - py0

        # Avatar — left edges of all three pills share a common x so the
        # avatars line up vertically even though the gold pill is indented
        # right in the template.
        av_d = int(pill_h * 0.62)
        av_x = _PILL_AVATAR_LEFT
        av = _avatar_with_ring(entry.avatar_bytes, av_d, _PILL_RING[rank])
        canvas.paste(av, (av_x, cy - av_d // 2), av)

        # Level tag, right-aligned inside the pill (before the right cap).
        level_size = {1: 50, 2: 40, 3: 34}[rank]
        level_img = _slanted_text(
            f"Lv {entry.level}", _fonts.sans(level_size, weight=800), PILL_LEVEL
        )
        level_right = px1 - pill_h // 2 + int(pill_h * 0.10)
        _paste_right_v_centre(canvas, level_img, level_right, cy)

        # Name fills the space between avatar and the level tag. Sizes step
        # down 1st > 2nd > 3rd to reinforce the podium hierarchy.
        name_x = av_x + av_d + int(pill_h * 0.12)
        name_max = (level_right - level_img.width - 20) - name_x
        max_size = {1: 116, 2: 88, 3: 70}[rank]
        name = _fit_name(
            entry.display_name,
            PILL_TEXT,
            max_w=max(name_max, 40),
            max_size=max_size,
            min_size=30,
            weight=800,
        )
        _paste_v_centre(canvas, name, name_x, cy)

        # Viewer highlight — hairline ring around the capsule.
        if self.highlight_rank == rank:
            radius = pill_h // 2
            ring = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle(
                (px0 + 2, py0 + 2, px1 - 3, py1 - 3),
                radius=radius - 2,
                outline=ACCENT_HIGHLIGHT + (235,),
                width=5,
            )
            canvas.paste(ring, (0, 0), ring)

    # -- side column (4-10) ----------------------------------------------
    def _draw_side_row(self, canvas: Image.Image, rank: int, entry: LeaderboardEntry) -> None:
        by0, by1 = _SIDE_BANDS[rank]
        cy = (by0 + by1) // 2

        # Viewer highlight band (drawn before the glyphs).
        if self.highlight_rank == rank:
            band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(band).rectangle(
                (_SIDE_X0, by0 - 6, _SIDE_X1, by1 + 6),
                fill=(255, 255, 255, 55),
            )
            canvas.paste(band, (0, 0), band)

        # Small avatar at the left of the panel.
        av_d = 40
        av_x = _SIDE_X0 + 8
        av = _avatar_with_ring(entry.avatar_bytes, av_d, (70, 50, 140))
        canvas.paste(av, (av_x, cy - av_d // 2), av)

        # Level tag, right-aligned.
        level_img = _slanted_text(
            f"Lv {entry.level}", _fonts.sans(20, weight=700), SIDE_LEVEL
        )
        level_right = _SIDE_X1 - 10
        _paste_right_v_centre(canvas, level_img, level_right, cy)

        # Name between avatar and level.
        name_x = av_x + av_d + 8
        name_max = (level_right - level_img.width - 10) - name_x
        name = _fit_name(
            entry.display_name,
            SIDE_TEXT,
            max_w=max(name_max, 30),
            max_size=24,
            min_size=13,
            weight=720,
        )
        _paste_v_centre(canvas, name, name_x, cy)
