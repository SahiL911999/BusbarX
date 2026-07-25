#!/usr/bin/env python3
"""
BusbarX — Bend-allowance profiles (configurable, NOT hardcoded).

Per the locked client requirement: the bend calculation method/parameters must be
*configurable* (customer- / machine- / material-specific later), and must be SEPARATE
from the deterministic extraction geometry.

This module is pure data + a resolver. It contains NO CAD/geometry code. The unfolder
(`unfold.py`) emits deterministic geometry (inner radius, angle, thickness) and calls
`resolve_bend_allowance(profile, ...)` as the *only* place a K-factor / deduction enters.

A profile can be loaded from an external JSON file so the method/params change without
touching any extraction code:

    profile = load_profile("default")                  # built-in default
    profile = load_profile(path="my_shop.json")        # external override
    BA = resolve_bend_allowance(profile, inner_r, thickness, angle_rad)

External JSON shape (same as a PROFILES entry):
    { "name": "acme_press_brake",
      "method": "k_factor", "k_factor": 0.40,
      "deduction_table": null }
"""
import os
import json
import math

# Built-in profiles. The DEFAULT is shipped clearly flagged; the client's
# customer/machine/material profiles plug in later with the same shape, with no
# change to the extraction code.
PROFILES = {
    "default": {
        "name": "default",
        "method": "k_factor",      # k_factor | bend_deduction | cad_flat_pattern
        "k_factor": 0.44,          # neutral-axis factor; SHIPPED DEFAULT, override per shop
        "deduction_table": None,   # used when method == "bend_deduction"
    },
}

ENV_VAR = "BUSBARX_BEND_PROFILE"   # path to an external profile JSON (optional)
VALID_METHODS = ("k_factor", "bend_deduction", "cad_flat_pattern")


def load_profile(name="default", path=None):
    """Resolve a bend profile. Priority: explicit path -> env var path -> built-in name.

    Returns a validated dict with at least {name, method, ...}. Never raises for a missing
    built-in *name* (falls back to 'default' so extraction can always proceed). BUT if an
    external file is *explicitly requested* (via path arg or the env var) and is missing or
    malformed, raises — so a typo'd shop profile fails loudly instead of silently applying
    the default K-factor fleet-wide.
    """
    requested = path or os.environ.get(ENV_VAR)
    if requested:
        if not os.path.exists(requested):
            src = "path arg" if path else ENV_VAR
            raise FileNotFoundError(f"bend profile file not found ({src}): {requested!r}")
        with open(requested, "r", encoding="utf-8") as f:
            prof = json.load(f)
        _validate(prof, requested)                 # validate the raw parsed object first
        prof.setdefault("name", os.path.splitext(os.path.basename(requested))[0])
        return prof
    prof = dict(PROFILES.get(name) or PROFILES["default"])  # copy; don't mutate built-in
    _validate(prof, f"<built-in:{name}>")
    return prof


def _validate(prof, where="<profile>"):
    if not isinstance(prof, dict):
        raise ValueError(f"bad bend profile {where!r}: top-level must be a JSON object, "
                         f"got {type(prof).__name__}")
    method = prof.get("method")
    if method not in VALID_METHODS:
        raise ValueError(f"bad bend profile {where!r}: method must be one of {VALID_METHODS}, "
                         f"got {method!r}")
    if method == "k_factor" and not isinstance(prof.get("k_factor"), (int, float)):
        raise ValueError(f"bad bend profile {where!r}: k_factor must be a number, "
                         f"got {prof.get('k_factor')!r}")
    if method == "bend_deduction" and not isinstance(prof.get("deduction_table"), dict):
        raise ValueError(f"bad bend profile {where!r}: bend_deduction requires a "
                         f"'deduction_table' object")


def resolved_value(profile):
    """The single scalar/id recorded in bend_parameters.value for traceability."""
    m = profile.get("method")
    if m == "k_factor":
        return profile.get("k_factor")
    if m == "bend_deduction":
        return profile.get("deduction_table")
    return None  # cad_flat_pattern carries no scalar


def resolve_bend_allowance(profile, inner_r, thickness, angle_rad):
    """Developed (flat) length of ONE bend's arc, at the neutral axis. Returns mm.

    method:
      k_factor       BA = (inner_r + K*thickness) * angle_rad         [standard formula]
      bend_deduction BA derived from a deduction table (per profile)  [shop-specific]
      cad_flat_pattern  not derivable here (no embedded flat body in this dataset)

    This is the ONLY function that turns geometry into a length using a shop parameter.
    Raising here signals the caller (unfold.py) to fall back safely.
    """
    m = profile.get("method")
    if m == "k_factor":
        k = profile.get("k_factor")
        if k is None:
            raise ValueError("k_factor profile missing 'k_factor'")
        return (inner_r + k * thickness) * angle_rad

    if m == "bend_deduction":
        table = profile.get("deduction_table")
        if not table:
            raise ValueError("bend_deduction profile missing 'deduction_table'")
        # table keyed by angle bucket -> deduction (mm); BA = outer_arc - deduction.
        # outer_arc uses outer radius = inner_r + thickness.
        deg = round(math.degrees(angle_rad))
        bd = table.get(str(deg))
        if bd is None:
            raise ValueError(f"no bend deduction entry for {deg} deg")
        outer_arc = (inner_r + thickness) * angle_rad
        return outer_arc - bd

    if m == "cad_flat_pattern":
        # No embedded flat-pattern body exists in this dataset's STEP files (confirmed),
        # so there is nothing to read. Caller falls back.
        raise NotImplementedError("cad_flat_pattern: no embedded flat body to read")

    raise ValueError(f"unknown bend method {m!r}")


if __name__ == "__main__":
    p = load_profile("default")
    print("profile:", p["name"], "method:", p["method"], "value:", resolved_value(p))
    # demo: 90 deg bend, inner r 12.7, t 12.7  ->  (12.7 + 0.44*12.7) * pi/2
    print("BA 90deg r12.7 t12.7 =", round(resolve_bend_allowance(p, 12.7, 12.7, math.pi/2), 3), "mm")
    print("BA 45deg r6.35 t6.35 =", round(resolve_bend_allowance(p, 6.35, 6.35, math.pi/4), 3), "mm")
