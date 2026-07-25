"""
BusbarX Nexus — Milestone 3 STEP-extraction package.

Modules:
  bend_profiles  configurable bend-allowance profiles + resolver (no hardcoded K)
  unfold         flat-pattern unfold engine (segments, fold lines, fallback guardrail)
  extract        STEP solid -> step-v2 structured JSON
  render         flat-pattern visualization (PNG)
  pipeline       batch driver -> one output folder per part {json, png, log}
  app            CustomTkinter desktop GUI

Run the GUI with:  python -m busbarx
"""
__version__ = "step-v2"
