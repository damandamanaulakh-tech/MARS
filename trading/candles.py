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


# ---- Owner ask 2026-07-28 ("i knw u have few but i want more"): the
# reference-chart set. Multi-bar reversals join the entry-confirmation
# list; single-bar shapes are lanes/warnings only. Bearish still never
# buys. ----------------------------------------------------------------

def _bar(df: pd.DataFrame, i: int) -> tuple[float, float, float, float]:
    return tuple(float(df[k].iloc[i]) for k in ("Open", "High", "Low", "Close"))


def three_inside_up(df: pd.DataFrame) -> bool:
    """Bullish harami, then a third green bar closing above the red bar's open."""
    if not _ok(df, 3):
        return False
    o3, _, _, c3 = _bar(df, -1)
    return bullish_harami(df.iloc[:-1]) and c3 > o3 and c3 > float(df["Open"].iloc[-3])


def three_outside_up(df: pd.DataFrame) -> bool:
    """Bullish engulfing, then a third green bar closing higher still."""
    if not _ok(df, 3):
        return False
    o3, _, _, c3 = _bar(df, -1)
    return bullish_engulfing(df.iloc[:-1]) and c3 > o3 and c3 > float(df["Close"].iloc[-2])


def three_line_strike(df: pd.DataFrame) -> bool:
    """Three falling red bars, then one green bar striking back above the
    first red bar's open — the whole decline reversed in a day."""
    if not _ok(df, 4):
        return False
    closes = []
    for i in (-4, -3, -2):
        o, _, _, c = _bar(df, i)
        if c >= o:
            return False
        closes.append(c)
    if not closes[0] > closes[1] > closes[2]:
        return False
    o, _, _, c = _bar(df, -1)
    return c > o and o <= closes[2] and c > float(df["Open"].iloc[-4])


def abandoned_baby_bull(df: pd.DataFrame) -> bool:
    """Red bar, a doji gapping fully below it, then a green bar gapping
    fully back up — sellers abandoned overnight."""
    if not _ok(df, 3):
        return False
    o1, _, l1, c1 = _bar(df, -3)
    o2, h2, l2, c2 = _bar(df, -2)
    o3, _, l3, c3 = _bar(df, -1)
    is_doji = abs(c2 - o2) <= 0.1 * max(h2 - l2, _EPS)
    return c1 < o1 and is_doji and h2 < l1 and c3 > o3 and l3 > h2


def kicker_bull(df: pd.DataFrame) -> bool:
    """Red bar, then a green bar that OPENS above the red bar's open and
    never trades back into it — the sharpest one-bar reversal."""
    if not _ok(df, 2):
        return False
    o1, _, _, c1 = _bar(df, -2)
    o2, _, l2, c2 = _bar(df, -1)
    return c1 < o1 and o2 > o1 and c2 > o2 and l2 >= o1


def dragonfly_doji(df: pd.DataFrame) -> bool:
    """Doji with a long lower tail after a fall — sellers pushed and were
    fully rejected. Needs next-day confirmation (lane only, no gate)."""
    if not _ok(df, 6):
        return False
    o, h, l, c = _bar(df, -1)
    rng = max(h - l, _EPS)
    downtrend = float(df["Close"].iloc[-2]) < float(df["Close"].iloc[-6])
    return (downtrend and abs(c - o) <= 0.1 * rng
            and (min(o, c) - l) >= 0.6 * rng and (h - max(o, c)) <= 0.1 * rng)


def morning_doji_star(df: pd.DataFrame) -> bool:
    """Morning star whose pause bar is a true doji — stronger indecision."""
    if not _ok(df, 3):
        return False
    o1, _, _, c1 = _bar(df, -3)
    o2, h2, l2, c2 = _bar(df, -2)
    o3, _, _, c3 = _bar(df, -1)
    is_doji = abs(c2 - o2) <= 0.1 * max(h2 - l2, _EPS)
    return (c1 < o1 and is_doji and c2 < c1 + abs(c1 - o1) * 0.1
            and c3 > o3 and c3 > (o1 + c1) / 2)


def three_inside_down(df: pd.DataFrame) -> bool:
    """Bearish harami, then a third red bar closing below the green's open."""
    if not _ok(df, 3):
        return False
    o3, _, _, c3 = _bar(df, -1)
    return bearish_harami(df.iloc[:-1]) and c3 < o3 and c3 < float(df["Open"].iloc[-3])


def three_outside_down(df: pd.DataFrame) -> bool:
    """Bearish engulfing, then a third red bar closing lower still."""
    if not _ok(df, 3):
        return False
    o3, _, _, c3 = _bar(df, -1)
    return bearish_engulfing(df.iloc[:-1]) and c3 < o3 and c3 < float(df["Close"].iloc[-2])


def abandoned_baby_bear(df: pd.DataFrame) -> bool:
    """Green bar, a doji gapping fully above it, then a red bar gapping
    fully back down — buyers abandoned overnight."""
    if not _ok(df, 3):
        return False
    o1, h1, _, c1 = _bar(df, -3)
    o2, h2, l2, c2 = _bar(df, -2)
    o3, h3, _, c3 = _bar(df, -1)
    is_doji = abs(c2 - o2) <= 0.1 * max(h2 - l2, _EPS)
    return c1 > o1 and is_doji and l2 > h1 and c3 < o3 and h3 < l2


def kicker_bear(df: pd.DataFrame) -> bool:
    """Green bar, then a red bar opening below its open and never looking back."""
    if not _ok(df, 2):
        return False
    o1, _, _, c1 = _bar(df, -2)
    o2, h2, _, c2 = _bar(df, -1)
    return c1 > o1 and o2 < o1 and c2 < o2 and h2 <= o1


def gravestone_doji(df: pd.DataFrame) -> bool:
    """Doji with a long upper tail — the rally was sold all the way back."""
    if not _ok(df, 1):
        return False
    o, h, l, c = _bar(df, -1)
    rng = max(h - l, _EPS)
    return (abs(c - o) <= 0.1 * rng
            and (h - max(o, c)) >= 0.6 * rng and (min(o, c) - l) <= 0.1 * rng)


def hanging_man(df: pd.DataFrame) -> bool:
    """Hammer shape AFTER A RISE — the first crack in an uptrend."""
    if not _ok(df, 6):
        return False
    o, h, l, c = _bar(df, -1)
    rng = max(h - l, _EPS)
    uptrend = float(df["Close"].iloc[-2]) > float(df["Close"].iloc[-6])
    return (uptrend and (min(o, c) - l) >= 0.6 * rng
            and (h - max(o, c)) <= 0.15 * rng)


def evening_doji_star(df: pd.DataFrame) -> bool:
    """Evening star whose pause bar is a true doji — stronger top signal."""
    if not _ok(df, 3):
        return False
    o1, _, _, c1 = _bar(df, -3)
    o2, h2, l2, c2 = _bar(df, -2)
    o3, _, _, c3 = _bar(df, -1)
    is_doji = abs(c2 - o2) <= 0.1 * max(h2 - l2, _EPS)
    return (c1 > o1 and is_doji and c2 > c1 - abs(c1 - o1) * 0.1
            and c3 < o3 and c3 < (o1 + c1) / 2)


def bearish_harami(df: pd.DataFrame) -> bool:
    """Big green bar, then a small red body inside it — buying pressure fading."""
    if not _ok(df, 2):
        return False
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    o2, c2 = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    return (c1 > o1 and c2 < o2
            and max(o2, c2) <= c1 and min(o2, c2) >= o1
            and abs(c2 - o2) < abs(c1 - o1))


def spinning_top(df: pd.DataFrame) -> bool:
    """Small body with long shadows both sides — a genuine coin-flip day."""
    if not _ok(df, 1):
        return False
    o, h, l, c = _bar(df, -1)
    rng = max(h - l, _EPS)
    body = abs(c - o)
    return (0.1 * rng < body <= 0.3 * rng
            and (h - max(o, c)) >= 0.2 * rng and (min(o, c) - l) >= 0.2 * rng)


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
    "three_inside_up": three_inside_up,
    "three_outside_up": three_outside_up,
    "three_line_strike": three_line_strike,
    "abandoned_baby_bull": abandoned_baby_bull,
    "kicker_bull": kicker_bull,
    "dragonfly_doji": dragonfly_doji,
    "morning_doji_star": morning_doji_star,
}
BEARISH_PATTERNS = {
    "bearish_engulfing": bearish_engulfing,
    "evening_star": evening_star,
    "dark_cloud_cover": dark_cloud_cover,
    "tweezer_top": tweezer_top,
    "three_black_crows": three_black_crows,
    "shooting_star": shooting_star,
    "three_inside_down": three_inside_down,
    "three_outside_down": three_outside_down,
    "abandoned_baby_bear": abandoned_baby_bear,
    "kicker_bear": kicker_bear,
    "gravestone_doji": gravestone_doji,
    "hanging_man": hanging_man,
    "evening_doji_star": evening_doji_star,
    "bearish_harami": bearish_harami,
}
NEUTRAL_PATTERNS = {
    "doji": doji,
    "marubozu": marubozu,
    "spinning_top": spinning_top,
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


# Gate-capable bullish structures: multi-bar reversals with a completed
# confirmation bar. Single-bar shapes that "need next-day follow-up"
# (inverted_hammer, bullish_harami, dragonfly_doji) stay lanes-only.
_PATTERN_FUNCS = {
    "engulfing": bullish_engulfing,
    "hammer": hammer,
    "morning_star": morning_star,
    "piercing": piercing_line,
    "tweezer_bottom": tweezer_bottom,
    "three_soldiers": three_white_soldiers,
    "three_inside_up": three_inside_up,
    "three_outside_up": three_outside_up,
    "three_line_strike": three_line_strike,
    "abandoned_baby_bull": abandoned_baby_bull,
    "kicker_bull": kicker_bull,
    "morning_doji_star": morning_doji_star,
}


def candle_gate_applies(cfg) -> bool:
    """True when this category's entries must wait for a candle pattern.

    The ONE predicate the live engine and the backtest both read, so the
    two can never disagree (owner order 2026-10-04: off for momentum)."""
    if not getattr(cfg, "candle_confirm_enabled", False):
        return False
    skip = tuple(getattr(cfg, "candle_confirm_skip_modes", ()) or ())
    return getattr(cfg, "mode", None) not in skip


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
