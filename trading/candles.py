"""Candle-pattern BUY-CONFIRMATION gate (Batch C — the user's "live
verification system").

A scored pick is only EXECUTED when the latest candle confirms it; otherwise
it waits (journaled as candle_wait_* and visible on the watchlist / ⚡card).

Patterns (any enabled pattern passing = confirmed):
  breakout  — close breaks the prior N-day high on strong volume, closing in
              the upper half of the day's range (default confirmation).
  urr       — Upthrust-Result-Retest: a breakout day inside the last K days,
              a retest that HELD the broken level, and price still above it.
  engulfing — bullish engulfing on the last two candles.

Daily bars by design: works EOD (09:20 scan uses yesterday's completed candle)
and is exactly backtestable on data/history_15stock. The same functions accept
intraday frames (UpstoxClient.get_intraday_candles) for the live 5-min layer.
"""

from __future__ import annotations

import pandas as pd

_EPS = 1e-9


def _ok(df: pd.DataFrame, need: int) -> bool:
    return (
        df is not None and not df.empty
        and all(c in df.columns for c in ("Open", "High", "Low", "Close"))
        and len(df.dropna(subset=["Close"])) >= need
    )


def breakout_candle(df: pd.DataFrame, lookback: int = 20,
                    vol_mult: float = 1.5) -> bool:
    """Latest close > prior `lookback`-day high, upper-half close, big volume."""
    if not _ok(df, lookback + 1):
        return False
    o, h, l, c = (float(df[k].iloc[-1]) for k in ("Open", "High", "Low", "Close"))
    prior_high = float(df["High"].iloc[-(lookback + 1):-1].max())
    if c <= prior_high:
        return False
    if (c - l) / max(h - l, _EPS) < 0.5:          # weak close = no conviction
        return False
    if "Volume" in df.columns:
        v = df["Volume"].dropna()
        if len(v) >= 21 and float(v.iloc[-1]) < vol_mult * float(v.iloc[-21:-1].mean()):
            return False
    return True


def bullish_engulfing(df: pd.DataFrame) -> bool:
    """Red candle fully engulfed by the next green candle."""
    if not _ok(df, 2):
        return False
    po, pc = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    lo, lc = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return pc < po and lc > lo and lo <= pc and lc >= po


def urr_confirmation(df: pd.DataFrame, lookback: int = 20,
                     window: int = 10, tol: float = 0.01) -> bool:
    """Upthrust-Result-Retest.

    level    = high of the `lookback` days BEFORE the last `window` days
    upthrust = a close above level inside the window
    retest   = after the upthrust, a low that came back to within `tol`
               of the level but CLOSED at/above it (the level held)
    result   = latest close still above the level
    """
    if not _ok(df, lookback + window + 1):
        return False
    level = float(df["High"].iloc[-(lookback + window):-window].max())
    win = df.iloc[-window:]
    closes = win["Close"].astype(float)
    lows = win["Low"].astype(float)
    up_idx = None
    for i in range(len(win) - 1):                 # upthrust before the last bar
        if float(closes.iloc[i]) > level:
            up_idx = i
            break
    if up_idx is None:
        return False
    retested = False
    for j in range(up_idx + 1, len(win)):
        if float(lows.iloc[j]) <= level * (1 + tol) and float(closes.iloc[j]) >= level * (1 - tol):
            retested = True
            break
    return retested and float(closes.iloc[-1]) > level


def hammer(df: pd.DataFrame) -> bool:
    """Long lower shadow (>=60% of range), tiny upper shadow — bottom reversal."""
    if not _ok(df, 6):
        return False
    o, h, l, c = (float(df[k].iloc[-1]) for k in ("Open", "High", "Low", "Close"))
    rng = max(h - l, _EPS)
    lower = min(o, c) - l
    upper = h - max(o, c)
    downtrend = float(df["Close"].iloc[-2]) < float(df["Close"].iloc[-6])
    return downtrend and lower >= 0.6 * rng and upper <= 0.15 * rng


def morning_star(df: pd.DataFrame) -> bool:
    """Big red -> small-body pause -> green closing above red bar's midpoint."""
    if not _ok(df, 3):
        return False
    o1, c1 = float(df["Open"].iloc[-3]), float(df["Close"].iloc[-3])
    o2, c2 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o3, c3 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    body1, body2 = abs(c1 - o1), abs(c2 - o2)
    return (c1 < o1 and body1 > _EPS
            and body2 <= 0.4 * body1 and c2 < c1 + body1 * 0.1
            and c3 > o3 and c3 > (o1 + c1) / 2)


def piercing_line(df: pd.DataFrame) -> bool:
    """Red bar, then green opening at/below its close and piercing above its midpoint."""
    if not _ok(df, 2):
        return False
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return c1 < o1 and c2 > o2 and o2 <= c1 and (o1 + c1) / 2 < c2 < o1


def tweezer_bottom(df: pd.DataFrame, tol: float = 0.0015) -> bool:
    """Two bars bottoming at the same low (within tol): red then green."""
    if not _ok(df, 2):
        return False
    l1, l2 = float(df["Low"].iloc[-2]), float(df["Low"].iloc[-1])
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return c1 < o1 and c2 > o2 and l1 > 0 and abs(l2 - l1) / l1 <= tol


def three_white_soldiers(df: pd.DataFrame) -> bool:
    """Three consecutive strong green bars, each closing higher."""
    if not _ok(df, 3):
        return False
    for i in (-3, -2, -1):
        o, h, l, c = (float(df[k].iloc[i]) for k in ("Open", "High", "Low", "Close"))
        if c <= o or (c - o) < 0.5 * max(h - l, _EPS):
            return False
    closes = [float(df["Close"].iloc[i]) for i in (-3, -2, -1)]
    return closes[0] < closes[1] < closes[2]


# ---- Bearish / warning family (owner ask 2026-07-26: "10-12 candle
# structures as parameters"). The 8 originals were ALL bullish, so the
# engine could never read a top. These never trigger a buy — they are
# read as warnings and shown on the desk. ----------------------------

def doji(df: pd.DataFrame, body_max: float = 0.1) -> bool:
    """Open and close nearly equal — indecision, trend may be pausing."""
    if not _ok(df, 1):
        return False
    o, h, l, c = (float(df[k].iloc[-1]) for k in ("Open", "High", "Low", "Close"))
    return abs(c - o) <= body_max * max(h - l, _EPS)


def shooting_star(df: pd.DataFrame) -> bool:
    """Long upper shadow (>=60% of range), tiny lower shadow — top reversal."""
    if not _ok(df, 1):
        return False
    o, h, l, c = (float(df[k].iloc[-1]) for k in ("Open", "High", "Low", "Close"))
    rng = max(h - l, _EPS)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return upper >= 0.6 * rng and lower <= 0.15 * rng


def inverted_hammer(df: pd.DataFrame) -> bool:
    """Shooting-star shape after a decline — potential bottom, needs follow-up."""
    if not _ok(df, 4):
        return False
    if not shooting_star(df):
        return False
    closes = [float(df["Close"].iloc[i]) for i in (-4, -1)]
    return closes[1] < closes[0]


def bearish_engulfing(df: pd.DataFrame) -> bool:
    """Green candle fully engulfed by the next red candle — top warning."""
    if not _ok(df, 2):
        return False
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1


def evening_star(df: pd.DataFrame) -> bool:
    """Big green -> small-body pause -> red closing below green's midpoint."""
    if not _ok(df, 3):
        return False
    o1, c1 = float(df["Open"].iloc[-3]), float(df["Close"].iloc[-3])
    o2, c2 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o3, c3 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    big_green = c1 > o1 and (c1 - o1) > 0
    small_body = abs(c2 - o2) <= 0.5 * max(abs(c1 - o1), _EPS)
    return big_green and small_body and c3 < o3 and c3 < (o1 + c1) / 2


def dark_cloud_cover(df: pd.DataFrame) -> bool:
    """Green bar, then red opening above its close and closing below midpoint."""
    if not _ok(df, 2):
        return False
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return c1 > o1 and o2 >= c1 and c2 < (o1 + c1) / 2 and c2 > o1


def tweezer_top(df: pd.DataFrame, tol: float = 0.0015) -> bool:
    """Two bars topping at the same high (within tol): green then red."""
    if not _ok(df, 2):
        return False
    h1, h2 = float(df["High"].iloc[-2]), float(df["High"].iloc[-1])
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return c1 > o1 and c2 < o2 and h1 > 0 and abs(h2 - h1) / h1 <= tol


def three_black_crows(df: pd.DataFrame) -> bool:
    """Three consecutive strong red bars, each closing lower — hard warning."""
    if not _ok(df, 3):
        return False
    for i in (-3, -2, -1):
        o, h, l, c = (float(df[k].iloc[i]) for k in ("Open", "High", "Low", "Close"))
        if c >= o or (o - c) < 0.5 * max(h - l, _EPS):
            return False
    closes = [float(df["Close"].iloc[i]) for i in (-3, -2, -1)]
    return closes[0] > closes[1] > closes[2]


def bullish_harami(df: pd.DataFrame) -> bool:
    """Big red bar, then a small green body inside it — pressure fading."""
    if not _ok(df, 2):
        return False
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return (c1 < o1 and c2 > o2
            and max(o2, c2) <= o1 and min(o2, c2) >= c1
            and abs(c2 - o2) < abs(c1 - o1))


def marubozu(df: pd.DataFrame, shadow_max: float = 0.05) -> bool:
    """Body fills the whole bar, almost no shadows — one side in full control."""
    if not _ok(df, 1):
        return False
    o, h, l, c = (float(df[k].iloc[-1]) for k in ("Open", "High", "Low", "Close"))
    rng = max(h - l, _EPS)
    return (h - max(o, c)) <= shadow_max * rng and (min(o, c) - l) <= shadow_max * rng


# Every structure the engine can read, in one place. BULLISH ones can
# confirm an entry; BEARISH/NEUTRAL ones never buy - they warn.
BULLISH_PATTERNS = {
    "breakout": None,          # needs lookback+vol args, handled separately
    "urr": None,               # same
    "engulfing": bullish_engulfing,
    "hammer": hammer,
    "morning_star": morning_star,
    "piercing": piercing_line,
    "tweezer_bottom": tweezer_bottom,
    "three_soldiers": three_white_soldiers,
    "inverted_hammer": inverted_hammer,
    "bullish_harami": bullish_harami,
}
BEARISH_PATTERNS = {
    "bearish_engulfing": bearish_engulfing,
    "evening_star": evening_star,
    "dark_cloud_cover": dark_cloud_cover,
    "tweezer_top": tweezer_top,
    "three_black_crows": three_black_crows,
    "shooting_star": shooting_star,
}
NEUTRAL_PATTERNS = {
    "doji": doji,
    "marubozu": marubozu,
}


def scan_all_patterns(df: pd.DataFrame, lookback: int = 20,
                      vol_mult: float = 1.5) -> dict:
    """Every structure's yes/no for one stock, on its latest bar.
    Returns {name: bool} across bullish + bearish + neutral - this is what
    makes each candle structure its own parameter instead of one lane."""
    out = {}
    try:
        out["breakout"] = breakout_candle(df, lookback, vol_mult)
    except Exception:
        out["breakout"] = False
    try:
        out["urr"] = urr_confirmation(df, lookback)
    except Exception:
        out["urr"] = False
    for group in (BULLISH_PATTERNS, BEARISH_PATTERNS, NEUTRAL_PATTERNS):
        for name, fn in group.items():
            if fn is None:
                continue
            try:
                out[name] = bool(fn(df))
            except Exception:
                out[name] = False
    return out


_PATTERN_FUNCS = {
    "engulfing": bullish_engulfing,
    "hammer": hammer,
    "morning_star": morning_star,
    "piercing": piercing_line,
    "tweezer_bottom": tweezer_bottom,
    "three_soldiers": three_white_soldiers,
}


def candle_confirmation(df: pd.DataFrame, cfg) -> tuple[bool, str]:
    """(confirmed?, which_pattern | why_not). Gate is cfg-driven."""
    if not getattr(cfg, "candle_confirm_enabled", False):
        return True, "disabled"
    lookback = int(getattr(cfg, "candle_breakout_lookback", 20))
    vol_mult = float(getattr(cfg, "candle_volume_mult", 1.5))
    patterns = list(getattr(cfg, "candle_patterns", ["breakout", "urr", "engulfing"]))
    if not _ok(df, lookback + 11):
        return False, "insufficient_bars"
    if "breakout" in patterns and breakout_candle(df, lookback, vol_mult):
        return True, "breakout"
    if "urr" in patterns and urr_confirmation(df, lookback):
        return True, "urr"
    for name in patterns:
        fn = _PATTERN_FUNCS.get(name)
        if fn is not None and fn(df):
            return True, name
    return False, "no_pattern"
