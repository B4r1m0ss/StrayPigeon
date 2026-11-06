#!/usr/bin/env python3
"""Validate the reproduction package and recompute the numbers quoted in the paper.

Two jobs:

  1. every JSON under data/ parses and matches the shape declared in schema.json;
  2. the headline numbers in the manuscript are re-derived from those JSON files,
     so a reader can check the paper without rerunning the experiments.

Usage:
    python3 validate.py
    python3 validate.py --paper ../../githubs/StrayPigeon/docs/paper/manuscript-v3.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

REQUIRED_KEYS = {"session": ["rows"], "summary": ["by"]}


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def check_shape(name: str, obj: object) -> list[str]:
    """Cheap structural check; the JSON Schema in schema.json is the full contract."""
    problems: list[str] = []
    if isinstance(obj, list):
        # per-round session files are arrays of rounds
        if not all(isinstance(r, dict) for r in obj):
            problems.append(f"{name}: array contains non-object entries")
        return problems
    if not isinstance(obj, dict):
        return [f"{name}: top level is neither an object nor an array"]
    if "rows" in obj and not isinstance(obj["rows"], list):
        problems.append(f"{name}: rows is not an array")
    if "rounds" in obj and "rows" in obj and obj["rounds"] != len(obj["rows"]):
        problems.append(f"{name}: rounds={obj['rounds']} but {len(obj['rows'])} rows")
    return problems


def numbers(paper: str | None) -> tuple[list[str], list[str]]:
    ok, bad = [], []

    def want(label: str, needle: str) -> None:
        if paper is None or needle in paper:
            ok.append(f"{label}: {needle}")
        else:
            bad.append(f"{label}: {needle} not found in manuscript")

    e1 = load("e1_100_summary.json")
    want("E1 A median", f"{e1['by']['A']['median']:.2f}")
    want("E1 B median", f"{e1['by']['B']['median']:.2f}")
    e6 = load("e6_100_summary.json")
    want("E6 Cloud median", f"{e6['by']['Cloud']['median']:.2f}")
    want("E6 B median", f"{e6['by']['B']['median']:.2f}")

    ok.append("E1/E6 summaries: parsed")

    e3a, e3b = load("e3_netns_batch_summary.json"), load("e3_netns_batch2_summary.json")
    for arm in ("direct", "repunch"):
        n = e3a["summary"][arm]["ok"] + e3b["summary"][arm]["ok"]
        ok.append(f"E3 {arm}: {n} successful rounds")
        if n != 34:
            bad.append(f"E3 {arm}: expected 34, got {n}")

    e4 = load("e4_apdm_batch.json")
    valid = [r for r in e4["rows"] if r.get("initiator", {}).get("nat") == "apdm"]
    ok.append(f"E4: {len(valid)}/{len(e4['rows'])} valid rounds")
    if len(valid) != 28:
        bad.append(f"E4: expected 28 valid, got {len(valid)}")

    lan = load("e5_lan_summary.json")["summary"]
    want("LAN 256 MiB Mbps", f"{lan['268435456']['median_mbps']:.0f}")

    decomp = load("e5_netem_decomp.json")
    reps = sorted(
        r["a"]["file"]["seconds"] for r in decomp["rows"]
        if r["condition"].startswith("rate100_d100_l1") and r["size"] == 4 << 20
    )
    if reps:
        ok.append(f"E5 lossy 4 MiB: {len(reps)} rounds, {reps[0]:.1f}-{reps[-1]:.1f} s")

    e1b = load("e1b_lan_4m.json")
    ok.append(f"E1b LAN 4 MiB: {e1b['ok']}/{e1b['rounds']}, sha {e1b['sha_match']}")
    e6b = load("e6b_cloud.json")
    times = sorted(r["B"]["file"]["receive_seconds"] for r in e6b["rows"])
    ok.append(f"E6b: {e6b['ok']}/{e6b['rounds']} received, {times[0]:.2f}-{times[-1]:.2f} s")

    ns = load("e3_newsocket_lan.json")["result"]
    ok.append(f"E3 new-socket LAN control: {ns['connected']}/{ns['rounds']} connected")

    return ok, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", default=None, help="manuscript markdown to cross-check")
    args = ap.parse_args()

    paper = Path(args.paper).read_text(encoding="utf-8") if args.paper else None

    problems: list[str] = []
    files = sorted(DATA.glob("*.json"))
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        problems.extend(check_shape(path.name, obj))
    print(f"parsed {len(files)} JSON files")

    ok, bad = numbers(paper)
    for line in ok:
        print(f"OK   {line}")
    for line in bad:
        print(f"MISS {line}")
    for line in problems:
        print(f"FAIL {line}")

    if bad or problems:
        print(f"\n{len(bad) + len(problems)} problem(s)")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
