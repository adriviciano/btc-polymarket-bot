"""Rules-based directional prediction for short-horizon BTC moves.

Each strategy consumes a lookback window of candles (ending at the decision
moment) and returns a Signal: which side to bet ("UP"/"DOWN"/None) plus a modest
confidence in [0.5, ~0.7]. These are classic technical rules — momentum, moving
average crossover, RSI mean-reversion and breakout — kept simple, transparent and
easy to backtest. They are NOT guaranteed to have edge; the backtester is what
tells us whether any of them beats the break-even price.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .marketdata import Candle


@dataclass
class Signal:
    side: Optional[str]      # "UP", "DOWN" or None (no bet)
    confidence: float        # ~0.5 (coin flip) .. ~0.7
    reason: str = ""


def _closes(candles: List[Candle]) -> List[float]:
    return [c.close for c in candles]


def _sma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _conf(magnitude: float, scale: float, cap: float = 0.68) -> float:
    """Map a positive signal magnitude to a confidence in [0.5, cap]."""
    if scale <= 0:
        return 0.5
    return min(cap, 0.5 + 0.18 * min(1.0, magnitude / scale))


def momentum(candles: List[Candle], params: dict = None) -> Signal:
    """Continuation: if the recent return exceeds a threshold, bet that direction."""
    p = params or {}
    lookback = int(p.get("lookback", 30))
    threshold = float(p.get("threshold", 0.0015))  # 0.15% over the window
    closes = _closes(candles)
    if len(closes) < lookback + 1:
        return Signal(None, 0.5, "insufficient data")
    ret = (closes[-1] - closes[-lookback - 1]) / closes[-lookback - 1]
    if ret > threshold:
        return Signal("UP", _conf(ret, threshold * 4), f"mom +{ret*100:.2f}%")
    if ret < -threshold:
        return Signal("DOWN", _conf(-ret, threshold * 4), f"mom {ret*100:.2f}%")
    return Signal(None, 0.5, f"flat {ret*100:.2f}%")


def ma_crossover(candles: List[Candle], params: dict = None) -> Signal:
    """Trend: fast SMA above slow SMA -> UP, below -> DOWN."""
    p = params or {}
    fast_n = int(p.get("fast", 9))
    slow_n = int(p.get("slow", 30))
    closes = _closes(candles)
    fast = _sma(closes, fast_n)
    slow = _sma(closes, slow_n)
    if fast is None or slow is None or slow == 0:
        return Signal(None, 0.5, "insufficient data")
    gap = (fast - slow) / slow
    if gap > 0:
        return Signal("UP", _conf(gap, 0.003), f"fast>slow {gap*100:.2f}%")
    if gap < 0:
        return Signal("DOWN", _conf(-gap, 0.003), f"fast<slow {gap*100:.2f}%")
    return Signal(None, 0.5, "ma equal")


def rsi_reversion(candles: List[Candle], params: dict = None) -> Signal:
    """Mean reversion: oversold -> bet UP (bounce), overbought -> bet DOWN."""
    p = params or {}
    period = int(p.get("period", 14))
    oversold = float(p.get("oversold", 30))
    overbought = float(p.get("overbought", 70))
    rsi = _rsi(_closes(candles), period)
    if rsi is None:
        return Signal(None, 0.5, "insufficient data")
    if rsi <= oversold:
        return Signal("UP", _conf(oversold - rsi, 25), f"RSI {rsi:.0f} oversold")
    if rsi >= overbought:
        return Signal("DOWN", _conf(rsi - overbought, 25), f"RSI {rsi:.0f} overbought")
    return Signal(None, 0.5, f"RSI {rsi:.0f} neutral")


def breakout(candles: List[Candle], params: dict = None) -> Signal:
    """Breakout: close above the recent high range -> UP, below the low -> DOWN."""
    p = params or {}
    window = int(p.get("window", 20))
    if len(candles) < window + 1:
        return Signal(None, 0.5, "insufficient data")
    recent = candles[-window - 1:-1]
    hi = max(c.high for c in recent)
    lo = min(c.low for c in recent)
    last = candles[-1].close
    rng = (hi - lo) or 1.0
    if last > hi:
        return Signal("UP", _conf(last - hi, rng * 0.5), "breakout high")
    if last < lo:
        return Signal("DOWN", _conf(lo - last, rng * 0.5), "breakdown low")
    return Signal(None, 0.5, "inside range")


STRATEGIES: Dict[str, Callable[[List[Candle], dict], Signal]] = {
    "momentum": momentum,
    "ma": ma_crossover,
    "rsi": rsi_reversion,
    "breakout": breakout,
}


def evaluate(name: str, candles: List[Candle], params: dict = None) -> Signal:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}'. Options: {list(STRATEGIES)}")
    return STRATEGIES[name](candles, params or {})
