import math

from beats_theme import DARK

ACCENT = DARK["accent"]
TILE = DARK["surface"]
TILE_LINE = DARK["border"]

BAR_WEIGHTS = (0.40, 0.68, 1.00, 0.55, 0.34)


def bar_rects(view, pad, max_h_ratio=0.58, base_ratio=0.78):
    n = len(BAR_WEIGHTS)
    inner = view - 2 * pad
    gap = inner * 0.07
    width = (inner - gap * (n - 1)) / n
    rx = width / 2
    max_h = view * max_h_ratio
    base = view * base_ratio
    rects = []
    x = pad
    for weight in BAR_WEIGHTS:
        h = max(width, max_h * weight)
        y = base - h
        rects.append((x, y, width, h, rx))
        x += width + gap
    return rects


def svg_app_icon(size=64):
    pad = size * 0.18
    rects = bar_rects(size, pad)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">',
        f'  <rect width="{size}" height="{size}" rx="{size * 0.25}" fill="{TILE}"/>',
        f'  <rect x="{size * 0.02}" y="{size * 0.02}" width="{size * 0.96}" '
        f'height="{size * 0.96}" rx="{size * 0.23}" fill="none" '
        f'stroke="{TILE_LINE}" stroke-width="{size * 0.023}"/>',
    ]
    for x, y, w, h, rx in rects:
        parts.append(
            f'  <rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
            f'rx="{rx:.3f}" fill="{ACCENT}"/>'
        )
    parts.append("</svg>")
    parts.append("")
    return "\n".join(parts)


def svg_glyph(size=24):
    rects = bar_rects(size, pad=size * 0.08, max_h_ratio=0.72, base_ratio=0.88)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
    ]
    for x, y, w, h, rx in rects:
        parts.append(
            f'  <rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
            f'rx="{rx:.3f}" fill="currentColor"/>'
        )
    parts.append("</svg>")
    parts.append("")
    return "\n".join(parts)


def paint_bars(cr, view, pad, max_h_ratio=0.58, base_ratio=0.78):
    for x, y, w, h, rx in bar_rects(view, pad, max_h_ratio, base_ratio):
        cr.new_path()
        cr.arc(x + rx, y + rx, rx, math.pi, 1.5 * math.pi)
        cr.arc(x + w - rx, y + rx, rx, 1.5 * math.pi, 0)
        cr.arc(x + w - rx, y + h - rx, rx, 0, 0.5 * math.pi)
        cr.arc(x + rx, y + h - rx, rx, 0.5 * math.pi, math.pi)
        cr.close_path()
        cr.fill()


def write_app_icon_svg(path, size=64):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg_app_icon(size))
