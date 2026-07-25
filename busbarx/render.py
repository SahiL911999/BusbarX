#!/usr/bin/env python3
"""
Render a step-v2 JSON as its flat-pattern visualization:
  - the unfolded blank outline (length x width), top-left origin, Y down
  - every feature drawn as its TRUE shape (round=circle, obround=stadium,
    rectangle/square=rectangle) at its center, sized and rotated by orientation
  - bends drawn as dashed fold lines with angle / direction labels
  - origin marker + X (length) / Y (width) axes

Reads the JSON the extractor produced (so it shows exactly what's in the data).
"""
import sys, os, json, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon

COLOR = {"round": "#1f77b4", "obround": "#d62728", "rectangle": "#2ca02c",
         "square": "#9467bd", "irregular": "#7f7f7f"}


def _rot(pts, deg, cx, cy):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]


def _stadium(L, W, n=24):
    """Obround/stadium local polygon: long axis = x (length L), short = y (width W)."""
    r = W / 2.0
    half = max(L / 2.0 - r, 0.0)
    pts = []
    for i in range(n + 1):                       # right cap
        t = math.radians(-90 + 180 * i / n)
        pts.append((half + r * math.cos(t), r * math.sin(t)))
    for i in range(n + 1):                       # left cap
        t = math.radians(90 + 180 * i / n)
        pts.append((-half + r * math.cos(t), r * math.sin(t)))
    return pts


def render(json_path, out_png):
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    part = d["part"]; fp = part["flat_pattern"]
    L = fp["length_mm"]; W = fp["width_mm"]; t = fp.get("thickness_mm")
    status = part["flat_pattern_status"]; bp = d["bend_parameters"]

    fig, ax = plt.subplots(figsize=(min(16, 3 + L / 70.0), min(9, 3 + W / 70.0)))
    # blank outline
    ax.add_patch(Polygon([(0, 0), (L, 0), (L, W), (0, W)], closed=True,
                         fill=False, edgecolor="black", lw=1.8))

    # features as true shapes
    for f in d["features"]:
        x, y = f["x_mm"], f["y_mm"]; col = COLOR.get(f["type"], "#333")
        if f["type"] == "round":
            ax.add_patch(Circle((x, y), f["size_mm"]["diameter"] / 2.0,
                                fill=True, facecolor=col, alpha=0.35, edgecolor=col, lw=1.5))
        else:
            Lf = f["size_mm"]["length"]; Wf = f["size_mm"]["width"]
            ori = f.get("orientation_deg") or 0.0
            if f["type"] == "obround":
                local = _stadium(Lf, Wf)
            else:
                local = [(-Lf / 2, -Wf / 2), (Lf / 2, -Wf / 2), (Lf / 2, Wf / 2), (-Lf / 2, Wf / 2)]
            ax.add_patch(Polygon(_rot(local, ori, x, y), closed=True,
                                 fill=True, facecolor=col, alpha=0.35, edgecolor=col, lw=1.5))
        ax.plot(x, y, marker="+", color=col, ms=7, mew=1.2)
        ax.annotate(f"{f['id']}", (x, y), color=col, fontsize=7,
                    xytext=(2, 2), textcoords="offset points")

    # bends as dashed fold lines (labels staggered to avoid overlap on close bends)
    for i, b in enumerate(d["bends"]):
        s0, s1 = b["line_start_mm"], b["line_end_mm"]
        ax.plot([s0[0], s1[0]], [s0[1], s1[1]], "--", color="#ff7f0e", lw=1.8)
        mx = (s0[0] + s1[0]) / 2
        ly = W * (0.30 + 0.18 * (i % 3))
        lab = f"bend{b['id']} {b['angle_deg']}°"
        if b.get("direction"):
            lab += f" {b['direction']}"
        ax.annotate(lab, (mx, ly), color="#ff7f0e", fontsize=7.5, ha="center",
                    rotation=90, backgroundcolor="white")

    # origin + axes
    ax.plot(0, 0, "ks", ms=6)
    al = max(L, W) * 0.10
    ax.annotate("", xy=(al, 0), xytext=(0, 0), arrowprops=dict(arrowstyle="->", color="black"))
    ax.annotate("", xy=(0, al), xytext=(0, 0), arrowprops=dict(arrowstyle="->", color="black"))
    ax.text(al * 1.05, 0, "X = length", fontsize=8, va="center")
    ax.text(0, al * 1.05, "Y = width", fontsize=8, ha="left", va="bottom")

    src = d.get("source", "STEP")
    title = (f"{d['part_number']}   [{src}]   flat-pattern {L}×{W}×{t} mm   "
             f"status={status}")
    sub = (f"features: {len(d['features'])}   bends: {len(d['bends'])}   "
           f"bend: {bp['method']} (K={bp['value']}, profile={bp['profile']})   "
           f"footprint {part['formed_footprint']['length_mm']}×{part['formed_footprint']['width_mm']}")
    ax.set_title(title + "\n" + sub, fontsize=9)
    ax.set_xlabel("X (mm) — length"); ax.set_ylabel("Y (mm) — width")
    pad = max(L, W) * 0.06 + 5
    ax.set_xlim(-pad, L + pad); ax.set_ylim(-pad, W + pad)
    ax.set_aspect("equal"); ax.invert_yaxis(); ax.grid(True, ls=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    print(f"  rendered {out_png}  ({d['part_number']}, {status}, {len(d['features'])} feats, {len(d['bends'])} bends)")


if __name__ == "__main__":
    for jp in sys.argv[1:]:
        render(jp, os.path.splitext(jp)[0] + "_flat.png")
