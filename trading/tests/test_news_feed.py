"""The news feed — the owner's design, display only.

Owner: "when i will fetch new universe for stocks, u will run the feed that
time only and it kept on that stock ... if it value it trigger or it pass",
then "no block just show".

These tests pin three things:
  1. the classifier is PURE and deterministic — same headline, same verdict,
     no network, no clock, no model;
  2. the two false readings found against the LIVE feed stay fixed (a
     fraud-detection headline must not read as a fraud event, and a headline
     that does not name the stock must not be judged against it);
  3. a failed fetch reads UNKNOWN, never "no news", and nothing anywhere can
     block a buy.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trading import news_feed as N


# ---------------------------------------------------------------------------
# 1. Classification is pure and deterministic
# ---------------------------------------------------------------------------

def test_classifier_is_deterministic_and_offline():
    title = "SEBI issues show-cause notice to XYZ over disclosure lapses"
    first = N.classify_headline(title)
    for _ in range(5):
        assert N.classify_headline(title) == first
    tags, side, risk, opp, _ = first
    assert "regulatory" in tags and side == N.RISK and risk > 0 and opp == 0


@pytest.mark.parametrize("title,tag,side", [
    ("Tata Steel shares rise 1.6% as Q1 results beat estimates",
     "results_beat", N.OPP),
    ("ABC Ltd bags Rs 2,400 crore order from Indian Railways",
     "order_win", N.OPP),
    ("XYZ wins Rs 900 cr contract for metro project", "order_win", N.OPP),
    ("Profit falls 18% as revenue declines", "results_miss", N.RISK),
    ("CFO resigns; auditor flags going-concern doubt", "governance", N.RISK),
    ("Brokerage downgrades LMN, cuts target to Rs 800", "downgrade", N.RISK),
    ("NCLT admits insolvency plea against DEF", "regulatory", N.RISK),
    ("Board approves acquisition of a cement unit", "expansion", N.OPP),
])
def test_event_tags_match_real_headline_shapes(title, tag, side):
    tags, got_side, _, _, _ = N.classify_headline(title)
    assert tag in tags and got_side == side


def test_deal_size_between_the_words_still_matches():
    """Indian headlines read 'bags Rs 2,400 crore order' — the words are not
    adjacent, which an earlier pattern required."""
    tags, _, _, opp, _ = N.classify_headline(
        "PQR bags Rs 12,000 crore order from NHAI for expressway")
    assert "order_win" in tags and opp > 0


def test_sentiment_only_scores_untagged_headlines():
    """A tagged event carries the weight; scoring sentiment too would double
    count the same headline."""
    _, _, _, _, sent = N.classify_headline("Profit falls 18% as revenue declines")
    assert sent == 0                       # tagged, so no lexicon score
    _, _, _, _, sent2 = N.classify_headline("Shares gain 2% on technical breakout")
    assert sent2 == 1                      # untagged, lexicon applies


# ---------------------------------------------------------------------------
# 2. The two false readings found against the live feed
# ---------------------------------------------------------------------------

def test_fraud_detection_is_not_a_fraud_event():
    """Found live: an RBI headline about AI 'fraud detection in banking' tagged
    a bank as a governance risk, which alone crosses the TRIGGER bar."""
    tags, _, risk, _, _ = N.classify_headline(
        "RBI Governor lists 5 ways AI can transform credit, services, "
        "inclusion & fraud detection in banking")
    assert tags == () and risk == 0.0
    # ...while a real fraud headline still tags
    tags2, _, risk2, _, _ = N.classify_headline(
        "Auditor alleges fraud at DEF Ltd, shares slump")
    assert "governance" in tags2 and risk2 > 0


def test_a_senior_exit_is_caught_whatever_the_title():
    """Found live: 'Senior Group President Rajat Monga Quits' was MISSED
    because the pattern only knew CFO and CEO."""
    for t in ("Yes Bank Senior Group President Rajat Monga Quits",
              "Managing Director exits ABC Ltd with immediate effect",
              "Chairman steps down at XYZ"):
        tags, _, _, _, _ = N.classify_headline(t)
        assert "governance" in tags, t


def test_headlines_must_actually_name_the_stock():
    """Google News answers a company query with sector round-ups. Judging one
    of those against this stock is how the RBI headline got attributed."""
    assert not N.mentions_company(
        "RBI Governor lists 5 ways AI can transform fraud detection in banking",
        "YESBANK.NS")
    assert N.mentions_company("Yes Bank Senior Group President Quits",
                              "YESBANK.NS")
    # the trading symbol alone is enough when prose uses the short name
    assert N.mentions_company("Reliance Stock And India Refiners Face A Test",
                              "RELIANCE.NS")


def test_quote_pages_are_dropped_as_noise():
    """Measured live: RELIANCE's top three 'headlines' were a live-price page,
    a chart page and a weekly-outlook template."""
    xml = "".join(
        f"<item><title>{t}</title><pubDate>Mon, 10 Aug 2026</pubDate>"
        f"<source url='x'>src</source></item>"
        for t in ("Reliance Industries Share Price - Live NSE: RELIANCE",
                  "RELIANCE Outlook for the Week (August 10, 2026)",
                  "Reliance wins Rs 500 crore contract for refinery unit"))
    kept = N._parse_rss(f"<rss>{xml}</rss>")
    assert len(kept) == 1 and "wins" in kept[0].title


# ---------------------------------------------------------------------------
# 3. Verdicts, and the fact that none of this can block a buy
# ---------------------------------------------------------------------------

def _h(title):
    return N.Headline(title=title, published="Mon, 10 Aug 2026", source="ET")


def test_verdict_triggers_only_above_the_bar():
    quiet = N.judge("X.NS", [_h("Shares gain 2% in line with the sector")],
                    "2026-08-14")
    assert quiet.verdict == "PASS"
    loud = N.judge("X.NS", [_h("SEBI opens probe into X's disclosures")],
                   "2026-08-14")
    assert loud.verdict == "TRIGGER" and "regulatory" in loud.tags
    assert "blocks nothing" in loud.why


def test_no_headlines_reads_no_news_not_pass():
    r = N.judge("X.NS", [], "2026-08-14")
    assert r.verdict == "NO_NEWS" and not r.fetch_failed


def test_failed_fetch_reads_unknown_never_no_news(monkeypatch):
    """'The fetch broke' and 'there is no news' are different facts and must
    never render as the same chip."""
    monkeypatch.setattr(N, "fetch_headlines", lambda *a, **k: None)
    r = N.read_stock("X.NS", "2026-08-14")
    assert r.verdict == "UNKNOWN" and r.fetch_failed is True
    assert "not 'no news'" in r.why


def test_run_is_bounded_by_the_owners_slot_logic(monkeypatch):
    """With 40 fundable slots there is no sense fetching hundreds of names."""
    seen = []

    def fake(t, days=7):
        seen.append(t)
        return []
    monkeypatch.setattr(N, "fetch_headlines", fake)
    N.run([f"S{i}.NS" for i in range(200)], asof="2026-08-14", limit=25)
    assert len(seen) == 25


def test_reading_serialises_for_storage():
    r = N.judge("X.NS", [_h("XYZ bags Rs 900 crore order")], "2026-08-14")
    d = r.to_dict()
    assert d["verdict"] == "TRIGGER" and d["headlines"][0]["side"] == N.OPP
    assert isinstance(d["tags"], list)          # JSON-safe for Mongo + state


def test_money_path_never_imports_the_news_feed():
    root = Path(N.__file__).resolve().parent
    for name in ("engine.py", "risk.py", "orders.py", "danger.py"):
        src = (root / name).read_text()
        assert "news_feed" not in src, (
            f"{name} must not import news_feed — display only, and news can "
            "never be calibrated (no archive), so it cannot earn a money-path "
            "place under the owner's REQUIRES_15Y_BACKTEST rule")


def test_module_states_it_uses_no_llm():
    """An LLM verdict would be unreproducible; the rules are auditable."""
    src = Path(N.__file__).read_text()
    assert "An LLM is NOT used" in src
    for forbidden in ("openai", "anthropic", "OPENAI_API_KEY"):
        assert forbidden not in src
