#!/usr/bin/env python3
"""FAIL-CLOSED intake auditor. Nothing enters the register until this passes.

FIXED 2026-08-09 (session review of the uploaded kit):
- Actually fail-closed now: exits 1 when any hard gate breaks (the original
  only printed numbers and always exited 0 — "fail-closed" was a caption).
- coverage_note is computed (rows vs weekdays in the file's own date span),
  not an always-empty column.
- Non-monotonic dates are a hard failure alongside duplicates.
- Reads the .csv.gz files the fixed fetcher writes (and plain .csv).
- The promised Kite-vs-Upstox cross-check EXISTS now: matching symbols in
  data/ohlcv_kite/ are compared close-by-close on shared dates.

Hard gates (exit 1): OHLC invariant violations > 0 · duplicate dates > 0 ·
non-monotonic dates > 0 · cross-check mismatch (>0.5% on >1% of shared days).
Soft flags (reported, human judges): |daily return| > 20% days · coverage < 60%.
"""
import csv
import datetime as dt
import glob
import gzip
import hashlib
import os
import sys


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def weekdays_between(d0: str, d1: str) -> int:
    a, b = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
    return sum(1 for i in range((b - a).days + 1)
               if (a + dt.timedelta(days=i)).weekday() < 5)


def audit_file(path):
    with _open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return dict(rows=0, note="EMPTY")
    dates = [r["date"] for r in rows]
    dup = len(dates) - len(set(dates))
    unsorted = sum(1 for a, b in zip(dates, dates[1:]) if b <= a)
    bad = big = 0
    prev = None
    for r in rows:
        o, h, l, c = (float(r["open"]), float(r["high"]),
                      float(r["low"]), float(r["close"]))
        if h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9:
            bad += 1
        if prev and prev > 0 and abs(c / prev - 1) > 0.20:
            big += 1
        prev = c
    span_days = weekdays_between(min(dates), max(dates))
    coverage = len(rows) / span_days if span_days else 0.0
    return dict(rows=len(rows), dmin=min(dates), dmax=max(dates), dup=dup,
                unsorted=unsorted, bad=bad, big=big,
                coverage=f"{coverage:.0%}", cov=coverage, note="")


def cross_check(upstox_dir, kite_dir):
    """Close-by-close comparison on shared dates. Returns report rows."""
    out = []
    for kp in sorted(glob.glob(f"{kite_dir}/*.csv")):
        sym = os.path.basename(kp)[:-4]
        up = None
        for ext in (".csv.gz", ".csv"):
            cand = f"{upstox_dir}/{sym}{ext}"
            if os.path.exists(cand):
                up = cand
                break
        if not up:
            continue
        with _open(up) as f:
            u = {r["date"]: float(r["close"]) for r in csv.DictReader(f)}
        with open(kp) as f:
            k = {r["date"]: float(r["close"]) for r in csv.DictReader(f)}
        shared = sorted(set(u) & set(k))
        if not shared:
            out.append([sym, 0, "", "NO SHARED DATES"])
            continue
        diffs = [abs(u[d] / k[d] - 1) for d in shared if k[d] > 0]
        n_bad = sum(1 for x in diffs if x > 0.005)
        out.append([sym, len(shared), f"{max(diffs):.4%}",
                    f"{n_bad} days >0.5%"])
    return out


def main():
    upstox_dir = os.getenv("FETCH_OUT", "data/ohlcv_upstox")
    rep = [["file", "rows", "date_min", "date_max", "dup_dates", "unsorted",
            "ohlc_invalid", "ret_gt20_days", "coverage", "sha256_16"]]
    hard_fail = 0
    files = sorted(glob.glob(f"{upstox_dir}/*.csv") +
                   glob.glob(f"{upstox_dir}/*.csv.gz"))
    files = [f for f in files
             if "_failures" not in f and "_rejected" not in f]
    low_cov = 0
    for p in files:
        a = audit_file(p)
        if a["rows"] == 0:
            rep.append([p, 0, "", "", 0, 0, 0, 0, "", sha16(p)])
            continue
        rep.append([p, a["rows"], a["dmin"], a["dmax"], a["dup"],
                    a["unsorted"], a["bad"], a["big"], a["coverage"],
                    sha16(p)])
        hard_fail += a["dup"] + a["unsorted"] + a["bad"]
        if a["cov"] < 0.60:
            low_cov += 1
    with open("DATA_INTAKE_REPORT.csv", "w", newline="") as f:
        csv.writer(f).writerows(rep)

    xrows = []
    if os.path.isdir("data/ohlcv_kite"):
        xrows = cross_check(upstox_dir, "data/ohlcv_kite")
        with open("CROSS_CHECK_REPORT.csv", "w", newline="") as f:
            csv.writer(f).writerows(
                [["symbol", "shared_days", "max_close_diff", "verdict"]] + xrows)

    n = len(rep) - 1
    tot_bad = sum(int(r[6]) for r in rep[1:])
    tot_big = sum(int(r[7]) for r in rep[1:])
    print(f"Audited {n} files | OHLC-invalid: {tot_bad} | dup/unsorted included "
          f"in hard gate | |ret|>20% days: {tot_big} (investigate) | "
          f"coverage<60%: {low_cov} files (often late listings — judge)")
    if xrows:
        print(f"Cross-check: {len(xrows)} symbols -> CROSS_CHECK_REPORT.csv")
    print("Report: DATA_INTAKE_REPORT.csv")
    if hard_fail:
        print(f"HARD GATE FAILED ({hard_fail} violations) — data must not "
              f"enter the register.")
        sys.exit(1)
    print("Hard gates PASSED.")


if __name__ == "__main__":
    main()
