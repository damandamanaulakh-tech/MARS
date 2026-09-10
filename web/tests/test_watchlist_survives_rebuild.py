"""Item 8: the weekly universe rebuild must not lose the watchlist.

Owner order 2026-09-10, verbatim: "you add keep the entire watch list in new
download on friday (x+watch list =2400), so some current work done on watch
list must be kept, (also in future i can track the n see why its in watch and
check many other parameter)".

And the clarification that settles the other half of item 8: "universe means
the stocks app will analysis for trade - do not even think or touch or dlt the
years data - universe mean 49 i added, and all that stock we choose not the
data files". So this rebuilds the LIST and never touches stored candles.
"""
from web import scheduler, state


def _doc(ticker, close, vol, bars=20):
    return {"ticker": ticker,
            "candles": [["2026-09-01", close, close, close, close, vol]
                        for _ in range(bars)]}


def test_a_watched_name_below_the_turnover_cut_is_carried(monkeypatch):
    """THE GAP. always_include carried HELD names only, so a name the trigger
    layer had flagged WATCH dropped out of the scanned universe the moment it
    fell below the liquidity cut — taking its accumulated reasoning with it.
    A watched name is watched precisely because it is not a position yet."""
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr("web.db.history_tails", lambda rows: [
        _doc("BIG.NS", 500.0, 1_000_000),
        _doc("WATCHED.NS", 2.0, 1_000),        # far below the Rs 1 Cr floor
    ])
    cfg, p = state.cfg(), state.portfolio()
    old_u, old_t, old_n = cfg.custom_universe, p.trigger_watch, p.news_watch
    try:
        # BOTH watch surfaces are isolated. Leaving news_watch alone let a
        # prior test's names into `keep`, which made an exact-list assertion
        # fail while the behaviour under test was fine — the shared-state
        # pollution class this suite has hit before.
        p.trigger_watch = {"watchlist": ["WATCHED.NS"], "rows": {}}
        p.news_watch = {}
        scheduler.refresh_liquid_universe("test")
        assert "WATCHED.NS" in cfg.custom_universe, \
            "a watched name must survive the rebuild"
        assert "BIG.NS" in cfg.custom_universe
        b = p.universe_batch
        # Membership, not an exact list: what is pinned is that the watched
        # name was carried IN SPITE OF its rank, which is the owner's rule.
        assert "WATCHED.NS" in b["watchlist_carried"]
        assert "BIG.NS" not in b["watchlist_carried"], \
            "a name that earned its place by turnover is not 'carried'"
    finally:
        cfg.custom_universe, p.trigger_watch, p.news_watch = old_u, old_t, old_n


def test_a_news_triggered_name_is_carried_too(monkeypatch):
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr("web.db.history_tails", lambda rows: [
        _doc("BIG.NS", 500.0, 1_000_000)])
    cfg, p = state.cfg(), state.portfolio()
    old_u, old_n, old_t = cfg.custom_universe, p.news_watch, p.trigger_watch
    try:
        p.news_watch = {"triggered": ["NEWSNAME.NS"], "rows": {}}
        p.trigger_watch = {}
        scheduler.refresh_liquid_universe("test")
        assert "NEWSNAME.NS" in cfg.custom_universe
    finally:
        cfg.custom_universe, p.news_watch = old_u, old_n


def test_the_blocklist_still_wins_over_the_watchlist(monkeypatch):
    """A banned name must not re-enter the universe through the watch door."""
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr("web.db.history_tails", lambda rows: [
        _doc("BIG.NS", 500.0, 1_000_000)])
    cfg, p = state.cfg(), state.portfolio()
    old_u, old_t, old_n = cfg.custom_universe, p.trigger_watch, p.news_watch
    try:
        p.trigger_watch = {"watchlist": list(cfg.ticker_blocklist)}
        p.news_watch = {}
        scheduler.refresh_liquid_universe("test")
        for banned in cfg.ticker_blocklist:
            assert banned not in cfg.custom_universe, banned
    finally:
        cfg.custom_universe, p.trigger_watch, p.news_watch = old_u, old_t, old_n


def test_a_malformed_watch_doc_degrades_instead_of_crashing_the_block(monkeypatch):
    """These dicts are hydrated from Mongo. A partial doc must read as "no
    watchlist", never raise inside the Friday block — a crash there used to
    cost five other data feeds (the 2026-08-29 isolation fix)."""
    p = state.portfolio()
    old = p.trigger_watch
    try:
        for junk in ({}, {"watchlist": None}, {"watchlist": "NOTALIST"},
                     None, {"rows": {}}):
            p.trigger_watch = junk
            assert isinstance(scheduler._watchlist_names(p), set)
    finally:
        p.trigger_watch = old


def test_watchlist_names_reads_both_surfaces():
    p = state.portfolio()
    old_t, old_n = p.trigger_watch, p.news_watch
    try:
        p.trigger_watch = {"watchlist": ["A.NS", "B.NS"]}
        p.news_watch = {"triggered": ["B.NS", "C.NS"]}
        assert scheduler._watchlist_names(p) == {"A.NS", "B.NS", "C.NS"}
    finally:
        p.trigger_watch, p.news_watch = old_t, old_n


def test_the_rebuild_never_touches_stored_candles():
    """Owner: "do not even think or touch or dlt the years data". Source-pin:
    the universe job reads history tails and writes a LIST — it must contain
    no write path into the OHLCV store."""
    import inspect
    # STRIP COMMENTS FIRST. This exact test shape has tripped on its own
    # explanatory prose four times in this repo now (the F1 test, the
    # /positions hunt, the DAILY_SCHEDULER guard, and this one) — the word
    # "dropped" appears in a comment about names leaving the list. Assert on
    # OPERATIVE CODE only.
    code = "\n".join(
        ln.split("#")[0] for ln in
        inspect.getsource(scheduler.refresh_liquid_universe).splitlines()
        if not ln.strip().startswith("#"))
    for forbidden in ("save_history", "save_ohlcv", "top_up_history",
                      "delete_", "drop_", "replace_one"):
        assert forbidden not in code, forbidden
