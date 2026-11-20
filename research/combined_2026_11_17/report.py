"""Render all sealed scenarios without choosing, retuning or replacing a policy."""
import argparse
import ast
from collections import defaultdict
import csv
import gzip
import io
import json
import zipfile
from pathlib import Path

from trading.validation import canonical, file_hash

HERE = Path(__file__).resolve().parent
LABELS = {"nifty200_momentum": "Momentum", "intraday": "Intraday", "short": "Short",
          "swing": "Swing", "positional": "Positional", "ocean": "Ocean-labelled holdings"}


def number(value, digits=2):
    return "Unknown / no sample" if value is None else f"{value:,.{digits}f}"


def table_csv(rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def render():
    result = json.loads(gzip.decompress((HERE / "results.json.gz").read_bytes()))
    protocol = json.loads((HERE / "protocol.json").read_text())
    seal = json.loads((HERE / "seal.json").read_text())
    manifest = json.loads((HERE / "manifest.json").read_text())
    opened = json.loads((HERE / "opened.json").read_text())
    summary = {**result, "outcomes": {k: r["summary"] for k, r in result["outcomes"].items()}}
    if canonical(summary) != canonical(json.loads((HERE / "summary.json").read_text())):
        raise ValueError("Summary differs from original numerical ledger")
    assert result["seal_sha256"] == file_hash(HERE / "seal.json")
    assert result["data_sha256"] == file_hash(HERE / "raw_responses.json.gz") == manifest["raw_sha256"]
    assert file_hash(HERE / "source.zip") == seal["source_archive_sha256"]
    assert set(result["outcomes"]) == {str(x) for x in protocol["slippage_bps_each_side"]}
    attribution = json.loads(gzip.decompress((HERE / "attribution.json.gz").read_bytes()))
    assert attribution["original_results_sha256"] == file_hash(HERE / "results.json.gz")
    assert attribution["amendment_sha256"] == file_hash(HERE / "attribution-amendment.json")
    assert attribution["reporting_source_sha256"] == file_hash(HERE / "attribution.py")
    assert attribution["decision_fill_cost_and_shared_book_parity"] is True
    for key, outcome in result["outcomes"].items():
        outcome["summary"]["modes"] = attribution["outcomes"][key]["modes"]
    saved = json.loads((HERE / "saved_settings.json").read_text())
    with zipfile.ZipFile(HERE / "source.zip") as archive:
        config_ast = ast.parse(archive.read("trading/config.py"))
    default_capital = next(ast.literal_eval(node.value) for node in ast.walk(config_ast)
                          if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                          and node.target.id == "starting_capital")
    start_capital = saved.get("starting_capital", default_capital)
    metrics, gates, coverage, trade_rows, exit_rows = [], [], [], [], []
    for bps in protocol["slippage_bps_each_side"]:
        outcome = result["outcomes"][str(bps)]
        reasons = defaultdict(list)
        for trade in outcome["trades"]:
            trade_rows.append({"slippage_bps_each_side": bps, **{key: trade[key] for key in
                ("trade_id", "ticker", "mode", "entry_date", "exit_date", "qty", "entry_price", "exit_price",
                 "gross_pnl", "costs", "net_pnl", "reason")},
                "cost_status": trade["cost_evidence"].get("status")})
            reasons[(trade["mode"], trade["reason"])].append(trade)
        for (mode, reason), legs in sorted(reasons.items()):
            exit_rows.append({"slippage_bps_each_side": bps, "segment": mode, "exit_trigger": reason,
                "legs": len(legs), "winning_legs": sum(t["net_pnl"] > 0 for t in legs),
                "losing_legs": sum(t["net_pnl"] < 0 for t in legs),
                "gross_pnl": round(sum(t["gross_pnl"] for t in legs), 2),
                "allocated_costs": round(sum(t["costs"] for t in legs), 2),
                "net_pnl": round(sum(t["net_pnl"] for t in legs), 2)})
        for mode, row in [("shared_book", outcome["summary"]), *outcome["summary"]["modes"].items()]:
            legs = [t for t in outcome["trades"] if mode == "shared_book" or t["mode"] == mode]
            metrics.append({"slippage_bps_each_side": bps, "segment": mode,
                **{key: row[key] for key in ("closed_trades", "exit_legs", "net_pnl", "net_expectancy",
                    "win_rate_pct", "loss_rate_pct", "max_drawdown_pct", "open_economic_trades",
                    "unknown_trades", "missing_equity_marks", "equity_observations", "final_equity")},
                "all_exit_legs_net_pnl": round(sum(t["net_pnl"] for t in legs), 2),
                "allocated_fees_on_exit_legs": round(sum(t["costs"] for t in legs), 2),
                "unverified_cost_legs": sum(t["cost_evidence"].get("status") != "paper_estimate" for t in legs)})
        gates += [{"slippage_bps_each_side": bps, "segment_gate_state": key, "count": count}
                  for key, count in sorted(outcome["summary"]["gates"].items())]
        coverage += [{"slippage_bps_each_side": bps, "observation": key, "count": count}
                     for key, count in sorted(outcome["summary"]["coverage"].items())]
    s = result["outcomes"]["5"]["summary"]
    raw = result["input_coverage"]
    requests = manifest["requests"]
    lines = ["# Goal 11 — Combined actual-strategy validation", "",
        "**Decision: no settings promotion. Forward performance acceptance remains pending.** "
        "The historical observations below are the actual engine's decisions on a bounded, newly acquired panel. "
        "They do not establish full-universe or future profitability.", "",
        "## Version and data evidence", "",
        f"- Frozen source commit: `{seal['source_commit']}`; [archived source](source.zip) contains every sealed strategy/input member.",
        f"- Source archive SHA256: `{seal['source_archive_sha256']}`.",
        "- The first reproduction exposed omitted non-Python dependencies in the source ZIP. "
        "[runtime-assets.json](runtime-assets.json) pins all 459 supplemental assets (203,326,589 bytes already in the repository) to the same pre-acquisition Git commit. "
        "Their bytes were verified against the original Git blob IDs when the manifest was created; reproduction checks SHA256 before isolated copying. Trigger parameters, measured side weights and IFR thresholds cannot silently fall back when reproducing. "
        "The original source ZIP, seal and outcomes remain unchanged; this packages existing inputs rather than adding market data or retuning settings.",
        f"- Raw provider archive SHA256: `{result['data_sha256']}`; [manifest](manifest.json) records URLs, request/receipt times and response digests.",
        "- The response digest hashes the canonical JSON encoding of the response-text string; the archive digest hashes the complete compressed file bytes.",
        f"- [Protocol](protocol.json), [saved settings](saved_settings.json), [source/runtime seal](seal.json) and [acquisition marker](opened.json) were fixed before price acquisition at {opened['opened_at']}. Consumed data is never relabelled unseen. Saved overrides combine with defaults and mode presets in the sealed source; absent saved fields are not silently set to zero.",
        f"- Window: **{protocol['start']} through {protocol['end']}, {s['sessions']} regular sessions, {s['tested_universe']}/{s['configured_universe']} configured names**. "
        "The 20 current survivors are disjoint from the 30 Goal08–10 names. This is not point-in-time full-universe membership or a claim that the companies never appeared in other research.",
        f"- {sum(r.get('status') == 200 for r in requests)}/{len(requests)} provider requests succeeded. "
        f"{raw.get('days_accepted_rows', 0):,} daily rows and {raw.get('minutes_accepted_rows', 0):,} completed regular-session five-minute rows are retained, including warm-up. "
        f"{sum(v for k,v in raw.items() if k.startswith('rejected:')):,} rows outside the supported schedule/validity rules are excluded explicitly.",
        "- Inputs come from [Upstox V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/). "
        "Current published Basic charges are a versioned paper assumption, not a claim about a historical account's contract notes; see [Goal05 cost evidence](../../docs/repairs/releases/2026-11-12-product-trading-costs.md).", "",
        "## What actually ran", "",
        "`TradingEngine.scan`, `refresh` and `square_off_intraday` execute the production scoring, timing, sizing, risk, rotation, exit and accounting code. "
        "Only provider I/O, clock, alerts and isolated-book persistence are adapted. No fixed-clock entries or patched eligibility scores replace strategy selection.", "",
        "A fresh INR 5 crore shared book starts without inherited trades or measured-Kelly history. Both real daily rotations run at 09:20 and 14:30 IST, "
        "with 30-minute refreshes and refreshes between categories, and intraday closing at 15:25. "
        "The simulation assumes instantaneous scans and complete supplied requests within the 120-name fetch cap. "
        "It excludes optional five-minute scans and unscheduled owner actions; it does not reproduce provider latency, outages or production's opening ledger.", "",
        "Daily indicators see only prior dates; five-minute indicators see only completed bars. Executions use the contemporaneous boundary open with adverse slippage on each side. "
        "Missing boundary quotes cannot fill an order. No future high/low path, prior close, old stop or retained valuation mark becomes an execution price. "
        "A gap can therefore exit beyond the stop. Network access during replay is forbidden.", "",
        "## Combined results after costs and slippage", "",
        "Net expectancy is the mean net INR result per fully closed economic trade; partial legs are grouped. "
        "Realised-leg P&L includes partial exits of still-open trades and is shown separately in metrics.csv. "
        "Marked equity includes cash, open holdings and fees already debited. The hypothetical liquidation estimate includes another adverse sell fill and estimated exit fees, without creating fabricated terminal trades.", "",
        "| Slippage each side | Closed trades | Net expectancy INR | Fully closed net P&L INR | Daily marked drawdown % | Scheduled drawdown % | Marked return % | Open holdings | Estimated liquidation equity INR |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for bps in protocol["slippage_bps_each_side"]:
        row = result["outcomes"][str(bps)]["summary"]
        ret = (row["final_equity"] / start_capital - 1) * 100 if row["final_equity"] is not None else None
        lines.append(f"| {bps} bps | {row['closed_trades']} | {number(row['net_expectancy'])} | {number(row['net_pnl'])} | "
            f"{number(row['max_drawdown_pct'], 4)} | {number(row['scheduled_observed_drawdown_pct'], 4)} | {number(ret, 4)} | "
            f"{len(row['open_positions'])} | {number(row['estimated_liquidation_equity'])} |")
    lines += ["", "Both drawdown series can miss unobserved intrabar lows. These three scenarios reuse the same market history; they are sensitivity tests, not independent samples. "
              "No scenario is selected after seeing its result.", "", "## Segment results at 5 bps per side", "",
              "| Segment | Closed trades | Exit legs | Net expectancy INR | Fully closed net P&L INR | Win % | Daily marked drawdown % | Still open |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for mode in protocol["mode_order"]:
        row = s["modes"][mode]
        lines.append(f"| {LABELS[mode]} | {row['closed_trades']} | {row['exit_legs']} | {number(row['net_expectancy'])} | "
            f"{number(row['net_pnl'])} | {number(row['win_rate_pct'])} | {number(row['max_drawdown_pct'], 4)} | {row['open_economic_trades']} |")
    lines += ["", "Segment curves start from each configured capital share and include net realised and marked open P&L; they are accounting attribution, not independent funded books. "
        "The original sealed segment curve mixed open budget categories with closed stored modes. A [declared reporting amendment](attribution-amendment.json) makes both use stored mode. "
        "[Corrected attribution](attribution.json.gz) is separate from the preserved original results; full replay proves every strategy decision, fill, fee, closed-trade count, expectancy and shared-book result unchanged. "
        "This correction uses consumed data and makes no new unseen-data claim. "
        "Ocean uses its real budget path but holdings retain their stored segment; it is not counted as another independent strategy sample. "
        "No closed sample means unknown expectancy, never a zero-loss or successful strategy.", "",
        "## Exit and cost attribution at 5 bps per side", "",
        "These are observed exit triggers, not proof that a different exit would have made money. "
        "Gross P&L already reflects adverse fill prices; allocated charges are then deducted. "
        "[trades.csv](trades.csv) lists every simulated closed leg with its entry, exit, quantity, reason, gross P&L, costs and net P&L. "
        "[exit_reasons.csv](exit_reasons.csv) covers every declared slippage scenario.", "",
        "| Segment | Exit trigger | Legs | Losing legs | Gross P&L INR | Charges INR | Net P&L INR |",
        "|---|---|---:|---:|---:|---:|---:|"]
    for row in exit_rows:
        if row["slippage_bps_each_side"] == 5:
            lines.append(f"| {LABELS[row['segment']]} | {row['exit_trigger']} | {row['legs']} | {row['losing_legs']} | "
                         f"{number(row['gross_pnl'])} | {number(row['allocated_costs'])} | {number(row['net_pnl'])} |")
    lines += ["",
        "## Coverage and unresolved limitations", "",
        f"- Each scenario executed {len(result['outcomes']['5']['decisions']):,} category scans across {s['sessions']} sessions. "
        "All declared scenarios, gate states, unavailable inputs and remaining holdings are preserved in the compressed result.",
        "- [metrics.csv](metrics.csv) includes every segment/scenario, partial-leg P&L, allocated realised-leg costs, unknown costs and mark coverage. "
        "[gates.csv](gates.csv) contains every recorded ranked-candidate gate/state count. [coverage.csv](coverage.csv) records requested/supplied provider observations. "
        "Counts repeat symbols over scans; they are not unique stocks or independent trades. Unreached candidates are not silently treated as passed gates.",
        "- Missing historical contexts: " + ", ".join(s["missing_inputs"]) + ".",
        "- Missing context follows the existing production fallback policy, with explicit unknown coverage. Neutral fallback values are not measured neutral market conditions. "
        "A 20-name cross-section changes breadth, correlation, rotation and IFR. Provider revisions, corporate adjustments and announcement-time lineage are not independently certified.",
        "- The daily /backtest route remains an approximation even after its sizing and terminal-fee fixes. It is not the source of this actual-engine study.", "",
        "## Forward paper observation", "",
        "The app's authenticated `/validation` page and `/api/validation` expose append-only observations from ordinary paper activity. "
        "Code/runtime/settings identities, compressed own-symbol entry bars, economic-trade and exit-leg identities, actual stored fees/fills, pending exports and capture failures remain inspectable. "
        "Manifests are stored once; each event waits for an acknowledged portfolio checkpoint and durable run manifest. Failed exports retry across restart. "
        "Inherited positions and earlier closes are baseline, not new forward trades. Owner entries, incomplete quantities, unverified costs and mixed exit versions are explicit.", "",
        "Forward cohort expectancy excludes inherited/owner entries. Cohort drawdown stays unknown when equity belongs to a shared mixed-version book; "
        "operational shared-book drawdown is labelled separately and removes recorded capital transfers. Own-symbol entry data does not reconstruct the full peer/provider snapshot.", "",
        "Acceptance remains open for at least 30 genuinely new regular sessions and 30 fully closed economic trades in each claimed segment, "
        "with complete cost/identity/coverage evidence. These floors are not statistical proof. No production scan/trade is induced and no setting is automatically promoted. "
        "Release and current observation status belong in the [release ledger](../../docs/repairs/releases/2026-11-17-combined-validation.md).", "",
        "## Exact offline reproduction", "", "```sh",
        "# Python 3.12.16",
        "pip install -r requirements.txt -r research/combined_2026_11_17/requirements.txt",
        "python -m research.combined_2026_11_17.reproduce",
        "python -m research.combined_2026_11_17.reproduce --attribution",
        "python -m research.combined_2026_11_17.report --check", "```", "",
        "Reproduction extracts and hashes the original source archive in an isolated directory, checks the frozen numerical runtime, and compares the entire numerical result byte-for-byte. "
        "Independent slippage scenarios run in separate fresh processes. The input acceleration removes older intraday rows which the original validator would discard anyway; accepted bars and all numerical results must still match the original sequential, unaccelerated run. "
        "The archive is preserved when later production instrumentation changes. Original raw data, consumed-marker and outcomes are never overwritten.", ""]
    return {"README.md": "\n".join(lines), "metrics.csv": table_csv(metrics),
            "gates.csv": table_csv(gates), "coverage.csv": table_csv(coverage),
            "trades.csv": table_csv(trade_rows), "exit_reasons.csv": table_csv(exit_rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, text in render().items():
        path = HERE / name
        if args.check:
            if not path.exists() or path.read_text() != text:
                raise ValueError("Report differs from sealed outcomes: " + name)
        else:
            path.write_text(text)
    print("Combined report and all scenario tables verified." if args.check else "Combined report rendered.")


if __name__ == "__main__":
    main()
