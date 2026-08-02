#!/usr/bin/env python3
"""
Content Forge — Instagram Post Generator
Generates 2 news posts + 1 educational post daily.
Styles: Mix of dark theme and gradient/vibrant.
"""

import json
import re
import random
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# === CONFIG ===
OUTPUT_DIR = Path(__file__).parent / "posts"
OUTPUT_DIR.mkdir(exist_ok=True)


def _load_brand_config():
    """Load brand/handle from config.json, falling back to the generic
    config.example.json template if no local config has been set up."""
    config_dir = Path(__file__).parent
    cfg_path = config_dir / "config.json"
    if not cfg_path.exists():
        cfg_path = config_dir / "config.example.json"
    cfg = json.loads(cfg_path.read_text())
    return cfg["brand"], cfg["handle"]


BRAND, HANDLE = _load_brand_config()
SIZE = (1080, 1080)  # Instagram square
CONTENT_LOG = OUTPUT_DIR / "content_log.json"
DEDUP_DAYS = 7
# Max days a topic can appear in a 7-day window before flagging (non-breaking news)
TOPIC_MAX_DAYS = 3
# Educational: exact title dedup window (longer than news)
EDU_DEDUP_DAYS = 14
# Jaccard similarity threshold for point-level content matching (0–1, lower = stricter)
EDU_SIMILARITY_THRESHOLD = 0.45

# Common words ignored when comparing point content
_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "have", "has",
    "do", "does", "did", "will", "would", "could", "should", "that", "this",
    "it", "its", "you", "your", "we", "our", "they", "their", "not", "so",
    "as", "from", "by", "can", "all", "more", "before", "after", "if",
    "then", "how", "what", "when", "where", "why", "who", "which", "into",
    "out", "up", "about", "than", "every", "even", "always", "never",
}

# Headlines containing these keywords are treated as high-importance and bypass
# the topic-diversity cap (they can run multiple days legitimately)
HIGH_IMPORTANCE_KEYWORDS = [
    "crash", "crisis", "recession", "collapse", "war", "invasion",
    "emergency", "bankruptcy", "default", "pandemic", "rate hike",
    "fed cuts", "fed raises", "hyperinflation", "market crash",
    "black monday", "black tuesday", "circuit breaker", "systemic risk",
]

# Fonts (bundled in fonts/ so the script is portable across machines)
FONTS_DIR = Path(__file__).parent / "fonts"
FONT_BOLD = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_MEDIUM = str(FONTS_DIR / "Poppins-Medium.ttf")
FONT_REGULAR = str(FONTS_DIR / "Poppins-Regular.ttf")

# Color palettes
DARK_PALETTES = [
    {"bg": "#0D0D0D", "accent": "#00F5D4", "secondary": "#7B2FBE", "text": "#FFFFFF", "subtext": "#B0B0B0"},
    {"bg": "#0A0A1A", "accent": "#FF6B35", "secondary": "#1E90FF", "text": "#FFFFFF", "subtext": "#A0A0B0"},
    {"bg": "#111827", "accent": "#F59E0B", "secondary": "#3B82F6", "text": "#FFFFFF", "subtext": "#9CA3AF"},
    {"bg": "#0F172A", "accent": "#22D3EE", "secondary": "#A855F7", "text": "#FFFFFF", "subtext": "#94A3B8"},
]

GRADIENT_PALETTES = [
    {"grad_start": "#667EEA", "grad_end": "#764BA2", "accent": "#FFD700", "text": "#FFFFFF", "subtext": "#E0E0FF"},
    {"grad_start": "#F093FB", "grad_end": "#F5576C", "accent": "#FFFFFF", "text": "#FFFFFF", "subtext": "#FFE0E8"},
    {"grad_start": "#4FACFE", "grad_end": "#00F2FE", "accent": "#1A1A2E", "text": "#FFFFFF", "subtext": "#E0F4FF"},
    {"grad_start": "#43E97B", "grad_end": "#38F9D7", "accent": "#1A1A2E", "text": "#1A1A2E", "subtext": "#2D3748"},
    {"grad_start": "#FA709A", "grad_end": "#FEE140", "accent": "#1A1A2E", "text": "#1A1A2E", "subtext": "#2D3748"},
]


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def stat_box_fill(text_color_hex):
    """Return a contrasting semi-transparent fill for stat boxes based on text color."""
    r, g, b = hex_to_rgb(text_color_hex)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    if luminance > 128:   # Light text → dark box
        return (0, 0, 0, 150)
    else:                 # Dark text → light box
        return (255, 255, 255, 200)


def _stat_colors(palette, box_fill, text_color):
    """Pick a legible (value_color, label_color) pair for a stat value drawn on box_fill."""
    val_color = hex_to_rgb(palette["accent"])
    val_luminance = 0.299 * val_color[0] + 0.587 * val_color[1] + 0.114 * val_color[2]
    box_is_dark = box_fill[3] < 180 or sum(box_fill[:3]) < 382
    if box_is_dark and val_luminance < 80:
        val_color = text_color
    lbl_color = (220, 220, 220) if box_fill[0] == 0 else (50, 50, 50)
    return val_color, lbl_color


def create_gradient(size, color1, color2, direction="vertical"):
    """Create a gradient image."""
    img = Image.new("RGB", size)
    pixels = img.load()
    r1, g1, b1 = hex_to_rgb(color1)
    r2, g2, b2 = hex_to_rgb(color2)

    for y in range(size[1]):
        for x in range(size[0]):
            if direction == "vertical":
                ratio = y / size[1]
            elif direction == "diagonal":
                ratio = (x + y) / (size[0] + size[1])
            else:
                ratio = x / size[0]

            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            pixels[x, y] = (r, g, b)
    return img


def draw_rounded_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    draw.pieslice([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=fill)
    draw.pieslice([x2 - 2*radius, y1, x2, y1 + 2*radius], 270, 360, fill=fill)
    draw.pieslice([x1, y2 - 2*radius, x1 + 2*radius, y2], 90, 180, fill=fill)
    draw.pieslice([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=fill)


def draw_text_wrapped(draw, text, x, y, max_width, font, fill, line_spacing=8, max_height=None):
    """Draw wrapped text and return the total height used.

    When max_height is given, stop before exceeding it and ellipsize the last
    line instead — the canvas is a fixed 1080x1080 square with source/footer
    text pinned at fixed Y coordinates below the body copy, so unbounded text
    (e.g. a long LLM-polished summary) would otherwise just draw straight
    through them.
    """
    avg_char_w = font.getlength("M")
    chars_per_line = max(1, int(max_width / avg_char_w))
    lines = textwrap.wrap(text, width=chars_per_line)

    if max_height is not None and lines:
        kept = []
        h = 0
        for line in lines:
            bbox = font.getbbox(line)
            line_h = bbox[3] - bbox[1]
            extra = line_h + (line_spacing if kept else 0)
            if kept and h + extra > max_height:
                break
            kept.append(line)
            h += extra
        if len(kept) < len(lines):
            last = kept[-1]
            while last and font.getlength(last + "…") > max_width:
                last = last[:-1].rstrip()
            kept[-1] = (last + "…") if last else "…"
        lines = kept

    total_h = 0
    for line in lines:
        bbox = font.getbbox(line)
        line_h = bbox[3] - bbox[1]
        draw.text((x, y + total_h), line, font=font, fill=fill)
        total_h += line_h + line_spacing
    return total_h


def _lines_height(font, n_lines, line_spacing):
    """Pixel height budget for n_lines of this font, matching draw_text_wrapped's math.

    Measured off "Mgjpqy" rather than just "Mg" — real wrapped lines regularly
    contain descenders (g/j/p/q/y), and a plain "Mg" reference undershoots
    their true height by a few px per line, which was enough to trip the
    max_height cutoff a line early on perfectly reasonable text.
    """
    lh = font.getbbox("Mgjpqy")[3] - font.getbbox("Mgjpqy")[1]
    return n_lines * lh + (n_lines - 1) * line_spacing


# Body copy (headline + summary + optional stat box) must end above this Y so
# it never collides with the source line (SIZE[1] - 130) or brand footer below it.
CONTENT_BOTTOM = SIZE[1] - 150


def draw_decorative_elements(draw, size, color, style="circles"):
    """Add subtle decorative elements."""
    w, h = size
    c = hex_to_rgb(color)
    faded = c + (30,)  # low opacity effect via drawing multiple small elements

    if style == "circles":
        for _ in range(5):
            cx = random.randint(0, w)
            cy = random.randint(0, h)
            r = random.randint(40, 150)
            draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=c + (40,), width=1)
    elif style == "dots":
        for _ in range(30):
            dx = random.randint(0, w)
            dy = random.randint(0, h)
            r = random.randint(2, 6)
            draw.ellipse([dx-r, dy-r, dx+r, dy+r], fill=c + (50,))
    elif style == "lines":
        for i in range(0, w, 80):
            draw.line([(i, 0), (i + 200, h)], fill=c + (15,), width=1)


def draw_brand_footer(draw, size, palette, is_dark=True):
    """Draw the brand footer with handle."""
    w, h = size
    accent = hex_to_rgb(palette["accent"])
    text_color = hex_to_rgb(palette["text"])

    # Divider line
    draw.line([(60, h - 100), (w - 60, h - 100)], fill=accent + (100,), width=2)

    # Brand name and handle
    brand_font = ImageFont.truetype(FONT_BOLD, 22)
    handle_font = ImageFont.truetype(FONT_REGULAR, 18)

    draw.text((60, h - 80), BRAND, font=brand_font, fill=accent)

    brand_w = brand_font.getlength(BRAND)
    draw.text((60 + brand_w + 15, h - 77), HANDLE, font=handle_font, fill=hex_to_rgb(palette["subtext"]))

    # "Follow for more" on right
    follow_font = ImageFont.truetype(FONT_MEDIUM, 18)
    follow_text = "Follow for more >"
    fw = follow_font.getlength(follow_text)
    draw.text((w - 60 - fw, h - 77), follow_text, font=follow_font, fill=accent)


# === POST TYPE 1: NEWS POST (Dark Style) ===
def generate_news_post_dark(headline, summary, category, source, stat_label=None, stat_value=None):
    """Generate a dark-themed news post."""
    palette = random.choice(DARK_PALETTES)
    bg_color = hex_to_rgb(palette["bg"])
    accent = hex_to_rgb(palette["accent"])
    secondary = hex_to_rgb(palette["secondary"])
    text_color = hex_to_rgb(palette["text"])
    subtext = hex_to_rgb(palette["subtext"])

    img = Image.new("RGBA", SIZE, bg_color + (255,))
    draw = ImageDraw.Draw(img)

    # Decorative elements
    draw_decorative_elements(draw, SIZE, palette["accent"], random.choice(["circles", "lines"]))

    # Top accent bar
    draw.rectangle([0, 0, SIZE[0], 6], fill=accent)

    # Category badge
    cat_font = ImageFont.truetype(FONT_BOLD, 20)
    cat_text = f"  {category.upper()}  "
    cat_w = cat_font.getlength(cat_text)
    draw_rounded_rect(draw, (60, 50, 60 + cat_w + 10, 88), 8, accent)
    draw.text((65, 53), cat_text, font=cat_font, fill=bg_color)

    # "BREAKING" or "LATEST" tag
    tag = random.choice(["BREAKING", "LATEST", "TRENDING", "UPDATE"])
    tag_font = ImageFont.truetype(FONT_MEDIUM, 16)
    tag_w = tag_font.getlength(tag)
    draw_rounded_rect(draw, (60 + cat_w + 30, 52, 60 + cat_w + 30 + tag_w + 20, 86), 8, secondary)
    draw.text((60 + cat_w + 40, 56), tag, font=tag_font, fill=text_color)

    # Headline
    headline_font = ImageFont.truetype(FONT_BOLD, 54)
    y_pos = 130
    headline_h = draw_text_wrapped(
        draw, headline, 60, y_pos, SIZE[0] - 120, headline_font, text_color, line_spacing=14,
        max_height=_lines_height(headline_font, 4, 14),
    )

    # Summary — bounded to whatever vertical room is left above the fixed
    # source/footer text (and the stat box, if one will be drawn after it).
    summary_font = ImageFont.truetype(FONT_REGULAR, 30)
    y_pos += headline_h + 30
    reserved_for_stat = 180 if (stat_label and stat_value) else 0
    summary_budget = max(
        CONTENT_BOTTOM - y_pos - reserved_for_stat,
        _lines_height(summary_font, 2, 8),
    )
    summary_h = draw_text_wrapped(
        draw, summary, 60, y_pos, SIZE[0] - 120, summary_font, subtext, line_spacing=8,
        max_height=summary_budget,
    )

    # Stat box (if provided)
    if stat_label and stat_value:
        y_pos += summary_h + 40
        box_h = 140
        draw_rounded_rect(draw, (60, y_pos, SIZE[0] - 60, y_pos + box_h), 16, hex_to_rgb(palette["bg"]))
        # Border effect
        draw.rounded_rectangle([60, y_pos, SIZE[0] - 60, y_pos + box_h], radius=16, outline=accent, width=2)

        val_font = ImageFont.truetype(FONT_BOLD, 52)
        lbl_font = ImageFont.truetype(FONT_REGULAR, 22)

        draw.text((90, y_pos + 20), stat_value, font=val_font, fill=accent)
        draw.text((90, y_pos + 85), stat_label, font=lbl_font, fill=subtext)

    # Source
    src_font = ImageFont.truetype(FONT_REGULAR, 18)
    source_text = f"Source: {source}"
    draw.text((60, SIZE[1] - 130), source_text, font=src_font, fill=subtext)

    # Brand footer
    draw_brand_footer(draw, SIZE, palette, is_dark=True)

    # Convert to RGB for saving
    final = Image.new("RGB", SIZE, bg_color)
    final.paste(img, mask=img.split()[3])
    return final


# === POST TYPE 2: NEWS POST (Gradient Style) ===
def generate_news_post_gradient(headline, summary, category, source, stat_label=None, stat_value=None):
    """Generate a gradient-themed news post."""
    palette = random.choice(GRADIENT_PALETTES)
    text_color = hex_to_rgb(palette["text"])
    subtext = hex_to_rgb(palette["subtext"])
    accent = hex_to_rgb(palette["accent"])

    direction = random.choice(["vertical", "diagonal"])
    img = create_gradient(SIZE, palette["grad_start"], palette["grad_end"], direction)
    draw = ImageDraw.Draw(img)

    # Semi-transparent overlay card
    overlay = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    draw_rounded_rect(overlay_draw, (40, 40, SIZE[0] - 40, SIZE[1] - 40), 24, (0, 0, 0, 80))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)

    # Category
    cat_font = ImageFont.truetype(FONT_BOLD, 20)
    cat_text = f"  {category.upper()}  "
    cat_w = cat_font.getlength(cat_text)
    draw_rounded_rect(draw, (80, 80, 80 + cat_w + 10, 118), 8, accent + (230,))

    # Pick contrasting text for badge
    badge_text_color = hex_to_rgb(palette.get("grad_start", "#000000"))
    draw.text((85, 83), cat_text, font=cat_font, fill=badge_text_color)

    # Headline
    headline_font = ImageFont.truetype(FONT_BOLD, 52)
    y_pos = 160
    headline_h = draw_text_wrapped(
        draw, headline, 80, y_pos, SIZE[0] - 160, headline_font, text_color, line_spacing=14,
        max_height=_lines_height(headline_font, 4, 14),
    )

    # Divider
    y_pos += headline_h + 20
    draw.line([(80, y_pos), (SIZE[0] - 80, y_pos)], fill=text_color + (100,), width=2)
    y_pos += 25

    # Summary — bounded to the room left above the fixed source/footer text
    # (and the stat box, if one will be drawn after it).
    summary_font = ImageFont.truetype(FONT_REGULAR, 29)
    reserved_for_stat = 165 if (stat_label and stat_value) else 0
    summary_budget = max(
        CONTENT_BOTTOM - y_pos - reserved_for_stat,
        _lines_height(summary_font, 2, 8),
    )
    summary_h = draw_text_wrapped(
        draw, summary, 80, y_pos, SIZE[0] - 160, summary_font, subtext, line_spacing=8,
        max_height=summary_budget,
    )

    # Stat box
    if stat_label and stat_value:
        y_pos += summary_h + 35
        box_fill = stat_box_fill(palette["text"])
        draw_rounded_rect(draw, (80, y_pos, SIZE[0] - 80, y_pos + 130), 16, box_fill)

        val_font = ImageFont.truetype(FONT_BOLD, 50)
        lbl_font = ImageFont.truetype(FONT_REGULAR, 22)
        val_color, lbl_color = _stat_colors(palette, box_fill, text_color)
        draw.text((110, y_pos + 15), stat_value, font=val_font, fill=val_color)
        draw.text((110, y_pos + 80), stat_label, font=lbl_font, fill=lbl_color)

    # Source + footer
    src_font = ImageFont.truetype(FONT_REGULAR, 18)
    draw.text((80, SIZE[1] - 130), f"Source: {source}", font=src_font, fill=subtext)
    draw_brand_footer(draw, SIZE, palette)

    return img.convert("RGB")


# === POST TYPE: NEWS POST (Split Banner, Dark Style) ===
def generate_news_post_split_dark(headline, summary, category, source, stat_label=None, stat_value=None):
    """Generate a dark-themed news post with a solid color banner up top."""
    palette = random.choice(DARK_PALETTES)
    bg_color = hex_to_rgb(palette["bg"])
    accent = hex_to_rgb(palette["accent"])
    secondary = hex_to_rgb(palette["secondary"])
    text_color = hex_to_rgb(palette["text"])
    subtext = hex_to_rgb(palette["subtext"])

    img = Image.new("RGBA", SIZE, bg_color + (255,))
    draw = ImageDraw.Draw(img)

    top_block_h = 400
    draw.rectangle([0, 0, SIZE[0], top_block_h], fill=secondary)
    draw.rectangle([0, top_block_h - 6, SIZE[0], top_block_h], fill=accent)

    # Category badge
    cat_font = ImageFont.truetype(FONT_BOLD, 20)
    cat_text = f"  {category.upper()}  "
    cat_w = cat_font.getlength(cat_text)
    draw_rounded_rect(draw, (60, 50, 60 + cat_w + 10, 88), 8, accent)
    draw.text((65, 53), cat_text, font=cat_font, fill=bg_color)

    # Headline (inside the banner, in white/text_color for guaranteed contrast
    # against the vivid secondary palette colors). Bounded to the banner's
    # own fixed height so it can't spill into the summary below it.
    headline_font = ImageFont.truetype(FONT_BOLD, 50)
    draw_text_wrapped(
        draw, headline, 60, 130, SIZE[0] - 120, headline_font, text_color, line_spacing=13,
        max_height=top_block_h - 130 - 20,
    )

    # Summary (below the banner) — bounded to the room left above the fixed
    # source/footer text (and the stat box, if one will be drawn after it).
    y_pos = top_block_h + 40
    summary_font = ImageFont.truetype(FONT_REGULAR, 30)
    reserved_for_stat = 180 if (stat_label and stat_value) else 0
    summary_budget = max(
        CONTENT_BOTTOM - y_pos - reserved_for_stat,
        _lines_height(summary_font, 2, 8),
    )
    summary_h = draw_text_wrapped(
        draw, summary, 60, y_pos, SIZE[0] - 120, summary_font, subtext, line_spacing=8,
        max_height=summary_budget,
    )

    # Stat box (if provided)
    if stat_label and stat_value:
        y_pos += summary_h + 40
        box_h = 140
        draw_rounded_rect(draw, (60, y_pos, SIZE[0] - 60, y_pos + box_h), 16, secondary)

        val_font = ImageFont.truetype(FONT_BOLD, 52)
        lbl_font = ImageFont.truetype(FONT_REGULAR, 22)
        draw.text((90, y_pos + 20), stat_value, font=val_font, fill=accent)
        draw.text((90, y_pos + 85), stat_label, font=lbl_font, fill=text_color)

    # Source
    src_font = ImageFont.truetype(FONT_REGULAR, 18)
    draw.text((60, SIZE[1] - 130), f"Source: {source}", font=src_font, fill=subtext)

    draw_brand_footer(draw, SIZE, palette, is_dark=True)

    final = Image.new("RGB", SIZE, bg_color)
    final.paste(img, mask=img.split()[3])
    return final


# === POST TYPE: NEWS POST (Stat Hero, Gradient Style) ===
def generate_news_post_stat_hero_gradient(headline, summary, category, source, stat_label=None, stat_value=None):
    """Generate a gradient news post with the stat value as the dominant, centered element.
    Falls back to a headline-forward layout when no stat is provided."""
    palette = random.choice(GRADIENT_PALETTES)
    text_color = hex_to_rgb(palette["text"])
    subtext = hex_to_rgb(palette["subtext"])

    direction = random.choice(["vertical", "diagonal"])
    img = create_gradient(SIZE, palette["grad_start"], palette["grad_end"], direction)
    draw = ImageDraw.Draw(img)

    # Overlay card — fill picked to contrast with this palette's own text color,
    # so light-text AND dark-text gradient palettes both stay legible.
    overlay_fill = stat_box_fill(palette["text"])
    overlay = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    draw_rounded_rect(overlay_draw, (40, 40, SIZE[0] - 40, SIZE[1] - 40), 24, overlay_fill)
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)

    # Category badge
    accent = hex_to_rgb(palette["accent"])
    cat_font = ImageFont.truetype(FONT_BOLD, 20)
    cat_text = f"  {category.upper()}  "
    cat_w = cat_font.getlength(cat_text)
    draw_rounded_rect(draw, (80, 80, 80 + cat_w + 10, 118), 8, accent + (230,))
    badge_text_color = hex_to_rgb(palette.get("grad_start", "#000000"))
    draw.text((85, 83), cat_text, font=cat_font, fill=badge_text_color)

    y_pos = 170
    center_x = SIZE[0] / 2

    if stat_label and stat_value:
        val_color, lbl_color = _stat_colors(palette, overlay_fill, text_color)

        val_font = ImageFont.truetype(FONT_BOLD, 100)
        val_bbox = val_font.getbbox(stat_value)
        val_x = center_x - (val_bbox[0] + val_bbox[2]) / 2
        draw.text((val_x, y_pos), stat_value, font=val_font, fill=val_color)
        # val_bbox[3] is the ink's bottom offset *from the draw origin* — the
        # real bottom edge is y_pos + val_bbox[3], not y_pos + (bbox height).
        # The old formula undercounted by val_bbox[1] (~25-30px at this font
        # size), letting the label sit a few px into the number's glyphs.
        y_pos += val_bbox[3] + 25

        lbl_font = ImageFont.truetype(FONT_MEDIUM, 26)
        lbl_text = stat_label.upper()
        lbl_w = lbl_font.getlength(lbl_text)
        draw.text((center_x - lbl_w / 2, y_pos), lbl_text, font=lbl_font, fill=lbl_color)
        y_pos += 60

        draw.line([(center_x - 80, y_pos), (center_x + 80, y_pos)], fill=text_color + (140,), width=3)
        y_pos += 40

        headline_font = ImageFont.truetype(FONT_BOLD, 42)
        summary_font_size = 24
    else:
        headline_font = ImageFont.truetype(FONT_BOLD, 52)
        summary_font_size = 29

    headline_h = draw_text_wrapped(
        draw, headline, 80, y_pos, SIZE[0] - 160, headline_font, text_color, line_spacing=12,
        max_height=_lines_height(headline_font, 4, 12),
    )
    y_pos += headline_h + 25

    summary_font = ImageFont.truetype(FONT_REGULAR, summary_font_size)
    summary_budget = max(CONTENT_BOTTOM - y_pos, _lines_height(summary_font, 2, 8))
    draw_text_wrapped(
        draw, summary, 80, y_pos, SIZE[0] - 160, summary_font, subtext, line_spacing=8,
        max_height=summary_budget,
    )

    src_font = ImageFont.truetype(FONT_REGULAR, 18)
    draw.text((80, SIZE[1] - 130), f"Source: {source}", font=src_font, fill=subtext)
    draw_brand_footer(draw, SIZE, palette)

    return img.convert("RGB")


# Pool of interchangeable news-post renderers (same call signature), so
# generate_all_daily_posts() can vary the layout day-to-day without repeating
# a style within the same day.
NEWS_POST_STYLES = [
    generate_news_post_dark,
    generate_news_post_gradient,
    generate_news_post_split_dark,
    generate_news_post_stat_hero_gradient,
]


# === POST TYPE 3: EDUCATIONAL POST ===
def generate_educational_post(title, points, category="LEARN"):
    """Generate an educational/infographic style post."""
    # Alternate between dark and gradient base
    use_dark = random.choice([True, False])

    if use_dark:
        palette = random.choice(DARK_PALETTES)
        bg_color = hex_to_rgb(palette["bg"])
        img = Image.new("RGBA", SIZE, bg_color + (255,))
    else:
        palette = random.choice(GRADIENT_PALETTES)
        palette["secondary"] = palette.get("grad_end", "#764BA2")
        palette["bg"] = palette.get("grad_start", "#667EEA")
        img = create_gradient(SIZE, palette["grad_start"], palette["grad_end"], "diagonal").convert("RGBA")
        # Darken overlay for readability
        overlay = Image.new("RGBA", SIZE, (0, 0, 0, 100))
        img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    accent = hex_to_rgb(palette["accent"])
    text_color = hex_to_rgb(palette["text"])
    subtext = hex_to_rgb(palette["subtext"])

    # No decorative dots — they reduce text readability

    # Category badge
    cat_font = ImageFont.truetype(FONT_BOLD, 18)
    cat_text = f"  {category.upper()}  "
    cat_w = cat_font.getlength(cat_text)
    draw_rounded_rect(draw, (60, 50, 60 + cat_w + 10, 84), 8, accent + (230,))
    badge_text = hex_to_rgb(palette.get("bg", "#000000")) if not use_dark else hex_to_rgb(palette["bg"])
    draw.text((65, 53), cat_text, font=cat_font, fill=badge_text)

    # Title
    title_font = ImageFont.truetype(FONT_BOLD, 47)
    y_pos = 120
    title_h = draw_text_wrapped(
        draw, title, 60, y_pos, SIZE[0] - 120, title_font, text_color, line_spacing=12,
        max_height=_lines_height(title_font, 3, 12),
    )

    # Accent line under title
    y_pos += title_h + 15
    draw.line([(60, y_pos), (300, y_pos)], fill=accent, width=4)
    y_pos += 30

    # Numbered points — the footer divider is fixed at SIZE[1] - 100, so points
    # stop being drawn once there's no room left above it rather than overlapping it.
    point_num_font = ImageFont.truetype(FONT_BOLD, 34)
    point_text_font = ImageFont.truetype(FONT_MEDIUM, 28)
    points_bottom = SIZE[1] - 120

    for i, point in enumerate(points[:5], 1):  # Max 5 points
        if y_pos + _lines_height(point_text_font, 1, 6) > points_bottom:
            break

        # Number circle
        circle_r = 22
        cx = 85
        cy = y_pos + 18
        draw.ellipse([cx - circle_r, cy - circle_r, cx + circle_r, cy + circle_r], fill=accent)

        # Number text centered in circle
        # getbbox returns (left, top, right, bottom) with glyph offsets —
        # use midpoints of the bbox to land exactly on (cx, cy)
        num_text = str(i)
        num_bbox = point_num_font.getbbox(num_text)
        num_color = hex_to_rgb(palette.get("bg", "#000000")) if not use_dark else hex_to_rgb(palette["bg"])
        text_x = cx - (num_bbox[0] + num_bbox[2]) / 2
        text_y = cy - (num_bbox[1] + num_bbox[3]) / 2
        draw.text((text_x, text_y), num_text, font=point_num_font, fill=num_color)

        # Point text
        point_h = draw_text_wrapped(
            draw, point, 130, y_pos, SIZE[0] - 200, point_text_font, text_color, line_spacing=6,
            max_height=min(_lines_height(point_text_font, 3, 6), points_bottom - y_pos),
        )
        y_pos += max(point_h, 50) + 25

    # Brand footer
    draw_brand_footer(draw, SIZE, palette, is_dark=use_dark)

    return img.convert("RGB")


def generate_caption(post_type, headline, summary, category, hashtags=None):
    """Generate an Instagram caption with hashtags."""
    if hashtags is None:
        base_tags = [f"#{BRAND.replace(' ', '')}", "#TechNews", "#FinanceNews", "#InvestSmart"]
        if "tech" in category.lower() or "ai" in category.lower():
            hashtags = base_tags + ["#TechTrends", "#AI", "#Innovation", "#FutureTech", "#StartupLife"]
        elif "finance" in category.lower() or "market" in category.lower():
            hashtags = base_tags + ["#StockMarket", "#Investing", "#WallStreet", "#BullMarket", "#Finance"]
        elif "crypto" in category.lower():
            hashtags = base_tags + ["#Crypto", "#Bitcoin", "#Blockchain", "#Web3", "#DeFi"]
        else:
            hashtags = base_tags + ["#Business", "#Money", "#Wealth", "#Growth", "#Knowledge"]

    if post_type == "news":
        caption = f"🔥 {headline}\n\n{summary}\n\n💡 What do you think about this? Drop your thoughts below 👇\n\n"
    else:
        caption = f"📚 {headline}\n\n{summary}\n\n💾 Save this for later | 🔄 Share with someone who needs this\n\n"

    caption += "━━━━━━━━━━━━━━━\n"
    caption += f"Follow {HANDLE} for daily tech & finance insights!\n\n"
    caption += " ".join(hashtags)

    return caption


def save_post(img, caption, post_type, index=None):
    """Save image and caption to output directory."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    idx = f"_{index}" if index else ""

    img_filename = f"{date_str}_{post_type}{idx}.png"
    caption_filename = f"{date_str}_{post_type}{idx}_caption.txt"

    img_path = OUTPUT_DIR / img_filename
    caption_path = OUTPUT_DIR / caption_filename

    img.save(img_path, "PNG", quality=95)
    caption_path.write_text(caption)

    print(f"✅ Saved: {img_path.name}")
    print(f"📝 Caption: {caption_path.name}")
    return img_path, caption_path


# === SAMPLE DATA FOR DEMO ===
SAMPLE_NEWS = [
    {
        "headline": "Apple Vision Pro 2 Reportedly In Final Testing Phase",
        "summary": "Sources close to Apple's supply chain indicate the next-gen spatial computing headset features a lighter design, improved hand tracking, and a price point 40% lower than the original.",
        "category": "TECH",
        "source": "Bloomberg",
        "stat_label": "Expected price reduction",
        "stat_value": "−40%",
    },
    {
        "headline": "S&P 500 Hits New All-Time High Amid AI Spending Surge",
        "summary": "The index crossed 6,200 for the first time as enterprise AI adoption accelerates. Tech mega-caps led the rally with cloud and AI infrastructure investments driving record earnings.",
        "category": "MARKETS",
        "source": "Reuters",
        "stat_label": "S&P 500 YTD gain",
        "stat_value": "+14.2%",
    },
]

SAMPLE_EDUCATIONAL = {
    "title": "5 Rules of Money That Schools Never Taught You",
    "category": "FINANCIAL LITERACY",
    "points": [
        "Pay yourself first — automate savings before spending on anything else",
        "The 50/30/20 rule: 50% needs, 30% wants, 20% savings & investments",
        "Compound interest is the 8th wonder — start investing early, even small amounts",
        "An emergency fund of 3-6 months expenses keeps you out of debt spirals",
        "Your income is your greatest wealth-building tool — invest in skills that grow it",
    ],
}


# === CONTENT DEDUPLICATION ===

def _load_content_log():
    """Load content log from disk, or return empty dict."""
    if CONTENT_LOG.exists():
        try:
            return json.loads(CONTENT_LOG.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_content_log(log):
    """Persist content log to disk."""
    CONTENT_LOG.write_text(json.dumps(log, indent=2))


def _recent_used_content(log):
    """Return set of normalised content strings used within their respective dedup windows."""
    news_cutoff = datetime.now() - timedelta(days=DEDUP_DAYS)
    edu_cutoff = datetime.now() - timedelta(days=EDU_DEDUP_DAYS)
    used = set()
    for date_str, entry in log.items():
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if entry_date >= news_cutoff:
            used.update(h.lower().strip() for h in entry.get("headlines", []))
        if entry_date >= edu_cutoff and entry.get("edu_title"):
            used.add(entry["edu_title"].lower().strip())
    return used


def _normalise(text):
    return text.lower().strip()


def _is_high_importance(headline):
    """Return True if a headline signals breaking/critical news that justifies repeat topics."""
    hl = headline.lower()
    return any(kw in hl for kw in HIGH_IMPORTANCE_KEYWORDS)


def _topic_day_counts(log):
    """
    Return {normalised_topic: days_count} for topics used in the last DEDUP_DAYS days.
    Each topic is counted once per day regardless of how many posts used it.
    """
    cutoff = datetime.now() - timedelta(days=DEDUP_DAYS)
    counts: dict[str, int] = {}
    for date_str, entry in log.items():
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if entry_date >= cutoff:
            seen_today = set()
            for cat in entry.get("categories", []):
                key = _normalise(cat)
                if key not in seen_today:
                    counts[key] = counts.get(key, 0) + 1
                    seen_today.add(key)
    return counts


def check_duplicates(news_items, edu_item):
    """
    Check proposed content against the last 7 days.
    Returns a list of duplicate strings (empty = all clear).
    """
    log = _load_content_log()
    used = _recent_used_content(log)
    dupes = []
    for item in news_items:
        if _normalise(item["headline"]) in used:
            dupes.append(item["headline"])
    if edu_item and _normalise(edu_item["title"]) in used:
        dupes.append(edu_item["title"])
    return dupes


def check_topic_diversity(news_items):
    """
    Warn if any non-high-importance topic has already appeared TOPIC_MAX_DAYS
    times in the last 7 days.
    Returns list of (topic, days_used) tuples that are over the limit.
    """
    log = _load_content_log()
    counts = _topic_day_counts(log)
    overused = []
    for item in news_items:
        if _is_high_importance(item["headline"]):
            continue  # Breaking/critical news bypasses diversity cap
        topic = _normalise(item["category"])
        days_used = counts.get(topic, 0)
        if days_used >= TOPIC_MAX_DAYS:
            overused.append((item["category"], days_used))
    return overused


def _keywords(text):
    """Return meaningful words from text, excluding stop words."""
    words = re.findall(r'[a-z]+', text.lower())
    return set(w for w in words if w not in _STOP_WORDS and len(w) > 2)


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_edu_content_similarity(edu_item):
    """
    Compare each proposed educational point against all points used in the
    last DEDUP_DAYS (7) days using Jaccard word overlap.
    Returns list of (new_point, similar_past_point) pairs that are too similar.
    Category is irrelevant — same category is fine, same content is not.
    """
    if not edu_item:
        return []
    log: dict = _load_content_log()
    cutoff = datetime.now() - timedelta(days=DEDUP_DAYS)
    past_points = []
    for date_str, entry in log.items():
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if entry_date >= cutoff:
            past_points.extend(entry.get("edu_points", []))

    conflicts = []
    for new_point in edu_item.get("points", []):
        new_kw = _keywords(new_point)
        for past_point in past_points:
            if _jaccard(new_kw, _keywords(past_point)) >= EDU_SIMILARITY_THRESHOLD:
                conflicts.append((new_point, past_point))
                break  # One conflict per point is enough
    return conflicts


def log_generated_content(news_items, edu_item):
    """Record today's content so future runs can detect repeats."""
    existing = _load_content_log()
    date_str = datetime.now().strftime("%Y-%m-%d")
    today_entry = {
        "headlines": [item["headline"] for item in news_items],
        "categories": [item["category"] for item in news_items],
        "edu_title": edu_item["title"] if edu_item else None,
        "edu_category": edu_item.get("category") if edu_item else None,
        "edu_points": edu_item.get("points", []) if edu_item else [],
    }
    # Prune using the longest window so edu title dedup (14 days) still works
    cutoff = datetime.now() - timedelta(days=max(DEDUP_DAYS, EDU_DEDUP_DAYS))
    pruned: dict[str, object] = {
        k: v for k, v in existing.items()
        if datetime.strptime(k, "%Y-%m-%d") >= cutoff
    }
    pruned[date_str] = today_entry
    _save_content_log(pruned)


def generate_all_daily_posts(news_items=None, edu_item=None):
    """Generate all 3 daily posts."""
    if news_items is None:
        news_items = SAMPLE_NEWS
    if edu_item is None:
        edu_item = SAMPLE_EDUCATIONAL

    # --- Exact duplicate check ---
    dupes = check_duplicates(news_items, edu_item)
    if dupes:
        print("⚠️  DUPLICATE CONTENT DETECTED (used in the last 7 days):")
        for d in dupes:
            print(f"   • {d}")
        print("   Replace this content before generating posts.")
        return []

    # --- News topic diversity check ---
    overused = check_topic_diversity(news_items)
    if overused:
        print(f"⚠️  TOPIC OVERUSE DETECTED (topic appeared {TOPIC_MAX_DAYS}+ days this week):")
        for topic, days in overused:
            print(f"   • '{topic}' used {days}/{DEDUP_DAYS} days — pick a different topic today.")
        print("   (High-importance breaking news bypasses this check automatically.)")
        return []

    # --- Educational content similarity check (7-day window) ---
    edu_conflicts = check_edu_content_similarity(edu_item)
    if edu_conflicts:
        print("⚠️  EDUCATIONAL CONTENT TOO SIMILAR to posts from the last 7 days:")
        for new_pt, past_pt in edu_conflicts:
            print(f"   • NEW:  {new_pt}")
            print(f"     PAST: {past_pt}")
        print("   Reword or replace the flagged points before generating.")
        return []

    results = []

    style1, style2 = random.sample(NEWS_POST_STYLES, 2)

    # News Post 1
    n1 = news_items[0]
    img1 = style1(
        n1["headline"], n1["summary"], n1["category"], n1["source"],
        n1.get("stat_label"), n1.get("stat_value")
    )
    cap1 = generate_caption("news", n1["headline"], n1["summary"], n1["category"])
    results.append(save_post(img1, cap1, "news", 1))

    # News Post 2
    n2 = news_items[1] if len(news_items) > 1 else news_items[0]
    img2 = style2(
        n2["headline"], n2["summary"], n2["category"], n2["source"],
        n2.get("stat_label"), n2.get("stat_value")
    )
    cap2 = generate_caption("news", n2["headline"], n2["summary"], n2["category"])
    results.append(save_post(img2, cap2, "news", 2))

    # Educational Post
    img3 = generate_educational_post(
        edu_item["title"], edu_item["points"], edu_item.get("category", "LEARN")
    )
    edu_summary = f"Here are {len(edu_item['points'])} key insights about {edu_item['title'].lower()} that can change how you think about money and tech."
    cap3 = generate_caption("educational", edu_item["title"], edu_summary, edu_item.get("category", "education"))
    results.append(save_post(img3, cap3, "educational"))

    # Log content so future runs can detect repeats
    log_generated_content(news_items, edu_item)

    return results


if __name__ == "__main__":
    print("🚀 Content Forge — Demo Post Generator (sample data)")
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📁 Output: {OUTPUT_DIR}")
    print("=" * 50)
    generate_all_daily_posts()
    print("=" * 50)
    print("✨ All posts generated! Ready to upload to Instagram.")
