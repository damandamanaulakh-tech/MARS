import os, sys, types
import trading.providers as P
from trading.providers import Answer, dual_run, ask, _compare


def _stub(text, ok=True, err=""):
    return lambda prompt, **kw: Answer("stub", "m", text, ok, err)


def test_no_key_fails_cleanly_never_pretends():
    os.environ.pop("XAI_API_KEY", None)
    a = P.ask_grok("hi")
    assert not a.ok and "XAI_API_KEY not set" in a.error and a.text == ""


def test_xai_uses_xai_base_url_not_openai():
    assert P.XAI_BASE_URL == "https://api.x.ai/v1"
    assert "openai.com" not in P.XAI_BASE_URL


def test_dual_run_agree(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setitem(P.PROVIDERS, "grok", _stub("the pyramid dates to dynasty four per mortar radiocarbon"))
    monkeypatch.setitem(P.PROVIDERS, "claude", _stub("mortar radiocarbon places the pyramid in dynasty four"))
    v = dual_run("q", primary="grok", auditor="claude")
    assert v.urr == "AGREE" and "weak evidence" in v.note


def test_dual_run_disagree_is_flagged_human_required(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setitem(P.PROVIDERS, "grok", _stub("ancient electricity powered the temple lamps"))
    monkeypatch.setitem(P.PROVIDERS, "claude", _stub("lotus flower symbolism creation myth harsomtus"))
    v = dual_run("q", primary="grok", auditor="claude")
    assert v.urr == "DISAGREE" and "HUMAN-REQUIRED" in v.note


def test_one_provider_down_is_single_source_not_silently_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setitem(P.PROVIDERS, "grok", _stub("", False, "no key"))
    monkeypatch.setitem(P.PROVIDERS, "claude", _stub("an answer"))
    v = dual_run("q", primary="grok", auditor="claude")
    assert v.urr == "SINGLE-SOURCE" and "unaudited" in v.note


def test_both_down_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setitem(P.PROVIDERS, "grok", _stub("", False, "x"))
    monkeypatch.setitem(P.PROVIDERS, "claude", _stub("", False, "y"))
    assert dual_run("q", primary="grok", auditor="claude").urr == "FAILED"


def test_compare_never_picks_a_winner():
    verdict, note = _compare("alpha beta gamma", "delta epsilon zeta")
    assert verdict == "DISAGREE"
    assert "right" not in note.lower()   # it flags; it does not adjudicate


def test_ledger_written(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setitem(P.PROVIDERS, "grok", _stub("hello world testing"))
    ask("grok", "q")
    assert (tmp_path / "l.jsonl").read_text().count("model_call") == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
