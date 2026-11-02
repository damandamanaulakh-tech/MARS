"""Reproduce the five-stock sensitivity illustration using only the standard library.

No production imports, network, settings writes, order calls or live-data reads.
All scenario inputs and flow-margin coefficients are illustrative assumptions.
"""
import csv
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def calculate(stock, scenario, assumptions):
    base, beta = stock["baseline_target"], stock["beta"]
    if not all(math.isfinite(x) for x in (base, beta)) or base <= 0:
        raise ValueError("A positive base and finite beta are required")
    market = scenario["market_pct"]
    sector = market if stock["sector_fallback"] else scenario["sector_pct"][stock["sector_index"]]
    relative = sector - market
    market_factor = 1 + beta * market / 100
    sector_factor = 1 + assumptions["sector_coefficient"] * relative / 100
    if min(market_factor, sector_factor) <= 0:
        raise ValueError("Scenario lies outside this simple positive-factor model")
    net = scenario["fpi_5d_cr"] + scenario["dii_5d_cr"]
    extra = min(assumptions["max_extra_flow_margin_pp"],
                max(0, -net / assumptions["flow_pressure_scale_cr"]
                    * assumptions["max_extra_flow_margin_pp"]))
    margin = assumptions["base_margin_pct"] + extra
    if not 0 <= margin < 100:
        raise ValueError("Invalid illustrative entry margin")
    market_value = base * market_factor
    adjusted = market_value * sector_factor
    entry = adjusted * (1 - margin / 100)
    return {
        "ticker": stock["ticker"], "scenario": scenario["name"],
        "source_asof": stock["price_asof"], "base_target": base,
        "reference_price": stock["reference_price"], "beta": beta,
        "market_pct": market, "sector_pct": sector,
        "sector_relative_pp": relative, "sector_fallback": stock["sector_fallback"],
        "market_impact_rs": market_value - base,
        "sector_impact_rs": adjusted - market_value,
        "adjusted_target": adjusted,
        "target_change_pct": (adjusted / base - 1) * 100,
        "conditional_low": stock["baseline_low"] * market_factor * sector_factor,
        "conditional_high": stock["baseline_high"] * market_factor * sector_factor,
        "fpi_5d_cr": scenario["fpi_5d_cr"], "dii_5d_cr": scenario["dii_5d_cr"],
        "net_flow_cr": net, "extra_flow_margin_pp": extra,
        "total_margin_pct": margin, "illustrative_entry_ceiling": entry,
        "entry_margin_rs": adjusted - entry,
        "target_vs_dated_price_pct": (adjusted / stock["reference_price"] - 1) * 100,
    }


def verify(inputs, results):
    assert len(results) == 15
    for r in results:
        assert math.isclose(r["base_target"] + r["market_impact_rs"]
                            + r["sector_impact_rs"], r["adjusted_target"], abs_tol=1e-9)
        assert r["illustrative_entry_ceiling"] < r["adjusted_target"]
        if r["scenario"] == "unchanged":
            assert r["adjusted_target"] == r["base_target"]
        if r["sector_fallback"]:
            assert r["sector_impact_rs"] == 0
    # Hand-check: base 100, beta 1, market -5%, sector -8% -> 92.15.
    sample = dict(inputs["stocks"][0], baseline_target=100, beta=1)
    r = calculate(sample, inputs["scenarios"][0], inputs["assumptions"])
    assert math.isclose(r["adjusted_target"], 92.15)
    assert math.isclose(r["illustrative_entry_ceiling"], 81.092)  # 12% margin
    # Changing only DII affects the margin, not the adjusted valuation.
    stress = inputs["scenarios"][0]
    no_offset = calculate(sample, dict(stress, dii_5d_cr=0), inputs["assumptions"])
    full_offset = calculate(sample, dict(stress, dii_5d_cr=12000), inputs["assumptions"])
    assert no_offset["adjusted_target"] == full_offset["adjusted_target"] == r["adjusted_target"]
    assert no_offset["total_margin_pct"] == 15
    assert full_offset["total_margin_pct"] == 10
    assert no_offset["illustrative_entry_ceiling"] < r["illustrative_entry_ceiling"] < full_offset["illustrative_entry_ceiling"]


def main():
    inputs = json.loads((HERE / "inputs.json").read_text())
    rows = [calculate(stock, scenario, inputs["assumptions"])
            for stock in inputs["stocks"] for scenario in inputs["scenarios"]]
    verify(inputs, rows)
    (HERE / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    with (HERE / "results.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print("PASS: 15 scenarios; unchanged-baseline, attribution, sector-fallback, hand-calculation and DII-offset checks.")
    for stock in inputs["stocks"]:
        rs = [r for r in rows if r["ticker"] == stock["ticker"]]
        print(stock["ticker"], "base", stock["baseline_target"],
              " | ".join(f'{r["scenario"]}: target {r["adjusted_target"]:.2f}, entry {r["illustrative_entry_ceiling"]:.2f}'
                         for r in rs))


if __name__ == "__main__":
    main()
