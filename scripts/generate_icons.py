#!/usr/bin/env python3
"""Generate gecko app icon PNGs at required iOS / Android / PWA sizes.

Run from the project root:
    python scripts/generate_icons.py
Outputs to app/static/.
"""
import math
import os
import sys
from PIL import Image, ImageDraw

# ── Palette ───────────────────────────────────────────────────────────────
BG    = ( 10,  26,  10)   # dark forest green background
BODY  = (104, 212,  24)   # lime green gecko
JOINT = (255, 101,   0)   # orange toe pads / elbow pads
LINE  = (255, 255, 255)   # white dotted segment lines
EYE   = (232,  24,  24)   # red eye
DARK  = (  4,  10,   4)   # near-black eye socket

# ── Drawing primitives ────────────────────────────────────────────────────

def dot(draw, x, y, r, fill):
    draw.ellipse([x-r, y-r, x+r, y+r], fill=fill)


def B(p0, p1, p2, p3, t):
    """Cubic bezier scalar."""
    u = 1 - t
    return u*u*u*p0 + 3*u*u*t*p1 + 3*u*t*t*p2 + t*t*t*p3


def BD(p0, p1, p2, p3, t):
    """Cubic bezier tangent scalar."""
    u = 1 - t
    return 3 * (u*u*(p1-p0) + 2*u*t*(p2-p1) + t*t*(p3-p2))


def sausage(draw, ax, ay, bx, by, cx, cy, dx, dy, r0, r1, fill):
    """Overlapping circles along a cubic bezier — produces a smooth tapered tube."""
    for i in range(29):
        t = i / 28
        x = B(ax, bx, cx, dx, t)
        y = B(ay, by, cy, dy, t)
        dot(draw, x, y, r0 + (r1-r0)*t, fill)


def limb(draw, x0, y0, x1, y1, w, fill):
    """Thick line with round caps."""
    draw.line([(x0, y0), (x1, y1)], fill=fill, width=int(w))
    r = w / 2
    dot(draw, x0, y0, r, fill)
    dot(draw, x1, y1, r, fill)


def paw(draw, x, y, dir_x, dir_y, size, fill):
    """Orange toe-pad cluster: 4 toes fanning outward + central palm pad."""
    m = math.sqrt(dir_x**2 + dir_y**2)
    dx, dy = dir_x / m, dir_y / m
    px, py = -dy, dx                    # perpendicular
    reach  = size * 0.50
    spread = size * 0.27
    r_pad  = size * 0.36                # central palm pad
    r_toe  = size * 0.25                # toe pads
    for f in (-1.4, -0.46, 0.46, 1.4):
        dot(draw, x + dx*reach + px*f*spread, y + dy*reach + py*f*spread, r_toe, fill)
    dot(draw, x + dx*size*0.18, y + dy*size*0.18, r_pad, fill)


def dash_line(draw, x0, y0, x1, y1, fill, w=3, dash=5, gap=6):
    length = math.sqrt((x1-x0)**2 + (y1-y0)**2)
    if length < 1:
        return
    ddx, ddy = (x1-x0)/length, (y1-y0)/length
    d = 0; on = True
    while d < length:
        if on:
            ex = x0 + ddx * min(d+dash, length)
            ey = y0 + ddy * min(d+dash, length)
            draw.line([(x0+ddx*d, y0+ddy*d), (ex, ey)], fill=fill, width=w)
        d += dash if on else gap
        on = not on


def cross_seg(draw, ax, ay, bx, by, cx, cy, dx, dy, r_fn, t, fill):
    """Dotted dashed line perpendicular to body bezier at parameter t."""
    x  = B(ax, bx, cx, dx, t);   y  = B(ay, by, cy, dy, t)
    tx = BD(ax, bx, cx, dx, t);  ty = BD(ay, by, cy, dy, t)
    tl = math.sqrt(tx**2 + ty**2) or 1
    nx, ny = -ty/tl, tx/tl
    r = r_fn(t) + 3
    dash_line(draw, x - nx*r, y - ny*r, x + nx*r, y + ny*r, fill)


def spine_dots(draw, ax, ay, bx, by, cx, cy, dx, dy, fill):
    for i in range(21):
        if i % 3 == 0:
            t = i / 20
            dot(draw, B(ax, bx, cx, dx, t), B(ay, by, cy, dy, t), 2.5, fill)


# ── Gecko ─────────────────────────────────────────────────────────────────

def draw_gecko(draw):
    # Main body bezier: neck → tail base
    bx0,by0 = 280,158;  bx1,by1 = 244,240
    bx2,by2 = 220,330;  bx3,by3 = 230,420
    b_rfn = lambda t: 33 + (24-33)*t   # linear taper 33→24

    # Tail (drawn first — behind body)
    sausage(draw, 230,420, 190,458, 130,455, 104,415, 22,14, BODY)
    sausage(draw, 104,415,  88,378, 108,345, 146,342, 14, 8, BODY)
    sausage(draw, 146,342, 172,340, 186,358, 182,378,  8, 4, BODY)

    # Front-left limb: shoulder → elbow → wrist
    limb(draw, 255,202, 186,160, 24, BODY)
    limb(draw, 186,160, 118,122, 18, BODY)
    paw(draw, 118,122, -68,-38, 46, JOINT)
    dot(draw, 186,160, 12, JOINT)          # elbow pad

    # Front-right limb
    limb(draw, 268,198, 337,165, 24, BODY)
    limb(draw, 337,165, 393,151, 18, BODY)
    paw(draw, 393,151, 56,-14, 44, JOINT)
    dot(draw, 337,165, 12, JOINT)

    # Back-left limb: hip → knee → ankle
    limb(draw, 220,352, 155,392, 24, BODY)
    limb(draw, 155,392,  96,422, 18, BODY)
    paw(draw,  96,422, -59, 30, 44, JOINT)
    dot(draw, 155,392, 12, JOINT)

    # Back-right limb
    limb(draw, 228,356, 315,386, 24, BODY)
    limb(draw, 315,386, 392,404, 18, BODY)
    paw(draw, 392,404,  77, 18, 44, JOINT)
    dot(draw, 315,386, 12, JOINT)

    # Body
    sausage(draw, bx0,by0, bx1,by1, bx2,by2, bx3,by3, 33,24, BODY)

    # Neck bridge: head centre → body start
    sausage(draw, 310,140, 298,150, 286,153, 278,158, 28,32, BODY)

    # Head (tilted ellipse approximated axis-aligned at icon sizes)
    draw.ellipse([312-56, 117-42, 312+56, 117+42], fill=BODY)
    # Snout
    draw.ellipse([344-28,  91-20, 344+28,  91+20], fill=BODY)

    # ── Segment decoration ─────────────────────────────────────────────

    # Cross-segment dotted lines on main body
    for t in (0.11, 0.24, 0.37, 0.51, 0.65, 0.79):
        cross_seg(draw, bx0,by0, bx1,by1, bx2,by2, bx3,by3, b_rfn, t, LINE)

    # Spine dotted line
    spine_dots(draw, bx0,by0, bx1,by1, bx2,by2, bx3,by3, LINE)

    # Tail outer-coil segments
    for t in (0.30, 0.68):
        cross_seg(draw, 230,420, 190,458, 130,455, 104,415, lambda _: 18, t, LINE)
    cross_seg(draw, 104,415,  88,378, 108,345, 146,342, lambda _: 12, 0.48, LINE)

    # Head V-marking
    dash_line(draw, 300,110, 312,120, LINE, w=2, dash=3, gap=4)
    dash_line(draw, 312,120, 326,108, LINE, w=2, dash=3, gap=4)

    # ── Eye ───────────────────────────────────────────────────────────
    dot(draw, 305,109, 14, DARK)
    dot(draw, 305,109,  9, EYE)
    dot(draw, 308,105,  4, (255,255,255))


# ── Icon export ───────────────────────────────────────────────────────────

SIZES = {
    "icon-512.png":       512,
    "icon-192.png":       192,
    "apple-icon-180.png": 180,
    "apple-icon-167.png": 167,
    "apple-icon-152.png": 152,
    "apple-icon-120.png": 120,
}


def make_icons(out_dir: str = "app/static") -> None:
    src = 512

    # Draw at source size
    img = Image.new("RGB", (src, src), BG)
    draw = ImageDraw.Draw(img)
    draw_gecko(draw)

    # Rounded-corner mask
    mask = Image.new("L", (src, src), 0)
    m = ImageDraw.Draw(mask)
    m.rounded_rectangle([0, 0, src-1, src-1], radius=82, fill=255)
    result = Image.new("RGB", (src, src), (0, 0, 0))
    result.paste(img, (0, 0), mask=mask)

    os.makedirs(out_dir, exist_ok=True)
    for filename, size in SIZES.items():
        out = result.resize((size, size), Image.LANCZOS)
        path = os.path.join(out_dir, filename)
        out.save(path, "PNG", optimize=True)
        print(f"  {path}  ({size}×{size})")


if __name__ == "__main__":
    root = os.path.join(os.path.dirname(__file__), "..")
    os.chdir(root)
    print("Generating gecko icons…")
    make_icons()
    print("Done.")
