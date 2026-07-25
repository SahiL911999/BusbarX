#!/usr/bin/env python3
"""
BusbarX — STEP / 3D Feature Extractor  ->  Nexus client JSON

Reads a STEP solid and outputs the client-agreed structured JSON:
  - units: millimetres
  - origin: TOP-LEFT of the flat part; X runs along the LENGTH, Y along the WIDTH (Y down)
  - features: round / obround / rectangle / square  (x,y center, size, orientation)
  - bends: angle, radius, fold line (if detectable)
  - per-feature + top-level source flag (STEP vs PDF_fallback) and confidence

Geometry source of truth = the 3D model (deterministic). No views, no dimension clutter.

step-v2: bent parts are output in TRUE FLAT-PATTERN (unfolded) coordinates (primary), with the
formed footprint kept as a secondary reference. Bend allowance is resolved from a configurable
profile (bend_profiles.py) — never hardcoded. The unfold engine is unfold.py; parts it cannot
unfold cleanly fall back to the footprint and are flagged (flat_pattern_status="fallback"),
so a guessed unfolded number is never presented as exact.
"""
import os, math, json
import numpy as np
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools

from . import bend_profiles
from . import unfold as unfold_mod

SCHEMA_VERSION = "step-v2"


# ── geometry helpers ─────────────────────────────────────────────────────────
def classify_wire(wire):
    """Classify a face inner-wire (a hole/cutout) by its edge signature + return its points."""
    from collections import Counter
    ets = Counter(e.geomType() for e in wire.Edges())
    n_circ = ets.get("CIRCLE", 0) + ets.get("ARC", 0)
    n_line = ets.get("LINE", 0)
    bb = wire.BoundingBox()
    dims = sorted([bb.xlen, bb.ylen, bb.zlen], reverse=True)
    long_, short_ = dims[0], dims[1]
    if long_ <= 1e-6:
        return None
    aspect = long_ / short_ if short_ > 1e-6 else 999
    has_other = any(t not in ("CIRCLE", "ARC", "LINE") for t in ets)
    if has_other:
        kind = "irregular"
    elif n_line == 0 and n_circ >= 1:
        kind = "round"
    elif n_circ >= 2 and n_line >= 2:
        kind = "obround"
    elif n_line >= 3 and n_circ == 0:
        kind = "square" if aspect <= 1.18 else "rectangle"
    else:
        kind = "irregular"
    c = wire.Center()
    pts = [v.toTuple() for v in wire.Vertices()]
    return dict(kind=kind, long=long_, short=short_, center=(c.x, c.y, c.z), pts=pts)


def cylinder_round_hole(face):
    """Near-FULL small-radius cylinder = round through-hole (no inner wire on some parts)."""
    try:
        s = BRepAdaptor_Surface(face.wrapped)
        r = s.Cylinder().Radius()
        u1, u2, v1, v2 = BRepTools.UVBounds_s(face.wrapped)
        if r <= 50.0 and abs(u2 - u1) > 5.0:           # >~286 deg = full round hole
            c = face.Center()
            ax = s.Cylinder().Axis().Direction()
            return dict(kind="round", long=2*r, short=2*r,
                        center=(c.x, c.y, c.z), normal=(ax.X(), ax.Y(), ax.Z()), pts=None)
    except Exception:
        return None
    return None


def sheet_thickness(solid):
    planes = [f for f in solid.Faces() if f.geomType() == "PLANE"]
    best = None
    for i, f in enumerate(planes):
        try:
            n, c = f.normalAt(), f.Center()
        except Exception:
            continue
        for g in planes[i+1:]:
            try:
                ng = g.normalAt()
            except Exception:
                continue
            if abs(n.x*ng.x + n.y*ng.y + n.z*ng.z) > 0.98:
                d = abs(c.sub(g.Center()).dot(n))
                if d > 0.1 and (best is None or d < best):
                    best = d
    return best


def part_frame(solid):
    """Build the flat-part 2D frame: U axis = LENGTH (longest in-plane edge), V = WIDTH."""
    planes = [f for f in solid.Faces() if f.geomType() == "PLANE"]
    dom = max(planes, key=lambda f: f.Area())
    n = dom.normalAt(); nrm = np.array([n.x, n.y, n.z], float)
    # length axis = direction of the longest straight edge on the dominant face
    U = None; best = 0.0
    for e in dom.Edges():
        if e.geomType() == "LINE":
            p0, p1 = e.startPoint(), e.endPoint()
            d = np.array([p1.x-p0.x, p1.y-p0.y, p1.z-p0.z], float)
            L = np.linalg.norm(d)
            if L > best:
                best, U = L, d / L
    if U is None:
        a = np.array([1, 0, 0], float) if abs(nrm[0]) < 0.9 else np.array([0, 1, 0], float)
        U = a - a.dot(nrm)*nrm; U /= np.linalg.norm(U)
    V = np.cross(nrm, U); V /= np.linalg.norm(V)
    # part extents in (U,V); ensure U is the longer (= length)
    pts = np.array([v.toTuple() for v in solid.Vertices()], float)
    us, vs = pts.dot(U), pts.dot(V)
    if (vs.max()-vs.min()) > (us.max()-us.min()):
        U, V = V, U; us, vs = pts.dot(U), pts.dot(V)
    return dict(U=U, V=V, n=nrm, min_u=us.min(), max_u=us.max(),
                min_v=vs.min(), max_v=vs.max())


def to_2d(p, fr):
    """3D point -> (x along length from left, y along width from top)."""
    P = np.array(p, float)
    x = P.dot(fr["U"]) - fr["min_u"]
    y = fr["max_v"] - P.dot(fr["V"])
    return round(float(x), 4), round(float(y), 4)


def orientation_deg(pts, fr):
    """Long-axis angle (deg from X) of a feature, measured in the SAME y-flipped frame the
    coordinates use (to_2d flips Y for the top-left origin), so the angle is consistent with
    the reported x/y. 0 = along length (X)."""
    if not pts or len(pts) < 3:
        return 0.0
    P = np.array(pts, float)
    xy = np.column_stack([P.dot(fr["U"]), fr["max_v"] - P.dot(fr["V"])])
    xy = xy - xy.mean(axis=0)
    try:
        _, vecs = np.linalg.eigh(np.cov(xy.T))
        ev = vecs[:, -1]                     # principal axis
        ang = math.degrees(math.atan2(ev[1], ev[0])) % 180.0
        return round(ang, 1)
    except Exception:
        return 0.0


def orientation_from_coords(coords):
    """Long-axis angle (deg from X) of a feature given its 2D (length,width) coords.
    Used for unfolded parts where feature points are already in the flat frame."""
    if not coords or len(coords) < 3:
        return 0.0
    P = np.array(coords, float)
    P = P - P.mean(axis=0)
    try:
        _, vecs = np.linalg.eigh(np.cov(P.T))
        ev = vecs[:, -1]
        return round(math.degrees(math.atan2(ev[1], ev[0])) % 180.0, 1)
    except Exception:
        return 0.0


# ── extraction ───────────────────────────────────────────────────────────────
def extract_features(solid):
    feats = []
    for f in solid.Faces():
        if f.geomType() != "PLANE":
            continue
        try:
            nn = f.normalAt(); nrm = (nn.x, nn.y, nn.z)
        except Exception:
            nrm = (0.0, 0.0, 1.0)
        for w in f.innerWires():
            sh = classify_wire(w)
            if sh and max(sh["long"], sh["short"]) > 0.2:
                sh["normal"] = nrm
                feats.append(sh)
    for f in solid.Faces():
        if f.geomType() == "CYLINDER":
            ch = cylinder_round_hole(f)
            if ch:
                feats.append(ch)
    # dedupe front/back of through-features by shared axis (thickness-independent)
    uniq = []
    for s in feats:
        dup = False
        for u in uniq:
            if not (s["kind"] == u["kind"] and abs(s["long"]-u["long"]) < 0.5
                    and abs(s["short"]-u["short"]) < 0.5):
                continue
            nA, nB = s["normal"], u["normal"]
            if abs(sum(nA[k]*nB[k] for k in range(3))) < 0.9:
                continue
            d = [s["center"][k]-u["center"][k] for k in range(3)]
            dlen = math.sqrt(sum(v*v for v in d))
            if dlen < 0.2:
                dup = True; break
            along = abs(sum(d[k]*nA[k] for k in range(3)))
            perp = math.sqrt(max(dlen*dlen - along*along, 0))
            if perp < 0.5 and along < 60.0:
                dup = True; break
        if not dup:
            uniq.append(s)
    return uniq


def to_json(step_path, source="STEP", profile=None):
    """Extract a STEP solid to the step-v2 client JSON.

    Bent parts -> true flat-pattern (unfolded) coords (primary) via unfold.py, with the
    formed footprint kept as a secondary reference. Parts that can't unfold cleanly fall
    back to the footprint and are flagged (flat_pattern_status="fallback").
    """
    if profile is None:
        profile = bend_profiles.load_profile("default")

    solid = cq.importers.importStep(step_path).val()
    fr = part_frame(solid)                       # footprint frame (secondary / fallback)
    foot_len = round(fr["max_u"] - fr["min_u"], 3)
    foot_wid = round(fr["max_v"] - fr["min_v"], 3)
    feats = extract_features(solid)
    phys_bends = unfold_mod.physical_bends(solid)
    is_bent = len(phys_bends) > 0
    sheet_t = sheet_thickness(solid)

    res = unfold_mod.unfold_solid(solid, profile) if is_bent else None

    bends_out = []
    if not is_bent:
        # genuinely flat: the footprint IS the flat pattern
        status = "computed"
        flat_len, flat_wid, thick = foot_len, foot_wid, sheet_t
        def mapper(P): return to_2d(P, fr)
        def orient(s): return orientation_deg(s.get("pts"), fr)
    elif res is not None:
        # unfolded successfully -> true flat-pattern coordinates (PRIMARY)
        status = "computed"
        flat_len, flat_wid, thick = res.length, res.width, res.thickness
        def mapper(P): return res.map_point(P)
        def orient(s): return orientation_from_coords([res.gv(p) for p in s.get("pts") or []])
        for b in res.bends_out:
            bb = dict(b); bb["source"] = source
            bends_out.append(bb)
    else:
        # bent but could not unfold cleanly -> footprint + flag (never a guessed number)
        status = "fallback"
        flat_len, flat_wid, thick = foot_len, foot_wid, sheet_t
        def mapper(P): return to_2d(P, fr)
        def orient(s): return orientation_deg(s.get("pts"), fr)
        for i, b in enumerate(phys_bends, 1):
            fe = unfold_mod.bend_fold_edge(b)
            if fe is None:
                continue
            s0 = to_2d(fe["p0"], fr); s1 = to_2d(fe["p1"], fr)
            bends_out.append({"id": i,
                              "line_start_mm": [s0[0], s0[1]], "line_end_mm": [s1[0], s1[1]],
                              "angle_deg": round(math.degrees(b["angle"]), 1),
                              "radius_mm": round(b["inner_r"], 3),
                              "direction": None, "affected_segment": None, "source": source})

    features = []
    feats_sorted = sorted(feats, key=lambda s: (round(mapper(s["center"])[1], 1),
                                                 mapper(s["center"])[0]))
    for i, s in enumerate(feats_sorted, 1):
        x, y = mapper(s["center"])
        if s["kind"] == "round":
            size = {"diameter": round(s["long"], 3)}
            ori = None                            # round: orientation not meaningful
        else:
            size = {"length": round(s["long"], 3), "width": round(s["short"], 3)}
            ori = orient(s)
        inb = (-1.0 <= x <= flat_len + 1.0) and (-1.0 <= y <= flat_wid + 1.0)
        features.append({"id": i, "type": s["kind"], "x_mm": x, "y_mm": y,
                         "size_mm": size, "orientation_deg": ori,
                         "source": source, "confidence": 1.0, "in_bounds": bool(inb)})

    oob = [f["id"] for f in features if not f["in_bounds"]]
    val_status = "flagged" if (oob or status == "fallback") else "pass"
    notes = None
    if status == "fallback":
        notes = ("bent part could not be unfolded cleanly; coordinates are the formed "
                 "footprint (flagged for review)")

    out = {
        "part_number": os.path.splitext(os.path.basename(step_path))[0],
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "units": "mm",
        "metadata": {                            # RESERVED — optional/empty now (ERP/parts list)
            "material": None, "plating": None, "edge_condition": None,
            "revision": None, "manufacturing": {},
        },
        "coordinate_system": {
            "origin": "top_left", "x_axis": "length", "y_axis": "width",
            "angle_reference": "degrees_from_x_axis", "basis": "flat_pattern",
        },
        "bend_parameters": {                     # configurable; reflects what was actually used
            "method": profile.get("method"),
            # value only when an allowance was actually applied (a computed unfold of a bent
            # part); null for flat parts and for fallback parts (footprint, no allowance used)
            "value": bend_profiles.resolved_value(profile) if (is_bent and status == "computed") else None,
            "profile": profile.get("name"),
            "configurable": True,
        },
        "part": {
            "is_bent": bool(is_bent),
            "bend_count": len(bends_out),
            "flat_pattern_status": status,       # computed | fallback
            "flat_pattern": {                    # PRIMARY — unfolded blank
                "length_mm": round(flat_len, 3), "width_mm": round(flat_wid, 3),
                "thickness_mm": round(thick, 3) if thick else None,
            },
            "formed_footprint": {                # SECONDARY — folded outline
                "length_mm": foot_len, "width_mm": foot_wid,
            },
        },
        "features": features,
        "bends": bends_out,
        "validation": {
            "status": val_status,
            "source": source,
            "features_total": len(features),
            "bends_total": len(bends_out),
            "out_of_bounds_ids": oob,
            "flat_pattern_status": status,
            "notes": notes,
        },
    }
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="BusbarX STEP -> step-v2 JSON")
    ap.add_argument("path", help="STEP file, OR a folder when --name is given")
    ap.add_argument("name", nargs="?", help="(optional) file name inside the folder")
    ap.add_argument("--profile", default="default", help="bend profile name")
    ap.add_argument("--profile-file", default=None, help="external bend-profile JSON")
    ap.add_argument("--out", default=None, help="write JSON here (default: stdout)")
    args = ap.parse_args()

    step = os.path.join(args.path, args.name) if args.name else args.path
    prof = bend_profiles.load_profile(args.profile, args.profile_file)
    result = to_json(step, profile=prof)
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
