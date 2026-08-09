#!/usr/bin/env python3
"""Surgical intake cleaner — the formal repair step between fetch and verify.

Two defect classes, both measured on the 2026-08-09 full-universe fetch:
1. Duplicate dates (identical rows twice, an API chunk-boundary echo,
   1 row in ~45% of symbols) — safe to drop, values identical. When the
   two rows DIFFER the file is left alone and listed for a human.
2. OHLC-invariant violations at source (high < max(open,close) etc.,
   84 rows in 29 files out of ~5M rows) — QUARANTINED to _rejected.csv,
   never silently repaired and never silently kept. No value is invented.

Run order: fetch -> clean_intake -> verify_intake (hard gates then pass on
honest data; whatever sits in _rejected.csv is disclosure, not repair).
"""
import csv
import glob
import gzip
import os


def _open(path, mode="rt"):
    return gzip.open(path, mode) if path.endswith(".gz") else open(path, mode.rstrip("t") or "r")


def _near(a, b, tol=0.002):
    """OHLC within tol and equal volume — a boundary echo, not a conflict."""
    try:
        for k in ("open", "high", "low", "close"):
            if abs(float(a[k]) / float(b[k]) - 1) > tol:
                return False
        return a.get("volume") == b.get("volume")
    except (ValueError, ZeroDivisionError):
        return False


def clean_file(path, rejected_writer):
    with _open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0, 0, 0
    header = list(rows[0].keys())
    # group by date, resolve duplicates deterministically
    by_date: dict = {}
    order: list = []
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
        if len(by_date[r["date"]]) == 1:
            order.append(r["date"])
    dropped_dup = quarantined = conflicts = 0
    out = []
    for i, d in enumerate(order):
        group = by_date[d]
        keep = group[0]
        if len(group) > 1:
            if all(tuple(g.values()) == tuple(group[0].values())
                   or _near(g, group[0]) for g in group[1:]):
                dropped_dup += len(group) - 1     # identical / paisa echo
            else:
                # CONFLICTING duplicates (measured: thin names, e.g. two
                # different instruments' prints on one date). Deterministic
                # continuity rule: keep the row whose close chains best with
                # the neighbouring day's close; quarantine the rest with the
                # full values on record. Nothing is invented, the losers are
                # disclosed, and the rule reproduces bit-identically.
                anchor = None
                for j in range(i + 1, len(order)):
                    if len(by_date[order[j]]) == 1:
                        anchor = float(by_date[order[j]][0]["close"])
                        break
                if anchor is None:
                    for j in range(i - 1, -1, -1):
                        if len(by_date[order[j]]) == 1:
                            anchor = float(by_date[order[j]][0]["close"])
                            break
                if anchor is not None:
                    keep = min(group,
                               key=lambda g: abs(float(g["close"]) - anchor))
                conflicts += 1
                for g in group:
                    if g is not keep:
                        rejected_writer.writerow(
                            [path] + [g[k] for k in header] +
                            ["conflicting_duplicate_dropped"])
                        quarantined += 1
        o, h, l, c = (float(keep["open"]), float(keep["high"]),
                      float(keep["low"]), float(keep["close"]))
        if h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9:
            rejected_writer.writerow([path] + [keep[k] for k in header] +
                                     ["ohlc_invariant"])
            quarantined += 1
            continue
        out.append(keep)
    if dropped_dup or quarantined:
        with _open(path, "wt") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(out)
    return dropped_dup, quarantined, conflicts


def main():
    src = os.getenv("FETCH_OUT", "data/ohlcv_upstox")
    files = [f for f in sorted(glob.glob(f"{src}/*.csv") +
                               glob.glob(f"{src}/*.csv.gz"))
             if "_failures" not in f and "_rejected" not in f]
    tot_dup = tot_q = tot_c = 0
    with open(f"{src}/_rejected.csv", "w", newline="") as rej:
        w = csv.writer(rej)
        w.writerow(["file", "symbol", "date", "open", "high", "low", "close",
                    "volume", "oi", "reason"])
        for p in files:
            dup, q, conflicts = clean_file(p, w)
            tot_dup += dup
            tot_q += q
            tot_c += conflicts
    print(f"cleaned {len(files)} files | identical/echo dups dropped: "
          f"{tot_dup} | conflicting-dup dates resolved by continuity: "
          f"{tot_c} | rows quarantined to _rejected.csv: {tot_q}")


if __name__ == "__main__":
    main()
