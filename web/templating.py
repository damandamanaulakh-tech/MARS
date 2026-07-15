"""Jinja templates. Adds helpers like `inr()` for Indian rupee formatting."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates


_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _logo_url() -> str:
    """Override logo by setting LOGO_URL in Render — e.g. your GitHub avatar
    URL https://github.com/<username>.png. Falls back to the bundled SVG."""
    override = os.environ.get("LOGO_URL", "").strip()
    return override or "/static/img/logo.svg"


def _logo_url_white() -> str:
    override = os.environ.get("LOGO_URL", "").strip()
    return override or "/static/img/logo-white.svg"


templates.env.globals["logo_url"] = _logo_url
templates.env.globals["logo_url_white"] = _logo_url_white


def inr(value: float, digits: int = 0) -> str:
    """Indian-grouping rupee formatter. 2_500_000 -> '25,00,000'."""
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    neg = n < 0
    n = abs(n)
    if digits:
        body = f"{n:.{digits}f}"
        whole, frac = body.split(".")
    else:
        whole = f"{int(round(n))}"
        frac = ""
    # Indian grouping: last 3, then every 2
    if len(whole) <= 3:
        out = whole
    else:
        tail = whole[-3:]
        head = whole[:-3]
        chunks = []
        while len(head) > 2:
            chunks.append(head[-2:])
            head = head[:-2]
        if head:
            chunks.append(head)
        chunks.reverse()
        out = ",".join(chunks) + "," + tail
    if frac:
        out = f"{out}.{frac}"
    return ("-" if neg else "") + out


def inr_lakh(value: float) -> str:
    """Compact: 2_500_000 -> '25.00 L', 12_000_000 -> '1.20 Cr'."""
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    abs_n = abs(n)
    sign = "-" if n < 0 else ""
    if abs_n >= 10_000_000:
        return f"{sign}{abs_n / 10_000_000:.2f} Cr"
    if abs_n >= 100_000:
        return f"{sign}{abs_n / 100_000:.2f} L"
    if abs_n >= 1000:
        return f"{sign}{abs_n / 1000:.2f} K"
    return f"{sign}{abs_n:.0f}"


def pct(value: float, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


templates.env.filters["inr"] = inr
templates.env.filters["inr_lakh"] = inr_lakh
templates.env.filters["pct"] = pct
