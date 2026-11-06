#!/usr/bin/env python3
"""Recompute the paper's summary statistics from the per-round records.

The paper's tables are summaries; this script rebuilds them from `data/*.json`
so a reader can check the aggregation rather than trust it. It only reads the
package, never the original experiment directories.

Usage:
    python3 aggregate.py            # print all summary tables
    python3 aggregate.py --json     # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def load(name: str) -> dict | list:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def e3_arms() -> dict:
    """E3: per-arm success counts and QUIC medians, pooled over both batches."""
    out: dict[str, dict] = {}
    for batch in ("e3_netns_batch_summary.json", "e3_netns_batch2_summary.json"):
        for row in load(batch)["rows"]:
            g = out.setdefault(row["arm"], {"attempted": 0, "ok": 0, "quic_ms": []})
            g["attempted"] += 1
            if row["exit"] == 0 and row.get("a", {}).get("received"):
                g["ok"] += 1
                g["quic_ms"].append(row["a"]["quic"] * 1000)
    return {
        arm: {
            "attempted": g["attempted"],
            "ok": g["ok"],
            "median_quic_ms": round(st.median(g["quic_ms"]), 1) if g["quic_ms"] else None,
        }
        for arm, g in out.items()
    }


def e5_netem() -> dict:
    """E5: per-profile/per-size success counts and median transfer time."""
    out: dict[str, dict] = {}
    for row in load("e5_netem_summary.json")["rows"]:
        key = f"{row['profile']}/{row['size']}"
        g = out.setdefault(key, {"attempted": 0, "ok": 0, "seconds": []})
        g["attempted"] += 1
        if row["file"]["received"]:
            g["ok"] += 1
            g["seconds"].append(row["file"]["seconds"])
    return {
        key: {
            "attempted": g["attempted"],
            "ok": g["ok"],
            "median_s": round(st.median(g["seconds"]), 3) if g["seconds"] else None,
        }
        for key, g in out.items()
    }


def e5_decomp() -> dict:
    """The 100 ms + 1% loss re-runs, grouped by condition."""
    out: dict[str, list[float]] = {}
    for row in load("e5_netem_decomp.json")["rows"]:
        name = row["condition"].split("_rep")[0]
        if row["size"] != 4 << 20:
            continue
        secs = (row.get("a") or {}).get("file", {}).get("seconds")
        if secs:
            out.setdefault(name, []).append(secs)
    return {
        name: {
            "n": len(v),
            "median_s": round(st.median(v), 2),
            "min_s": round(min(v), 2),
            "max_s": round(max(v), 2),
        }
        for name, v in sorted(out.items())
    }


def session_phases() -> dict:
    """E1/E6: median setup split into mail wait and post-mail wait."""
    return {
        label: {
            side: data
            for side, data in group["sides"].items()
        }
        | {"probe_start_skew_s": group["probe_start_skew_median_s"],
           "connected_skew_s": group["connected_wallclock_skew_median_s"]}
        for label, group in load("sessions_phase_summary.json")["groups"].items()
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = {
        "E3 arms": e3_arms(),
        "E5 netem": e5_netem(),
        "E5 weak-network decomposition": e5_decomp(),
        "E1/E6 phase split": session_phases(),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    for section, body in result.items():
        print(f"\n== {section} ==")
        for key, value in body.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
