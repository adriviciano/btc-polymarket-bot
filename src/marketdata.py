"""Crypto OHLCV market data (Binance public API, no key required).

Used by the rules-based prediction engine and the backtester. Provides recent
candles and paginated historical ranges. Binance has clean klines and generous
public limits; data-api.binance.vision is used as a fallback host.
"""

import time
from dataclasses import dataclass
from typing import List, Optional

import httpx

_BASES = ["https://data-api.binance.vision", "https://api.binance.com"]

# Candle interval -> milliseconds
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


@dataclass
class Candle:
    ts: int       # open time (ms)
    open: float
    high: float
    low: float
    close: float
    volume: float


def _row(x) -> Candle:
    return Candle(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))


def _get(path: str, params: dict):
    last = None
    for base in _BASES:
        try:
            r = httpx.get(base + path, params=params, timeout=20,
                          headers={"User-Agent": "btc-polymarket-bot"})
            if r.status_code == 200:
                return r.json()
            last = f"{r.status_code} {r.text[:120]}"
        except Exception as e:  # try next host
            last = e
    raise RuntimeError(f"Binance fetch failed for {path}: {last}")


def get_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 500) -> List[Candle]:
    """Most recent `limit` candles (max 1000)."""
    data = _get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)})
    return [_row(x) for x in data]


def get_klines_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                     pause: float = 0.12) -> List[Candle]:
    """Paginated historical candles in [start_ms, end_ms)."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    step = INTERVAL_MS[interval]
    out: List[Candle] = []
    cur = start_ms
    while cur < end_ms:
        data = _get("/api/v3/klines", {
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms, "limit": 1000,
        })
        if not data:
            break
        out.extend(_row(x) for x in data)
        last_open = data[-1][0]
        nxt = last_open + step
        if nxt <= cur:
            break
        cur = nxt
        if len(data) < 1000:
            break
        time.sleep(pause)  # be gentle with the public API

    # de-dup by open time, keep order
    seen = set()
    uniq: List[Candle] = []
    for c in out:
        if c.ts in seen:
            continue
        seen.add(c.ts)
        uniq.append(c)
    return uniq


def get_history_days(symbol: str = "BTCUSDT", interval: str = "1m",
                     days: float = 30, end_ms: Optional[int] = None) -> List[Candle]:
    """Historical candles for the last `days` days."""
    end = end_ms if end_ms is not None else int(time.time() * 1000)
    start = end - int(days * 86_400_000)
    return get_klines_range(symbol, interval, start, end)


def last_price(symbol: str = "BTCUSDT") -> float:
    data = _get("/api/v3/ticker/price", {"symbol": symbol})
    return float(data["price"])
