"""Triple-barrier labeling for the model factory (pure-Python, no extra deps).

A candidate long entry at bar ``i`` is labeled by which barrier is hit first as
price moves forward:

    TP = entry + tp_atr_mult * ATR_i   -> hit first  => win  (label 1)
    SL = entry - sl_atr_mult * ATR_i   -> hit first  => loss (label 0)
    time = i + max_hold_bars           -> neither hit => labeled by close vs entry

This matches how the live bot actually exits (TP/SL/timeout), so the model learns
"probability this trade reaches TP before SL" instead of a vague "price up later".

See ``training/DESIGN.md`` section 4.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas import Candle


def atr_series(candles: list[Candle], period: int = 14) -> list[float | None]:
    """Rolling-average Average True Range (simple MA of true range)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n == 0:
        return out
    trs: list[float] = []
    for i in range(n):
        high = float(candles[i].high)
        low = float(candles[i].low)
        if i == 0:
            tr = high - low
        else:
            prev_close = float(candles[i - 1].close)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(max(tr, 0.0))
    run = 0.0
    for i in range(n):
        run += trs[i]
        if i >= period:
            run -= trs[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


@dataclass(frozen=True)
class BarrierOutcome:
    label: int            # 1 = win (hit TP first), 0 = loss/timeout-down
    gross_return: float   # realized return BEFORE fees (fraction, e.g. 0.012 = +1.2%)
    bars_held: int
    reason: str           # "tp" | "sl" | "timeout"


def label_entry(
    candles: list[Candle],
    i: int,
    atr: list[float | None],
    *,
    tp_atr_mult: float,
    sl_atr_mult: float,
    max_hold_bars: int,
) -> BarrierOutcome | None:
    """Label a single long entry at bar ``i``. Returns None if not labelable."""
    n = len(candles)
    if i < 0 or i >= n - 1:
        return None
    a = atr[i]
    if a is None or a <= 0:
        return None
    entry = float(candles[i].close)
    if entry <= 0:
        return None
    tp = entry + tp_atr_mult * a
    sl = entry - sl_atr_mult * a

    last_j = min(i + max_hold_bars, n - 1)
    for j in range(i + 1, last_j + 1):
        high = float(candles[j].high)
        low = float(candles[j].low)
        hit_tp = high >= tp
        hit_sl = low <= sl
        if hit_tp and hit_sl:
            # Ambiguous within one bar -> assume the stop triggered first (conservative).
            return BarrierOutcome(0, (sl - entry) / entry, j - i, "sl")
        if hit_tp:
            return BarrierOutcome(1, (tp - entry) / entry, j - i, "tp")
        if hit_sl:
            return BarrierOutcome(0, (sl - entry) / entry, j - i, "sl")

    final = float(candles[last_j].close)
    gross = (final - entry) / entry
    return BarrierOutcome(1 if final > entry else 0, gross, last_j - i, "timeout")
