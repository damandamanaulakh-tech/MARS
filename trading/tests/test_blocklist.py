"""Owner ticker blocklist — banned names never enter a scan universe."""

from trading.universes import apply_blocklist


def test_blocked_name_removed():
    assert apply_blocklist(["A.NS", "YESBANK.NS", "B.NS"], [], ("YESBANK.NS",)) \
        == ["A.NS", "B.NS"]


def test_held_blocked_name_stays_visible_for_exits():
    out = apply_blocklist(["A.NS", "YESBANK.NS"], ["YESBANK.NS"], ("YESBANK.NS",))
    assert "YESBANK.NS" in out


def test_empty_blocklist_is_identity():
    u = ["A.NS", "B.NS"]
    assert apply_blocklist(u, [], ()) == u


def test_default_config_blocks_yesbank():
    from trading.config import Config
    assert "YESBANK.NS" in Config().ticker_blocklist
