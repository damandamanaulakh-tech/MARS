"""Owner-requested engine entries remain economic outcomes, not automatic edge."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from trading import forward_validation as fv, validation as v
from trading.config import Config
from trading.paper import ClosedTrade, PaperPosition, Portfolio

NOW = datetime(2026, 11, 26, 4, 30, tzinfo=timezone.utc)
EPOCH = "2026-11-26T04:00:00+00:00"


def authority(*, applied=True, original_path="scan"):
    return {"batch_id": "2026-11-26:request-one", "actor": "owner@example.test",
            "authorized_date_ist": "2026-11-26", "execution": "paper",
            "original_path": original_path,
            "override": {"scope": "measured_performance", "authorized": True,
                         "applied": applied}}


@pytest.fixture
def book(tmp_path, monkeypatch):
    def identity(cfg):
        return {"id": "run-" + cfg.mode, "model": v.MODEL,
                "settings": {"mode": cfg.mode, "starting_capital": 1000.},
                "code": {"sha256": "source"}, "settings_sha256": "settings-" + cfg.mode}
    monkeypatch.setattr(v, "identity", identity)
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    p = Portfolio(cfg=cfg, cash=1000., capital_base=1000.)
    p.validation_state = {"started_at": EPOCH}
    return p


def captured(book, *, batch=True, applied=True, original_path="scan", key="batch-trade"):
    decision = {"path": "owner_strategy_batch" if batch else original_path}
    if batch:
        decision["strategy_batch"] = authority(applied=applied, original_path=original_path)
    record = v.capture_entry(book, book.cfg, {"decision": decision}, NOW, 10)
    return {"trade_id": key, "validation": record}, decision


def position(evidence, qty=10, *, ocean=False):
    return PaperPosition("A.NS", qty, 100., NOW, "Test", 100., 80., "fixture",
                         mode="nifty200_momentum", stop_price=95., target_price=120.,
                         tags=["ocean"] if ocean else [], trade_evidence=deepcopy(evidence))


def closed(book, evidence, qty, net, exit_id, *, ocean=False):
    ev = deepcopy(evidence)
    ev["exit"] = {"id": exit_id, "validation_run": v.identity(book.cfg)}
    return ClosedTrade("A.NS", qty, NOW, NOW, 100., 99., net + 2., 2., net,
                       "STOP_LOSS", "Test", 80., "fixture",
                       mode="nifty200_momentum", tags=["ocean"] if ocean else [],
                       trade_evidence=ev, cost_evidence={"status": "paper_estimate"})


def expanded(events):
    """The Mongo reader joins manifests while leaving compressed inputs unread."""
    runs = {e["run"]["id"]: e["run"] for e in events if e["kind"] == "manifest"}
    out = deepcopy(events)
    for e in out:
        e["run"] = deepcopy(runs.get(e.get("run", {}).get("id"), e.get("run")))
    return out


@pytest.mark.parametrize("applied", [False, True])
def test_entry_capture_copies_exact_authority_before_later_request_mutation(book, applied):
    evidence, decision = captured(book, applied=applied)
    record = evidence["validation"]
    expected = authority(applied=applied)
    decision["strategy_batch"]["override"]["applied"] = not applied
    decision["strategy_batch"]["actor"] = "different@example.test"
    assert record["path"] == "owner_strategy_batch"
    assert record["strategy_batch"] == expected
    assert record["original_qty"] == 10


@pytest.mark.parametrize("path", ["scan", "ocean"])
def test_normal_entry_capture_retains_its_path_without_owner_authority(book, path):
    evidence, _ = captured(book, batch=False, original_path=path)
    assert evidence["validation"]["path"] == path
    assert "strategy_batch" not in evidence["validation"]


def test_authority_survives_portfolio_json_restart(book):
    evidence, _ = captured(book)
    book.positions["A.NS"] = position(evidence)
    assert book.save() is True
    restored = Portfolio.load_or_fresh(book.cfg)
    assert restored.positions["A.NS"].trade_evidence["validation"]["strategy_batch"] == authority()


def test_staged_entry_event_is_independent_of_mutable_position_evidence(book):
    evidence, _ = captured(book)
    book.positions["A.NS"] = position(evidence)
    fv.stage(book, NOW)
    event = next(e for e in book.validation_pending.values() if e["kind"] == "entry")
    before = deepcopy(event)
    record = book.positions["A.NS"].trade_evidence["validation"]
    record["strategy_batch"]["override"]["applied"] = False
    record["strategy_batch"]["actor"] = "later-review@example.test"
    assert event == before
    assert event["strategy_batch"] == authority()
    assert event["input"]["strategy_batch"] == authority()


@pytest.mark.parametrize("still_open", [False, True])
def test_exit_and_closed_before_save_fallback_retain_original_entry_authority(book, still_open):
    evidence, _ = captured(book)
    if still_open:
        book.positions["A.NS"] = position(evidence, qty=5)
    book.cfg.mode = "swing"  # Exit processing may use another current preset.
    book.trades.append(closed(book, evidence, 5 if still_open else 10, -12., "exit-one"))
    fv.stage(book, NOW)
    entry = next(e for e in book.validation_pending.values() if e["kind"] == "entry")
    exit_event = next(e for e in book.validation_pending.values() if e["kind"] == "exit")
    assert entry["strategy_batch"] == exit_event["strategy_batch"] == authority()
    assert entry["path"] == exit_event["path"] == "owner_strategy_batch"
    assert entry["position"]["qty"] == 10
    assert bool(entry.get("captured_from_closed_trade")) is not still_open
    assert exit_event["run"]["id"] == "run-nifty200_momentum"
    assert exit_event["exit_run"]["id"] == "run-swing"
    # Annotation changes cannot mutate an already staged immutable event.
    frozen = deepcopy(exit_event)
    book.trades[-1].trade_evidence["validation"]["strategy_batch"]["actor"] = "later@example.test"
    fv.stage(book, NOW)
    assert exit_event == frozen


def report_events(book, *, batch_path="owner_strategy_batch", applied=True, ocean=False):
    run = v.identity(book.cfg)
    batch = authority(applied=applied, original_path="ocean" if ocean else "scan")
    def event(kind, key, **kw):
        return {"kind": kind, "run": deepcopy(run), "book_epoch": EPOCH,
                "observed_at": NOW.isoformat(), "trade_id": key, **kw}
    def exit_leg(key, qty, net, **kw):
        row = {"trade_id": key, "qty": qty, "net_pnl": net, "costs": 2.,
               "reason": "STOP_LOSS", "mode": "nifty200_momentum",
               "tags": ["ocean"] if key == "batch" and ocean else [],
               "cost_evidence": {"status": "paper_estimate"}}
        return event("exit", key, trade=row, exit_code_sha256="source",
                     exit_settings_sha256="settings-nifty200_momentum", **kw)
    return [event("entry", "automatic", path="scan", position={"qty": 10, "mode": "nifty200_momentum"}),
            event("entry", "batch", path=batch_path, strategy_batch=batch,
                  position={"qty": 10, "mode": "nifty200_momentum"}),
            exit_leg("batch", 5, -5., path=batch_path, strategy_batch=batch),
            exit_leg("batch", 5, -7., path=batch_path, strategy_batch=batch),
            exit_leg("automatic", 10, 20.),
            event("book", None, open_trade_ids=[]),
            event("equity", None, point=[EPOCH, 1000.], capital_base=1000.),
            event("equity", None, point=[NOW.isoformat(), 980.], capital_base=1000.)]


@pytest.mark.parametrize("applied", [False, True])
@pytest.mark.parametrize("batch_path", ["owner_strategy_batch", "scan"])
def test_batch_outcomes_are_separate_but_economic_losses_and_shared_equity_remain(book, applied, batch_path):
    events = report_events(book, applied=applied, batch_path=batch_path)
    before = deepcopy(events)
    result = fv.report(list(reversed(events)))  # Mongo returns newest first.
    auto = result["cohorts"][0]
    batch = result["owner_batch_cohorts"][0]
    assert auto["closed_trades"] == 1 and auto["net_pnl"] == 20.
    assert batch["closed_trades"] == 1 and batch["exit_legs"] == 2
    assert batch["net_pnl"] == -12. and batch["net_expectancy"] == -12.
    assert batch["automatic_strategy_evidence"] is False
    assert batch["override_authorized_entries"] == 1
    assert batch["performance_override_entries"] == int(applied)
    assert batch["batch_ids"] == [authority()["batch_id"]]
    assert result["excluded_owner_or_unknown_entries"] == 1
    economics = result["observed_economics"][0]
    assert economics["net_pnl"] == 8. and economics["costs"] == 6.
    assert economics["owner_batch_exit_legs"] == 2 and economics["exit_legs"] == 3
    assert economics["from"] == economics["through"] == NOW.isoformat()
    assert economics["time_scope"] == "retained_event_observation_timestamps"
    assert result["shared_book"]["max_drawdown_pct"] == 2.
    assert result["shared_book"]["final_equity"] == 980.
    assert events == before


def test_partial_owner_loss_is_retained_without_claiming_a_completed_trade(book):
    events = report_events(book)
    events = [e for e in events if not (e["kind"] == "exit" and e["trade_id"] == "batch" and e["trade"]["net_pnl"] == -7.)]
    next(e for e in events if e["kind"] == "book")["open_trade_ids"] = ["batch"]
    result = fv.report(events)
    batch = result["owner_batch_cohorts"][0]
    assert batch["closed_trades"] == 0 and batch["net_expectancy"] is None
    assert batch["open_economic_trades"] == 1
    assert result["observed_economics"][0]["net_pnl"] == 15.
    assert result["observed_economics"][0]["owner_batch_exit_legs"] == 1


def test_exit_only_query_cannot_relabel_batch_as_automatic_evidence(book):
    events = [e for e in report_events(book) if e["kind"] != "entry"]
    result = fv.report(events, truncated=True)
    batch = result["owner_batch_cohorts"][0]
    assert batch["closed_trades"] == 0 and batch["net_expectancy"] is None
    assert batch["exit_legs"] == 2 and batch["automatic_strategy_evidence"] is False
    assert batch["batch_ids"] == [authority()["batch_id"]]
    assert result["observed_economics"][0]["net_pnl"] == 8.
    assert result["query_truncated"] is True


def test_owner_path_without_authority_details_remains_excluded(book):
    events = report_events(book)
    for event in events:
        event.pop("strategy_batch", None)
    result = fv.report(events)
    batch = result["owner_batch_cohorts"][0]
    assert batch["automatic_strategy_evidence"] is False
    assert batch["authority_unavailable_entries"] == 1
    assert batch["batch_ids"] == []
    assert result["cohorts"][0]["net_pnl"] == 20.
    assert result["observed_economics"][0]["net_pnl"] == 8.


def test_owner_witness_does_not_reclassify_same_trade_key_from_another_epoch(book):
    events = report_events(book)
    automatic = []
    for event in events:
        if event.get("trade_id") != "batch":
            continue
        copy = deepcopy(event)
        copy["book_epoch"] = "prior-book"
        copy["path"] = "scan"
        copy.pop("strategy_batch", None)
        automatic.append(copy)
    result = fv.report(events + automatic)
    prior = next(row for row in result["cohorts"] if row["book_epoch"] == "prior-book")
    assert prior["closed_trades"] == 1 and prior["net_pnl"] == -12.
    assert len(result["owner_batch_cohorts"]) == 1


def test_ocean_batch_keeps_separate_allocated_segment_in_economic_reporting(book):
    result = fv.report(report_events(book, ocean=True))
    assert result["owner_batch_cohorts"][0]["mode"] == "ocean"
    segments = {row["mode"]: row for row in result["observed_economics"]}
    assert segments["ocean"]["net_pnl"] == -12.
    assert segments["nifty200_momentum"]["net_pnl"] == 20.


def test_missing_amounts_remain_unknown_and_do_not_mutate_recorded_rows(book):
    events = report_events(book)
    row = next(e["trade"] for e in events if e["kind"] == "exit" and e["trade_id"] == "batch")
    row["net_pnl"] = None
    row["costs"] = None
    row["cost_evidence"]["status"] = "unavailable"
    before = deepcopy(events)
    result = fv.report(events)
    economics = result["observed_economics"][0]
    assert economics["net_pnl"] is None and economics["known_net_pnl"] == 13.
    assert economics["costs"] is None and economics["known_costs"] == 4.
    assert economics["unknown_net_legs"] == economics["unknown_cost_legs"] == 1
    assert economics["unverified_cost_legs"] == 1
    assert result["owner_batch_cohorts"][0]["net_expectancy"] is None
    assert events == before


def test_unverified_but_recorded_costs_are_retained_outside_strategy_expectancy(book):
    events = report_events(book)
    for event in events:
        if event["kind"] == "exit" and event["trade_id"] == "batch":
            event["trade"]["cost_evidence"] = {"status": "legacy"}
    before = deepcopy(events)
    result = fv.report(events)
    assert result["owner_batch_cohorts"][0]["closed_trades"] == 0
    assert result["owner_batch_cohorts"][0]["unknown_cost_legs"] == 2
    economic = result["observed_economics"][0]
    assert economic["net_pnl"] == 8. and economic["costs"] == 6.
    assert economic["unverified_cost_legs"] == 2
    assert events == before


def test_entry_capture_and_stage_feed_the_report_after_restart(book):
    evidence, _ = captured(book)
    book.trades.append(closed(book, evidence, 10, -12., "fully-closed"))
    fv.stage(book, NOW)
    assert book.save() is True
    restored = Portfolio.load_or_fresh(book.cfg)
    events = expanded(list(restored.validation_pending.values()))
    result = fv.report(events)
    assert result["owner_batch_cohorts"][0]["net_pnl"] == -12.
    assert result["observed_economics"][0]["net_pnl"] == -12.
    assert not any(row["closed_trades"] for row in result["cohorts"])


def test_active_batch_cannot_relabel_protective_exit_of_preexisting_normal_entry(
        book, tmp_path, monkeypatch):
    from freezegun import freeze_time
    from trading import quote_evidence as qe
    from trading.tests.test_measured_kelly_wired_2026_10_02 import _fresh

    with freeze_time(NOW) as clock:
        engine = _fresh(tmp_path, monkeypatch, [])
        engine.portfolio.validation_state = {"started_at": EPOCH}
        engine.scan()  # Ordinary automatic entry, before any owner request.
        pos = engine.portfolio.positions["UPP.NS"]
        entry = deepcopy(pos.trade_evidence["entry"])
        validation = deepcopy(pos.trade_evidence["validation"])
        assert entry["path"] == validation["path"] == "scan"
        assert "strategy_batch" not in entry and "strategy_batch" not in validation

        # The web save wrapper may already have staged an open-book snapshot.
        # Reproduce that path explicitly, then give the later protective close
        # its own clock so latest-book selection cannot tie on frozen time.
        fv.stage(engine.portfolio, NOW)
        exit_at = NOW + timedelta(minutes=1)
        clock.move_to(exit_at)

        px = pos.stop_price * .99
        prices = qe.QuoteBatch({pos.ticker: px}, evidence={
            pos.ticker: qe.make(px, "upstox", exit_at, provider_at=exit_at)})
        monkeypatch.setattr(engine, "_absorb_corporate_actions", lambda *a: None)
        monkeypatch.setattr(engine, "_danger_pass", lambda *a: None)
        monkeypatch.setattr(engine, "_valuation_pass", lambda *a, **k: None)
        monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
        monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: prices)
        with engine.strategy_batch_request(
                batch_id=authority()["batch_id"], actor=authority()["actor"],
                authorized_date_ist="2026-11-26", override_measured_performance=True):
            assert engine._active_strategy_batch() is not None
            engine.refresh()  # Real protective STOP_LOSS while authority is active.
            fv.stage(engine.portfolio, exit_at)

        assert pos.ticker not in engine.portfolio.positions
        trade = engine.portfolio.trades[-1]
        assert trade.reason == "STOP_LOSS"
        assert trade.costs > 0 and trade.net_pnl < 0
        assert trade.trade_evidence["entry"] == entry
        assert trade.trade_evidence["validation"] == validation
        assert trade.trade_evidence["exit"]["path"] == "exit"
        assert "strategy_batch" not in trade.trade_evidence["exit"]
        events = expanded(list(engine.portfolio.validation_pending.values()))
        trade_events = [event for event in events if event["kind"] in ("entry", "exit")]
        assert {event["path"] for event in trade_events} == {"scan"}
        assert all("strategy_batch" not in event for event in trade_events)
        result = fv.report(events)
        assert result["owner_batch_cohorts"] == []
        assert result["excluded_owner_or_unknown_entries"] == 0
        assert result["cohorts"][0]["closed_trades"] == 1
        assert result["cohorts"][0]["net_pnl"] == round(trade.net_pnl, 2)
        assert result["observed_economics"][0]["owner_batch_exit_legs"] == 0
