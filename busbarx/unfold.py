#!/usr/bin/env python3
"""
BusbarX — flat-pattern UNFOLDER (deterministic geometry engine).

The STEP files are single folded 3D BREP solids with no embedded flat pattern (confirmed),
and OpenCASCADE has no native unfolder, so we compute the unfold from the solid:

  1. classify faces        -> PLANE segments + CYLINDER bends (by swept angle)
  2. group bend cylinders  -> physical bends (inner+outer radius share an axis)
  3. pair planar faces     -> segments (slabs: two parallel faces a thickness apart)
  4. adjacency             -> segment <-> bend via shared on-part LINE edge (the FOLD LINE)
  5. BFS from the base      -> lay every segment into one plane, inserting the developed
                              bend allowance (from a configurable profile) between segments
  6. remap features/bends  -> express all coordinates in the unfolded (flat-pattern) frame

The fold lines used here are the real on-part tangent edges (NOT the cylinder Axis().Location(),
which OCC anchors off-part — the root cause of the old off-part fold-line bug).

This module emits ONLY deterministic geometry (segments, fold lines, inner radius, angle,
thickness). The single place a shop parameter (K-factor / deduction) enters is the call to
bend_profiles.resolve_bend_allowance() — keeping extraction separate from allowance resolution.

On ANY topology it cannot safely unfold, unfold_solid() returns None so the caller falls back
to the formed footprint and flags it (never presents a guessed unfolded number as exact).

Scope handled (the busbar fleet): folds whose axes are all parallel (single fold direction,
fold lines spanning the width). Anything else -> None (fallback).
"""
import math
import logging
import numpy as np
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools

from . import bend_profiles

SWEEP_LO, SWEEP_HI = 0.30, 2.97          # rad: a BEND cylinder (~17deg..170deg)
PARALLEL = 0.98                           # |dot| threshold for "parallel"
AREA_TOL = 0.15                           # slab pairing: areas within 15%


# ── small vector helpers ──────────────────────────────────────────────────────
def _v(p):
    return np.array([p.x, p.y, p.z], float) if hasattr(p, "x") else np.array(p, float)

def _unit(a):
    n = np.linalg.norm(a)
    return a / n if n > 1e-12 else a

def _key(p, nd=3):
    return (round(float(p[0]), nd), round(float(p[1]), nd), round(float(p[2]), nd))


# ── face / edge extraction ─────────────────────────────────────────────────────
def _edges_of(face):
    out = []
    for e in face.Edges():
        try:
            p0, p1 = _v(e.startPoint()), _v(e.endPoint())
        except Exception:
            continue
        out.append(dict(key=tuple(sorted([_key(p0), _key(p1)])),
                        gtype=e.geomType(), p0=p0, p1=p1,
                        mid=(p0 + p1) / 2.0, dir=_unit(p1 - p0),
                        length=float(np.linalg.norm(p1 - p0))))
    return out


def _classify(solid):
    planes, bendcyls = [], []
    for i, f in enumerate(solid.Faces()):
        gt = f.geomType()
        if gt == "PLANE":
            try:
                n = f.normalAt(); c = f.Center()
            except Exception:
                continue
            edges = _edges_of(f)
            planes.append(dict(idx=i, face=f, n=_v(n), c=_v(c), area=f.Area(),
                               edges=edges, ekeys={e["key"] for e in edges}))
        elif gt == "CYLINDER":
            try:
                s = BRepAdaptor_Surface(f.wrapped); cyl = s.Cylinder()
                r = cyl.Radius()
                u1, u2, _, _ = BRepTools.UVBounds_s(f.wrapped)
                swept = abs(u2 - u1)
                ax = cyl.Axis(); d = ax.Direction(); loc = ax.Location()
            except Exception:
                continue
            if SWEEP_LO < swept < SWEEP_HI:
                edges = _edges_of(f)
                bendcyls.append(dict(idx=i, face=f, r=r, swept=swept,
                                     axis=_unit(np.array([d.X(), d.Y(), d.Z()])),
                                     loc=np.array([loc.X(), loc.Y(), loc.Z()]),
                                     edges=edges, ekeys={e["key"] for e in edges}))
    return planes, bendcyls


def _group_bends(bendcyls):
    """Merge inner+outer cylinder faces of one physical bend (parallel & coincident axes)."""
    used = [False] * len(bendcyls)
    bends = []
    for i, c in enumerate(bendcyls):
        if used[i]:
            continue
        grp = [c]; used[i] = True
        for j in range(i + 1, len(bendcyls)):
            if used[j]:
                continue
            o = bendcyls[j]
            if abs(float(np.dot(c["axis"], o["axis"]))) < 0.999:
                continue
            dp = o["loc"] - c["loc"]
            perp = np.linalg.norm(dp - np.dot(dp, c["axis"]) * c["axis"])
            if perp < 0.20:
                grp.append(o); used[j] = True
        radii = sorted(g["r"] for g in grp)
        inner = radii[0]; outer = radii[-1] if len(radii) > 1 else radii[0]
        ekeys = set().union(*[g["ekeys"] for g in grp])
        edges = [e for g in grp for e in g["edges"]]
        bends.append(dict(axis=c["axis"], inner_r=inner, outer_r=outer,
                          thickness=(outer - inner) if outer > inner else None,
                          angle=float(np.mean([g["swept"] for g in grp])),
                          ekeys=ekeys, edges=edges, faces=grp))
    return bends


def _build_segments(planes, thickness):
    """Pair two large parallel planar faces a `thickness` apart into one slab segment."""
    used = [False] * len(planes)
    segs = []
    order = sorted(range(len(planes)), key=lambda k: -planes[k]["area"])
    for ii in order:
        if used[ii]:
            continue
        a = planes[ii]
        best = None
        for jj in order:
            if jj == ii or used[jj]:
                continue
            b = planes[jj]
            if abs(float(np.dot(a["n"], b["n"]))) < PARALLEL:
                continue
            if min(a["area"], b["area"]) / max(a["area"], b["area"]) < (1 - AREA_TOL):
                continue
            sep = abs(float(np.dot(a["c"] - b["c"], a["n"])))
            if abs(sep - thickness) > max(0.25, 0.1 * thickness):
                continue
            # in-plane offset must be ~0 (truly stacked, not two coplanar-ish faces)
            inplane = np.linalg.norm((a["c"] - b["c"]) - sep * a["n"])
            if best is None or inplane < best[1]:
                best = (jj, inplane)
        if best is None:
            continue
        jj = best[0]; b = planes[jj]
        used[ii] = used[jj] = True
        faces = [a, b]
        ekeys = a["ekeys"] | b["ekeys"]
        edges = a["edges"] + b["edges"]
        segs.append(dict(faces=faces, n=_unit(a["n"]),
                         c=(a["c"] * a["area"] + b["c"] * b["area"]) / (a["area"] + b["area"]),
                         area=a["area"] + b["area"], ekeys=ekeys, edges=edges))
    return segs


def _shared_fold_edge(seg, bend, axis):
    """The on-part tangent LINE edge shared by `seg` and `bend`, parallel to the bend axis."""
    shared = seg["ekeys"] & bend["ekeys"]
    if not shared:
        return None
    cands = []
    for e in seg["edges"]:
        if e["key"] in shared and e["gtype"] == "LINE" and abs(float(np.dot(e["dir"], axis))) > PARALLEL:
            cands.append(e)
    if not cands:
        return None
    return max(cands, key=lambda e: e["length"])   # full-width tangent


# ── the unfold ─────────────────────────────────────────────────────────────────
class UnfoldResult:
    def __init__(self, segs, V, transforms, gmin, gmax, vmin, vmax, thickness, profile):
        self.segs = segs
        self.V = V
        self.transforms = transforms          # seg index -> dict(entry, d, sign, g0)
        self.gmin = gmin
        self.vmax = vmax
        self.thickness = thickness
        self.profile = profile
        self.bends_out = []                   # filled in by unfold_solid after construction
        self.length = round(gmax - gmin, 4)
        self.width = round(vmax - vmin, 4)

    def _g(self, si, P):
        t = self.transforms[si]
        return t["g0"] + t["sign"] * float(np.dot(P - t["entry"], t["d"]))

    def seg_of(self, P):
        """Which segment a 3D point belongs to (closest plane within half-thickness)."""
        best = None
        for si, s in enumerate(self.segs):
            dist = abs(float(np.dot(P - s["c"], s["n"])))
            if dist <= self.thickness / 2.0 + 0.5:
                if best is None or dist < best[1]:
                    best = (si, dist)
        if best is None:                       # fall back to nearest plane outright
            for si, s in enumerate(self.segs):
                dist = abs(float(np.dot(P - s["c"], s["n"])))
                if best is None or dist < best[1]:
                    best = (si, dist)
        return best[0]

    def map_point(self, P):
        P = _v(P)
        si = self.seg_of(P)
        x = self._g(si, P) - self.gmin
        y = self.vmax - float(np.dot(P, self.V))
        return round(x, 4), round(y, 4)

    def map_points(self, pts):
        return [self.map_point(p) for p in pts]

    def gv(self, P):
        """(x, y) coords in the flat frame — SAME y-flipped frame as map_point — so the
        orientation PCA is measured in the reported (top-left) coordinate system."""
        P = _v(P)
        si = self.seg_of(P)
        return self._g(si, P) - self.gmin, self.vmax - float(np.dot(P, self.V))


def physical_bends(solid):
    """Standalone: the grouped physical bends of a solid (for fallback parts that
    don't fully unfold but still need bend angle/radius/on-part fold line)."""
    _, bendcyls = _classify(solid)
    return _group_bends(bendcyls)


def bend_fold_edge(bend):
    """The on-part tangent LINE edge of a bend (longest edge parallel to its axis).
    Replaces the old off-part Axis().Location() projection."""
    cands = [e for e in bend["edges"]
             if e["gtype"] == "LINE" and abs(float(np.dot(e["dir"], bend["axis"]))) > PARALLEL]
    return max(cands, key=lambda e: e["length"]) if cands else None


def unfold_solid(solid, profile):
    """Return UnfoldResult or None (None => caller falls back to footprint).

    Thickness is derived internally from the bend geometry (outer_r - inner_r), which is
    authoritative for folded sheet; no thickness needs to be passed in.
    """
    try:
        planes, bendcyls = _classify(solid)
        if not bendcyls:
            return None
        bends = _group_bends(bendcyls)
        if not bends:
            return None

        # fold axes must all be parallel (single fold direction = busbar fleet)
        ax0 = bends[0]["axis"]
        for b in bends:
            if abs(float(np.dot(b["axis"], ax0))) < PARALLEL:
                return None                    # multi-direction fold -> fallback

        # Thickness from the bends themselves (outer_r - inner_r). This is authoritative:
        # for a folded sheet the inner/outer bend cylinders are coaxial and their radius
        # difference IS the material thickness. The min-plane-separation heuristic
        # (sheet_thickness) mis-reads on these parts (e.g. SBV13019 -> 2.369 vs true 6.35).
        bt = [b["thickness"] for b in bends if b["thickness"]]
        if not bt:
            return None
        t = float(np.median(bt))
        if max(bt) - min(bt) > max(0.3, 0.15 * t):
            return None                        # bends disagree on thickness -> fallback

        all_segs = _build_segments(planes, t)
        if len(all_segs) < 2:
            return None

        # link each bend to the segments it shares an on-part fold edge with
        raw_links = []
        for bi, b in enumerate(bends):
            linked = []
            for si, s in enumerate(all_segs):
                fe = _shared_fold_edge(s, b, b["axis"])
                if fe is not None:
                    linked.append((si, fe))
            if len(linked) != 2:
                return None                    # ambiguous/branching -> fallback
            raw_links.append(linked)

        # keep ONLY segments incident to a bend — drops spurious small slab pairs
        # (e.g. 46008-902-01's 121 mm^2 pairs) without an arbitrary area cutoff.
        keep = sorted({si for linked in raw_links for (si, _) in linked})
        if len(keep) != len(bends) + 1:
            return None                        # not a clean tree of N seg / N-1 bends
        remap = {old: new for new, old in enumerate(keep)}
        segs = [all_segs[old] for old in keep]

        adj = {i: [] for i in range(len(segs))}
        for bi, linked in enumerate(raw_links):
            (s0o, e0), (s1o, e1) = linked
            s0, s1 = remap[s0o], remap[s1o]
            adj[s0].append((s1, bi, e0, e1))
            adj[s1].append((s0, bi, e1, e0))

        # base = largest-area segment; frame V = fold axis (width), U = n x V (length)
        base_idx = max(range(len(segs)), key=lambda i: segs[i]["area"])
        n_base = _unit(segs[base_idx]["n"])
        V = _unit(ax0)
        # OCC's cylinder-axis direction sign is arbitrary; canonicalize V's sign so the
        # frame AND the bend up/down label are deterministic across exports of the same
        # geometry (the direction test below is linear in V, so an uncanonicalized sign
        # would let the label flip between identical parts).
        kdom = int(np.argmax(np.abs(V)))
        if V[kdom] < 0:
            V = -V
        if np.linalg.norm(np.cross(n_base, V)) < 1e-6:
            return None
        U = _unit(np.cross(n_base, V))

        # BFS, assigning each segment a developed-frame transform (entry, d, sign, g0)
        transforms = {base_idx: dict(entry=np.zeros(3), d=U, sign=1.0, g0=0.0)}
        base_c_g = float(np.dot(segs[base_idx]["c"], U))
        order = [base_idx]
        seen = {base_idx}
        bends_out = [None] * len(bends)
        seg_label = {base_idx: "base"}
        flange_n = 0
        queue = [base_idx]
        while queue:
            cur = queue.pop(0)
            tcur = transforms[cur]
            for (nb, bi, e_parent, e_child) in adj[cur]:
                if nb in seen:
                    continue
                seen.add(nb)
                b = bends[bi]
                # parent tangent developed position
                par_tan_g = tcur["g0"] + tcur["sign"] * float(np.dot(e_parent["mid"] - tcur["entry"], tcur["d"]))
                # sign for this arm
                if cur == base_idx:
                    sign = 1.0 if base_c_g < par_tan_g else -1.0
                else:
                    sign = tcur["sign"]
                # child in-plane direction (away from the fold, into the child)
                n_child = _unit(segs[nb]["n"])
                d_child = _unit(np.cross(n_child, V))
                if float(np.dot(segs[nb]["c"] - e_child["mid"], d_child)) < 0:
                    d_child = -d_child
                # developed bend allowance (the ONLY shop-parameter call)
                BA = bend_profiles.resolve_bend_allowance(profile, b["inner_r"], t, b["angle"])
                g0_child = par_tan_g + sign * BA
                transforms[nb] = dict(entry=e_child["mid"], d=d_child, sign=sign, g0=g0_child)
                # bend direction up/down relative to base top face
                cross = np.cross(n_child, V)
                direction = "up" if float(np.dot(n_base, cross)) >= 0 else "down"
                flange_n += 1
                seg_label[nb] = f"flange_{flange_n}"
                bends_out[bi] = dict(_bend=b, parent=cur, child=nb,
                                     fold_parent=e_parent, direction=direction)
                order.append(nb)
                queue.append(nb)

        if len(seen) != len(segs):
            return None                        # disconnected -> fallback

        # compute developed extents over all segment face vertices
        gs, vs = [], []
        for si, s in enumerate(segs):
            for f in s["faces"]:
                for e in f["edges"]:
                    for P in (e["p0"], e["p1"]):
                        t_ = transforms[si]
                        gs.append(t_["g0"] + t_["sign"] * float(np.dot(P - t_["entry"], t_["d"])))
                        vs.append(float(np.dot(P, V)))
        gmin, gmax = min(gs), max(gs)
        vmin, vmax = min(vs), max(vs)

        res = UnfoldResult(segs, V, transforms, gmin, gmax, vmin, vmax, t, profile)

        # finalize bends in flat coords (fold line on the PARENT, mapped)
        out = []
        for bi, bo in enumerate(bends_out):
            if bo is None:
                return None
            b = bo["_bend"]; fe = bo["fold_parent"]; pi = bo["parent"]
            # map the two endpoints of the parent tangent edge into flat coords
            s0 = res.map_point(fe["p0"]); s1 = res.map_point(fe["p1"])
            out.append(dict(id=bi + 1,
                            line_start_mm=[s0[0], s0[1]],
                            line_end_mm=[s1[0], s1[1]],
                            angle_deg=round(math.degrees(b["angle"]), 1),
                            radius_mm=round(b["inner_r"], 3),
                            direction=bo["direction"],
                            affected_segment=seg_label.get(bo["child"])))
        res.bends_out = out
        return res
    except Exception as e:
        # Guardrail: any geometry we can't unfold cleanly -> None; the caller
        # (cad_step_extract) then falls back to the formed footprint and flags it.
        # Bend profiles are validated up front (bend_profiles.load_profile), so this
        # path should only ever be a genuine geometry/topology limitation.
        logging.getLogger(__name__).debug("unfold fallback (%s): %s", type(e).__name__, e)
        return None


if __name__ == "__main__":
    import sys, os
    prof = bend_profiles.load_profile("default")
    for path in sys.argv[1:]:
        solid = cq.importers.importStep(path).val()
        r = unfold_solid(solid, prof)
        name = os.path.basename(path)
        if r is None:
            print(f"{name}: UNFOLD -> fallback (None)")
        else:
            print(f"{name}: segs={len(r.segs)} bends={len(r.bends_out)} "
                  f"flat L={r.length} W={r.width} t={round(r.thickness,3)}")
            for b in r.bends_out:
                print(f"   bend{b['id']} {b['angle_deg']}deg r{b['radius_mm']} "
                      f"{b['direction']} seg={b['affected_segment']} "
                      f"line={b['line_start_mm']}->{b['line_end_mm']}")
