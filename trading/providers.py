"""Sourceborn — swappable model providers + dual-run URR compare.

Implements the spec line: "Base model swappable: Claude · Grok · OpenAI ·
OpenRouter · local. One draft call per ask; the engine frames, files, doubts
and verifies around it."

xAI is OpenAI-wire-compatible: same request shape, different base_url. Nothing
routes through OpenAI. Requires: pip install openai

ENV:
    XAI_API_KEY          from console.x.ai
    ANTHROPIC_API_KEY    existing
    SB_MODEL_PRIMARY     default "grok"
    SB_MODEL_AUDITOR     default "claude"   (cross-model audit node)
    SB_SPEND_CAP_USD     default 5.00       (hard stop; xAI bills per token)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

XAI_BASE_URL = "https://api.x.ai/v1"
XAI_DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4.5")
CLAUDE_DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

LEDGER = os.environ.get("SB_MODEL_LEDGER", "data/model_ledger.jsonl")


@dataclass
class Answer:
    provider: str
    model: str
    text: str
    ok: bool = True
    error: str = ""
    usage: dict = field(default_factory=dict)


def _ledger(event: str, **kw):
    os.makedirs(os.path.dirname(LEDGER) or ".", exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                             "event": event, **kw}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- providers
def ask_grok(prompt: str, system: str = "", model: str = XAI_DEFAULT_MODEL,
             max_tokens: int = 800) -> Answer:
    key = os.environ.get("XAI_API_KEY", "")
    if not key:
        return Answer("grok", model, "", False, "XAI_API_KEY not set")
    try:
        from openai import OpenAI           # the SDK is just a wire format
        client = OpenAI(api_key=key, base_url=XAI_BASE_URL)   # -> xAI, not OpenAI
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        r = client.chat.completions.create(model=model, messages=msgs,
                                           max_tokens=max_tokens)
        u = getattr(r, "usage", None)
        return Answer("grok", model, r.choices[0].message.content or "",
                      usage={"in": getattr(u, "prompt_tokens", 0),
                             "out": getattr(u, "completion_tokens", 0)})
    except Exception as e:
        return Answer("grok", model, "", False, f"{type(e).__name__}: {e}")


def ask_claude(prompt: str, system: str = "", model: str = CLAUDE_DEFAULT_MODEL,
               max_tokens: int = 800) -> Answer:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return Answer("claude", model, "", False, "ANTHROPIC_API_KEY not set")
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=key)
        r = c.messages.create(model=model, max_tokens=max_tokens,
                              system=system or None,
                              messages=[{"role": "user", "content": prompt}])
        txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return Answer("claude", model, txt,
                      usage={"in": r.usage.input_tokens, "out": r.usage.output_tokens})
    except Exception as e:
        return Answer("claude", model, "", False, f"{type(e).__name__}: {e}")


PROVIDERS = {"grok": ask_grok, "claude": ask_claude}


def ask(provider: str, prompt: str, **kw) -> Answer:
    fn = PROVIDERS.get(provider)
    if not fn:
        return Answer(provider, "?", "", False, f"unknown provider '{provider}'")
    a = fn(prompt, **kw)
    _ledger("model_call", provider=a.provider, model=a.model, ok=a.ok,
            error=a.error, usage=a.usage, chars=len(a.text))
    return a


# ------------------------------------------------- dual run + URR compare
@dataclass
class DualVerdict:
    primary: Answer
    auditor: Answer
    urr: str            # AGREE / DISAGREE / SINGLE-SOURCE / FAILED
    note: str

    def as_dict(self):
        return {"urr": self.urr, "note": self.note,
                "primary": {"provider": self.primary.provider, "text": self.primary.text},
                "auditor": {"provider": self.auditor.provider, "text": self.auditor.text}}


def dual_run(prompt: str, system: str = "",
             primary: str = None, auditor: str = None) -> DualVerdict:
    """Run the same ask through two independent models. URR compares.

    This is the cross-model audit node: two vendors, two training sets, one
    question. Agreement is weak evidence. DISAGREEMENT IS THE PRODUCT — it
    marks exactly where a single model would have sounded confident and been
    unverified. Nothing is auto-promoted; the disagreement is surfaced, not
    resolved.
    """
    primary = primary or os.environ.get("SB_MODEL_PRIMARY", "grok")
    auditor = auditor or os.environ.get("SB_MODEL_AUDITOR", "claude")
    a = ask(primary, prompt, system=system)
    b = ask(auditor, prompt, system=system)

    if not a.ok and not b.ok:
        v = DualVerdict(a, b, "FAILED", f"both providers failed: {a.error} | {b.error}")
    elif not (a.ok and b.ok):
        good = a if a.ok else b
        v = DualVerdict(a, b, "SINGLE-SOURCE",
                        f"only {good.provider} answered — no independent check; "
                        "treat as unaudited")
    else:
        v = DualVerdict(a, b, "PENDING", "")
        v.urr, v.note = _compare(a.text, b.text)
    _ledger("dual_run", urr=v.urr, primary=a.provider, auditor=b.provider, note=v.note)
    return v


def _compare(x: str, y: str) -> tuple[str, str]:
    """Cheap lexical agreement check. Deliberately dumb and honest:
    it flags *possible* divergence for a human/URR node — it does not decide
    who is right. A model deciding which model is right is the thing URR exists
    to prevent."""
    def toks(s):
        return {w for w in "".join(c.lower() if c.isalnum() else " " for c in s).split()
                if len(w) > 3}
    tx, ty = toks(x), toks(y)
    if not tx or not ty:
        return "SINGLE-SOURCE", "one answer empty"
    j = len(tx & ty) / len(tx | ty)
    if j >= 0.45:
        return "AGREE", f"lexical overlap {j:.0%} — agreement is weak evidence, not proof"
    return "DISAGREE", (f"lexical overlap only {j:.0%} — models diverge. "
                        "HUMAN-REQUIRED: this is where a single model would have "
                        "sounded certain. Read both.")
