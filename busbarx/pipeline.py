#!/usr/bin/env python3
"""
Batch pipeline — run STEP files through extraction into a modular output layout:

    <out_root>/
      <part>/
        <part>.json        step-v2 structured output
        <part>_flat.png    flat-pattern visualization
        <part>.log         per-part run log

No GUI here — `app.py` calls into this, and it doubles as a CLI:

    python -m busbarx.pipeline part1.stp part2.stp [--out DIR] [--profile NAME]
"""
import os
import json
import traceback

from . import bend_profiles
from . import extract as _extract
from . import render as _render


def process_one(step_path, out_root, profile_name="default", log=None):
    """Extract one STEP file into out_root/<part>/{json,png,log}. Returns a result dict."""
    base = os.path.splitext(os.path.basename(step_path))[0]
    part_dir = os.path.join(out_root, base)
    os.makedirs(part_dir, exist_ok=True)
    lf = open(os.path.join(part_dir, base + ".log"), "w", encoding="utf-8")

    def _log(m):
        lf.write(m + "\n"); lf.flush()
        if log:
            log(m)

    try:
        prof = bend_profiles.load_profile(profile_name)
        _log(f"Reading STEP: {os.path.basename(step_path)}")
        out = _extract.to_json(step_path, profile=prof)
        json_path = os.path.join(part_dir, base + ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        _log(f"Wrote JSON: {json_path}")
        png_path = os.path.join(part_dir, base + "_flat.png")
        try:
            _render.render(json_path, png_path)
            _log(f"Wrote visualization: {png_path}")
        except Exception as e:
            png_path = None
            _log(f"(visualization skipped: {e})")
        p = out["part"]; fp = p["flat_pattern"]
        _log(f"status={p['flat_pattern_status']} "
             f"flat={fp['length_mm']}x{fp['width_mm']}x{fp['thickness_mm']} "
             f"features={len(out['features'])} bends={len(out['bends'])}")
        return {"ok": True, "part": base, "json": json_path, "png": png_path,
                "part_dir": part_dir, "out": out}
    except Exception as e:
        _log("ERROR:\n" + traceback.format_exc())
        return {"ok": False, "part": base, "part_dir": part_dir,
                "error": str(e), "json": None, "png": None, "out": None}
    finally:
        lf.close()


def process_batch(paths, out_root, profile_name="default", on_each=None, log=None):
    """Process several files. on_each(phase, index, path, result) -> progress callback
    (phase is 'start' then 'done'). Returns the list of per-file result dicts."""
    results = []
    for i, p in enumerate(paths, 1):
        if on_each:
            on_each("start", i, p, None)
        res = process_one(p, out_root, profile_name, log)
        results.append(res)
        if on_each:
            on_each("done", i, p, res)
    return results


def default_out_root(paths):
    """Default output: a BusbarX_Output folder next to the first input file."""
    if not paths:
        return os.path.join(os.getcwd(), "BusbarX_Output")
    return os.path.join(os.path.dirname(os.path.abspath(paths[0])), "BusbarX_Output")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="BusbarX batch STEP -> step-v2 JSON")
    ap.add_argument("paths", nargs="+", help="STEP files")
    ap.add_argument("--out", default=None, help="output root (default: next to first file)")
    ap.add_argument("--profile", default="default", help="bend profile name")
    args = ap.parse_args()
    root = args.out or default_out_root(args.paths)
    res = process_batch(args.paths, root, args.profile, log=print)
    ok = sum(1 for r in res if r["ok"])
    print(f"\nDone — {ok}/{len(res)} extracted -> {root}")
