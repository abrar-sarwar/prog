"""Programmatic leaderboard image.

The leaderboard is drawn entirely in Pillow (no static template), so every
podium pill is a *dynamic* capsule whose width grows to fit the member's
name — a long name lengthens the bubble instead of spilling outside it.
The look matches the supplied design: a deep-aubergine backdrop, a heavy
slanted ``leaderboard`` masthead, three descending gold/indigo/violet
podium pills (rank label in the far-left gutter, then avatar, then name,
then ``Lv N``), and a compact 4-10 side column on the right.

All geometry and colour live in the tunables block near the top so the
layout is easy to adjust. No XP is shown.

``make_atmospheric_background`` and the ``TEXT_*`` / ``BG_*`` constants are
kept exported because :mod:`core.rank_image` imports them.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_FONTS_DIR = _ASSETS_DIR / "fonts"
_FONT_SANS_PATH = _FONTS_DIR / "BricolageGrotesque-VariableFont.ttf"
_FONT_SERIF_PATH = _FONTS_DIR / "Fraunces-VariableFont.ttf"
# Every text on the leaderboard uses Helvetica Neue Bold Italic. The .ttc is
# bundled in assets/fonts so the renderer is portable (the server has no
# macOS system fonts); index 3 is the Bold Italic face. Fall back to the
# system copy, then to Bricolage, if the bundled file is unavailable.
_FONT_HELV_PATHS = [
    _FONTS_DIR / "HelveticaNeue.ttc",
    Path("/System/Library/Fonts/HelveticaNeue.ttc"),
]
_FONT_HELV_BOLD_ITALIC_INDEX = 3
# The original design — used only for its background (a clean full-height
# strip from the right edge is stretched across the canvas, so none of the
# template's own pills/text bleed through).
_TEMPLATE_PATH = _ASSETS_DIR / "leaderboard_template.png"

# ---------------------------------------------------------------------------
# Canvas + global tunables
# ---------------------------------------------------------------------------
CANVAS_W = 1080
CANVAS_H = 1080

# Helvetica Neue Bold Italic is a *real* italic, so no faux shear is needed
# (a non-zero value would double-slant the glyphs).
SLANT = 0.0

# Masthead. LEFT_MARGIN is the shared left edge for the title and the podium
# rank labels, giving a strong vertical alignment line down the left.
LEFT_MARGIN = 60
TITLE_TEXT = "leaderboard"
TITLE_SIZE = 120
TITLE_POS = (LEFT_MARGIN, 44)

# Background gradient endpoints (top -> bottom) + shared text colours.
BG_TOP = (14, 7, 23)
BG_BOTTOM = (27, 14, 50)
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (215, 203, 240, 255)
TEXT_DIM = (160, 142, 200, 255)

PILL_TEXT = (255, 255, 255)
PILL_LEVEL = (255, 255, 255)
PILL_ORDINAL = (255, 255, 255)

# --- Podium pills (ranks 1-3) ---------------------------------------------
# Pills are "open" bars that fade out on the LEFT (sliding-in look), so they
# start at the very left edge and run right. All avatars share one x so they
# line up; the rank label sits in the faded gutter to the left of the avatar.
PILL_X0 = 0                # bars begin at the canvas left edge and fade in
PILL_AVATAR_X = 188        # shared left x of every podium avatar (clears the
#                            left-aligned rank label + a gap)
PILL_RANK_X = LEFT_MARGIN  # left edge of the rank label (aligns with title)
PILL_MAX_RIGHT = CANVAS_W - 44   # 1st (above the column) may reach here
_SIDE_GAP = 28             # 2nd/3rd must stop this far short of the column

# Vertical layout is derived from ONE constant gap so the three pills sit in
# an even rhythm (the eye measures the gap between pills, so it must be
# constant even though the heights descend).
_PODIUM_TOP = 208          # y of the 1st pill's top
_PODIUM_GAP = 30           # constant vertical gap between pills

# Per-rank sizing. Heights descend 1st > 2nd > 3rd. Font sizes are kept
# proportional to the name (rank = 0.50*name, level = 0.30*name) so the
# three rows read as one design at three scales. ``min_w`` keeps short-name
# pills from looking stubby; the pill grows past it to fit longer names.
_PODIUM = {
    1: {"height": 224, "name_size": 150, "rank_size": 75,
        "level_size": 45, "min_w": 660},
    2: {"height": 192, "name_size": 118, "rank_size": 59,
        "level_size": 35, "min_w": 560},
    3: {"height": 172, "name_size": 92, "rank_size": 46,
        "level_size": 28, "min_w": 520},
}

# Each rank has a signature colour; the pill gradient is DERIVED from it (a
# dark, near-black version that fades in on the left up to the full bright
# colour on the right), and the rank label is drawn in the same colour.
# Classic medal palette: gold / silver / bronze.
RANK_COLORS: dict[int, tuple[int, int, int]] = {
    1: (255, 201, 92),    # gold
    2: (208, 214, 232),   # silver
    3: (214, 150, 92),    # bronze
}


def _darken(c: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    """Scale an RGB colour toward black by factor ``f`` (0=black, 1=same)."""
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


def _rank_gradient(rank: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(left, right) gradient endpoints derived from the rank colour."""
    c = RANK_COLORS[rank]
    return (_darken(c, 0.22), c)


def _podium_y_top(rank: int) -> int:
    """Top y of a pill, derived from a constant inter-pill gap."""
    y = _PODIUM_TOP
    for r in range(1, rank):
        y += _PODIUM[r]["height"] + _PODIUM_GAP
    return y

# Avatar ring tint per rank.
_PILL_RING = {1: (150, 96, 24), 2: (54, 38, 120), 3: (74, 54, 140)}

# --- Side column (ranks 4-10) ---------------------------------------------
SIDE_X0 = 690
SIDE_X1 = CANVAS_W - 36
SIDE_Y0 = _podium_y_top(2)   # top aligns with the 2nd pill
SIDE_Y1 = CANVAS_H - 40
SIDE_ROW_GAP = 16        # vertical gap between row pills (was 12; too tight)
SIDE_FILL = (104, 84, 188, 235)
SIDE_EMPTY_FILL = (70, 58, 120, 150)    # dimmer fill for empty slots
SIDE_HIGHLIGHT = (255, 255, 255, 70)
SIDE_TEXT = (240, 234, 255)
SIDE_EMPTY_TEXT = (150, 140, 185)       # dim rank label on empty slots
SIDE_LEVEL = (214, 203, 244)
SIDE_ORDINAL = (208, 196, 240)
SIDE_NAME_SIZE = 30      # bigger than before; shrinks to fit if needed
SIDE_RANK_SIZE = 22      # was 28 — rank shouldn't rival the name

ACCENT_HIGHLIGHT = (255, 255, 255)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
class _FontBook:
    """Lazily-loaded, size+weight-cached variable-font accessor."""

    def __init__(self) -> None:
        self._fonts: dict[str, ImageFont.FreeTypeFont] = {}

    def sans(self, size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
        # Every leaderboard text is Helvetica Neue Bold Italic. ``weight`` is
        # ignored (the face is already bold); kept in the signature so call
        # sites don't change.
        key = f"helv:{size}"
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = self._load_helvetica(size)
        self._fonts[key] = font
        return font

    # Serif callers fall through to the same Helvetica Neue Bold Italic so
    # the whole card is one typeface.
    serif = sans

    def _load_helvetica(self, size: int) -> ImageFont.FreeTypeFont:
        for path in _FONT_HELV_PATHS:
            if path.exists():
                try:
                    return ImageFont.truetype(
                        str(path), size=size,
                        index=_FONT_HELV_BOLD_ITALIC_INDEX,
                    )
                except Exception:
                    continue
        # Last-resort fallback so rendering never crashes.
        font = ImageFont.truetype(str(_FONT_SANS_PATH), size=size)
        try:
            font.set_variation_by_axes([min(max(size, 12), 96), 800, 100])
        except Exception:
            pass
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
    """Render ``text`` sheared to the right (faux italic).

    The image height is the font's full cell (ascent+descent), NOT the
    inked bounding box, and only the *horizontal* extent is cropped. That
    means every string at a given size has the same height and the same
    internal baseline, so vertical-centering different names (with/without
    ascenders or descenders) and aligning name-vs-level baselines both
    work consistently — fixing names that otherwise rode low."""
    ascent, descent = font.getmetrics()
    cell_h = ascent + descent
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = tmp.textbbox((0, 0), text, font=font)
    tw = max(bbox[2] - bbox[0], 1)
    pad = 8
    base = Image.new("RGBA", (tw + pad * 2, cell_h), (0, 0, 0, 0))
    # Draw at the natural baseline (y=0 top => baseline at ``ascent``); only
    # nudge x so the ink starts at ``pad``.
    ImageDraw.Draw(base).text(
        (pad - bbox[0], 0), text, font=font, fill=fill + (255,)
    )
    h = base.height
    extra = int(abs(slant) * h) + 1
    sheared = base.transform(
        (base.width + extra, h),
        Image.AFFINE,
        (1, slant, -slant * h, 0, 1, 0),
        resample=Image.BICUBIC,
    )
    # Crop horizontally only; keep the full vertical cell for consistent
    # centering.
    alpha_bbox = sheared.split()[-1].getbbox()
    if alpha_bbox:
        sheared = sheared.crop((alpha_bbox[0], 0, alpha_bbox[2], sheared.height))
    return sheared


def _fit_name(
    text: str,
    fill: tuple[int, int, int],
    *,
    max_w: int,
    max_size: int,
    min_size: int,
    weight: int = 800,
) -> Image.Image:
    """Largest slanted-sans render of ``text`` that fits ``max_w``.

    Shrinks down to ``min_size``; only ellipsises if it still won't fit at
    the minimum (which, for the dynamic pills, effectively never happens
    because the pill grows first)."""
    size = max_size
    while size >= min_size:
        img = _slanted_text(text, _fonts.sans(size, weight=weight), fill)
        if img.width <= max_w:
            return img
        size -= 2
    font = _fonts.sans(min_size, weight=weight)
    trimmed = text
    while trimmed and _slanted_text(trimmed + "…", font, fill).width > max_w:
        trimmed = trimmed[:-1]
    return _slanted_text((trimmed + "…") if trimmed else "…", font, fill)


def _measure(text: str, size: int, weight: int = 800) -> int:
    """Slanted-text pixel width of ``text`` at the given size."""
    return _slanted_text(text, _fonts.sans(size, weight=weight), (0, 0, 0)).width


def _paste_v_centre(canvas: Image.Image, img: Image.Image, x: int, cy: int) -> None:
    canvas.paste(img, (x, cy - img.height // 2), img)


def _paste_right_v_centre(
    canvas: Image.Image, img: Image.Image, x_right: int, cy: int
) -> None:
    canvas.paste(img, (x_right - img.width, cy - img.height // 2), img)


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 4 -> '4th', … 11 -> '11th'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _circular(image_bytes: bytes, size: int) -> Image.Image:
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


def _h_gradient_capsule(
    w: int, h: int, left: tuple[int, int, int], right: tuple[int, int, int]
) -> Image.Image:
    """A rounded capsule filled with a left->right horizontal gradient."""
    row = Image.new("RGB", (w, 1))
    rpx = row.load()
    for x in range(w):
        t = x / max(w - 1, 1)
        rpx[x, 0] = (
            int(left[0] + (right[0] - left[0]) * t),
            int(left[1] + (right[1] - left[1]) * t),
            int(left[2] + (right[2] - left[2]) * t),
        )
    pill = row.resize((w, h)).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=255)
    pill.putalpha(mask)
    return pill


def _fade_left_capsule(
    w: int,
    h: int,
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    fade_w: int,
) -> Image.Image:
    """A bar with a rounded RIGHT cap whose LEFT side fades out to nothing.

    The colour runs left->right (dark to bright) and the alpha ramps from 0
    at the far left up to fully opaque by ``fade_w``, so the pill reads as
    if it's sliding in from off-screen rather than a closed capsule.
    """
    # Horizontal colour gradient.
    row = Image.new("RGB", (w, 1))
    rpx = row.load()
    for x in range(w):
        t = x / max(w - 1, 1)
        rpx[x, 0] = (
            int(left[0] + (right[0] - left[0]) * t),
            int(left[1] + (right[1] - left[1]) * t),
            int(left[2] + (right[2] - left[2]) * t),
        )
    pill = row.resize((w, h)).convert("RGBA")

    # Shape mask: round only the RIGHT corners; the left edge is square so
    # it can fade straight off rather than curving back in.
    shape = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shape)
    sd.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=255)
    sd.rectangle((0, 0, h // 2, h - 1), fill=255)

    # Horizontal alpha ramp: 0 -> 255 across ``fade_w``.
    ramp_row = Image.new("L", (w, 1), 255)
    rp = ramp_row.load()
    for x in range(min(fade_w, w)):
        rp[x, 0] = int(255 * (x / max(fade_w, 1)))
    ramp = ramp_row.resize((w, h))

    pill.putalpha(ImageChops.multiply(shape, ramp))
    return pill


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


_BG_CACHE: dict[tuple[int, int], Image.Image] = {}


def _draw_background(w: int, h: int) -> Image.Image:
    """The original design's background, restored — smooth, no streaks.

    The template art is grainy, so stretching any single column turns that
    per-pixel noise into visible horizontal lines. Instead we collapse the
    template to its *average colour per row* (averaging across the full
    width cancels the grain), then resize that 1px-wide gradient up to the
    canvas. That keeps the exact aubergine top->bottom colour progression
    of the original while rendering perfectly smooth. Falls back to a plain
    gradient if the asset is missing.
    """
    cached = _BG_CACHE.get((w, h))
    if cached is not None:
        return cached.copy()
    try:
        tpl = Image.open(_TEMPLATE_PATH).convert("RGB")
        th = tpl.height
        # Sample the template's far-LEFT edge column — pure aubergine
        # background, no pills/side-panel contamination. It carries the
        # original top->bottom colour but also film grain, so blur it
        # vertically hard to get a smooth gradient (no horizontal streaks).
        col = tpl.crop((1, 0, 9, th)).resize((1, th), Image.BOX)
        col = col.filter(ImageFilter.GaussianBlur(24))
        bg = col.resize((w, h), Image.BILINEAR).convert("RGBA")
    except Exception:
        bg = make_atmospheric_background(w, h, orbs=[])
    _BG_CACHE[(w, h)] = bg
    return bg.copy()


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
        canvas = _draw_background(CANVAS_W, CANVAS_H)
        self._draw_title(canvas)
        if self.guild_icon_bytes is not None:
            self._draw_server_icon(canvas)

        for rank in (1, 2, 3):
            entry = self.by_rank.get(rank)
            if entry is not None:
                self._draw_pill(canvas, rank, entry)

        # Always lay out all seven 4-10 slots; empty ones show an empty
        # bubble with just the rank, so the column always looks complete.
        self._draw_side_panel(canvas)

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # -- masthead --------------------------------------------------------
    def _draw_title(self, canvas: Image.Image) -> None:
        img = _slanted_text(TITLE_TEXT, _fonts.sans(TITLE_SIZE, weight=800), (244, 240, 252))
        canvas.paste(img, TITLE_POS, img)

    def _draw_server_icon(self, canvas: Image.Image) -> None:
        d = 96
        # Vertically centre the icon on the masthead's optical centre so the
        # top bar reads as one aligned row.
        title_cy = TITLE_POS[1] + int(TITLE_SIZE * 0.42)
        icon = _avatar_with_ring(self.guild_icon_bytes, d, (120, 100, 180))
        canvas.paste(icon, (CANVAS_W - 56 - d, title_cy - d // 2), icon)

    # -- podium pills ----------------------------------------------------
    def _draw_pill(self, canvas: Image.Image, rank: int, entry: LeaderboardEntry) -> None:
        cfg = _PODIUM[rank]
        y_top = _podium_y_top(rank)
        h = cfg["height"]
        cy = y_top + h // 2

        av_d = int(h * 0.60)
        av_x = PILL_AVATAR_X
        gap_av_name = int(h * 0.12)   # avatar -> name
        gap_name_lvl = int(h * 0.16)  # name -> level
        pad_r = h // 2                # keep level clear of the rounded cap

        level_img = _slanted_text(
            f"Lv {entry.level}", _fonts.sans(cfg["level_size"], weight=800), PILL_LEVEL
        )

        # 1st sits above the side column and may run nearly full width;
        # 2nd/3rd are level with the column so they stop short of it.
        max_right = PILL_MAX_RIGHT if rank == 1 else (SIDE_X0 - _SIDE_GAP)

        # The bar spans from the canvas left edge to ``pill_right``; the name
        # slot lies between the avatar and the level tag, so the bar's right
        # end grows to fit the (full) name.
        name_left = av_x + av_d + gap_av_name
        max_name_w = max_right - name_left - gap_name_lvl - level_img.width - pad_r
        name_img = _fit_name(
            entry.display_name,
            PILL_TEXT,
            max_w=max(max_name_w, 60),
            # Low floor so even a very long name shrinks to fit IN FULL.
            max_size=cfg["name_size"],
            min_size=15,
            weight=800,
        )

        content_right = (
            name_left + name_img.width + gap_name_lvl + level_img.width + pad_r
        )
        pill_right = max(cfg["min_w"], content_right)
        pill_right = min(pill_right, max_right)
        pill_w = pill_right - PILL_X0

        # Fade-left bar (open on the left, rounded cap on the right). The fade
        # runs out roughly where the avatar begins, so the rank label to its
        # left reads against the dark background.
        fade_w = av_x
        grad_left, grad_right = _rank_gradient(rank)
        pill = _fade_left_capsule(pill_w, h, grad_left, grad_right, fade_w)
        canvas.paste(pill, (PILL_X0, y_top), pill)

        if self.highlight_rank == rank:
            glow = _fade_left_capsule(
                pill_w, h, (255, 255, 255), (255, 255, 255), fade_w
            )
            glow.putalpha(glow.getchannel("A").point(lambda a: a // 5))
            canvas.paste(glow, (PILL_X0, y_top), glow)

        # Rank label, LEFT-aligned at the shared left margin (same x as the
        # masthead), so title + "1st/2nd/3rd" form one vertical edge.
        ord_img = _slanted_text(
            _ordinal(rank), _fonts.sans(cfg["rank_size"], weight=800), PILL_ORDINAL
        )
        _paste_v_centre(canvas, ord_img, PILL_RANK_X, cy)

        # Avatar.
        av = _avatar_with_ring(entry.avatar_bytes, av_d, _PILL_RING[rank])
        canvas.paste(av, (av_x, cy - av_d // 2), av)

        # Name.
        _paste_v_centre(canvas, name_img, name_left, cy)

        # Level, right-aligned inside the bar.
        _paste_right_v_centre(canvas, level_img, pill_right - pad_r, cy)

    # -- side column (4-10) ----------------------------------------------
    def _draw_side_panel(self, canvas: Image.Image) -> None:
        total_h = SIDE_Y1 - SIDE_Y0
        # Seven fixed slots (ranks 4-10). Empty slots still get a bubble.
        slot_h = (total_h - SIDE_ROW_GAP * 6) // 7
        row_h = slot_h

        # Fixed interior columns so every row's avatar/name/level start at
        # the SAME x regardless of rank-label width ("10th" is wider than
        # "4th"). The rank is right-aligned within a gutter sized for "10th".
        rank_gutter = _measure("10th", SIDE_RANK_SIZE)
        ord_right_x = SIDE_X0 + 16 + rank_gutter
        av_d = row_h - 14
        col_av_x = ord_right_x + 14
        col_name_x = col_av_x + av_d + 12
        level_right = SIDE_X1 - 16

        for i, rank in enumerate(range(4, 11)):
            entry = self.by_rank.get(rank)
            ry0 = SIDE_Y0 + i * (row_h + SIDE_ROW_GAP)
            cy = ry0 + row_h // 2
            highlighted = entry is not None and self.highlight_rank == rank

            # Row bubble. Empty slots use a dimmer fill so they read as
            # placeholders without disappearing.
            if highlighted:
                fill = SIDE_HIGHLIGHT
            elif entry is not None:
                fill = SIDE_FILL
            else:
                fill = SIDE_EMPTY_FILL
            pill = Image.new("RGBA", (SIDE_X1 - SIDE_X0, row_h), (0, 0, 0, 0))
            ImageDraw.Draw(pill).rounded_rectangle(
                (0, 0, SIDE_X1 - SIDE_X0 - 1, row_h - 1),
                radius=row_h // 2,
                fill=fill,
            )
            canvas.paste(pill, (SIDE_X0, ry0), pill)

            # Rank label, far left (always shown).
            ord_img = _slanted_text(
                _ordinal(rank), _fonts.sans(SIDE_RANK_SIZE, weight=800),
                SIDE_ORDINAL if entry is not None else SIDE_EMPTY_TEXT,
            )
            ord_right = SIDE_X0 + 16 + ord_img.width
            _paste_right_v_centre(canvas, ord_img, ord_right, cy)

            av_d = row_h - 14
            av_x = ord_right + 12
            if entry is None:
                # Empty slot: an outlined empty circle, nothing else.
                ph = _circle_placeholder(av_d, (96, 80, 140))
                canvas.paste(ph, (av_x, cy - av_d // 2), ph)
                continue

            av = _avatar_with_ring(entry.avatar_bytes, av_d, (70, 50, 140))
            canvas.paste(av, (av_x, cy - av_d // 2), av)

            # Level, right-aligned.
            level_img = _slanted_text(
                f"Lv {entry.level}", _fonts.sans(SIDE_RANK_SIZE - 4, weight=800), SIDE_LEVEL
            )
            level_right = SIDE_X1 - 16
            _paste_right_v_centre(canvas, level_img, level_right, cy)

            # Name between avatar and level — always shown in full.
            name_x = av_x + av_d + 12
            name_max = (level_right - level_img.width - 12) - name_x
            name_img = _fit_name(
                entry.display_name,
                SIDE_TEXT,
                max_w=max(name_max, 24),
                max_size=SIDE_NAME_SIZE,
                min_size=8,
                weight=760,
            )
            _paste_v_centre(canvas, name_img, name_x, cy)
