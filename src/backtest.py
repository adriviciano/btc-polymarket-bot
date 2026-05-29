"""Backtester for the rules-based BTC UP/DOWN prediction strategies.

It reconstructs the Polymarket-style game from real BTC candles: at each decision
point we look at the recent lookback window, ask a strategy which way the next
`horizon` minutes will go, then check the actual outcome. We report directional
accuracy vs. the break-even price and the simulated P&L of betting fixed size on
each signal.

IMPORTANT / honesty note:
  Polymarket UP/DOWN shares are paid at the market's *implied probability*. We do
  not have that historical price series, so the entry price is MODELLED: near a
  fresh window the two sides trade ~0.50, so the default entry is 0.50 plus a
  configurable `--cost` (spread + fees). To make money you need accuracy > entry
  price. If a strategy's accuracy isn't clearly above entry+cost, it has no edge.

Usage:
  python -m src.backtest                       # all strategies, defaults
  python -m src.backtest --horizon 5 --days 90 --cost 0.03
"""

import argparse
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List

from .marketdata import INTERVAL_MS, get_history_days, Candle
from .strategy import STRATEGIES, evaluate


@dataclass
class Result:
    strategy: str
    bets: int
    wins: int
    windows: int
    accuracy: float
    coverage: float
    breakeven: float
    edge: float
    pnl: float
    roi: float
    avg_pnl_per_bet: float
    profit_factor: float
    max_drawdown: float


def simulate(candles: List[Candle], strategy: str, *, horizon_bars: int, lookback: int,
             step_bars: int, entry_price: float, size: float, params: dict = None) -> Result:
    bets = wins = windows = 0
    pnl = 0.0
    gross_win = gross_loss = 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    per_bet: List[float] = []

    i = lookback
    last_decision = len(candles) - horizon_bars - 1
    while i <= last_decision:
        window = candles[i - lookback + 1: i + 1]
        entry_close = candles[i].close
        outcome_close = candles[i + horizon_bars].close
        if outcome_close != entry_close:
            windows += 1
            outcome = "UP" if outcome_close > entry_close else "DOWN"
            sig = evaluate(strategy, window, params)
            if sig.side is not None:
                bets += 1
                win = sig.side == outcome
                trade_pnl = size * ((1.0 - entry_price) if win else -entry_price)
                pnl += trade_pnl
                per_bet.append(trade_pnl)
                if win:
                    wins += 1
                    gross_win += trade_pnl
                else:
                    gross_loss += -trade_pnl
                equity += trade_pnl
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
        i += step_bars

    accuracy = wins / bets if bets else 0.0
    staked = bets * size * entry_price
    roi = pnl / staked if staked else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    return Result(
        strategy=strategy, bets=bets, wins=wins, windows=windows,
        accuracy=accuracy, coverage=(bets / windows if windows else 0.0),
        breakeven=entry_price, edge=accuracy - entry_price, pnl=pnl, roi=roi,
        avg_pnl_per_bet=(pnl / bets if bets else 0.0),
        profit_factor=profit_factor, max_drawdown=max_dd,
    )


def base_rate_up(candles: List[Candle], horizon_bars: int, lookback: int, step_bars: int) -> float:
    up = tot = 0
    i = lookback
    last = len(candles) - horizon_bars - 1
    while i <= last:
        a, b = candles[i].close, candles[i + horizon_bars].close
        if b != a:
            tot += 1
            up += 1 if b > a else 0
        i += step_bars
    return up / tot if tot else 0.0


def main():
    ap = argparse.ArgumentParser(description="Backtest BTC UP/DOWN rules strategies")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1m", choices=list(INTERVAL_MS))
    ap.add_argument("--horizon", type=int, default=15, help="market window in minutes")
    ap.add_argument("--lookback", type=int, default=60, help="candles used for the decision")
    ap.add_argument("--step", type=int, default=None, help="minutes between decisions (default = horizon)")
    ap.add_argument("--days", type=float, default=60)
    ap.add_argument("--size", type=float, default=5, help="shares per bet")
    ap.add_argument("--entry", type=float, default=0.50, help="modelled entry price per share")
    ap.add_argument("--cost", type=float, default=0.0, help="extra cost added to entry (spread+fees)")
    ap.add_argument("--strategy", default="all")
    args = ap.parse_args()

    interval_min = INTERVAL_MS[args.interval] // 60_000
    if args.horizon % interval_min != 0:
        raise SystemExit(f"--horizon ({args.horizon}) must be a multiple of interval ({interval_min}m)")
    step_min = args.step if args.step is not None else args.horizon
    if step_min % interval_min != 0:
        raise SystemExit(f"--step ({step_min}) must be a multiple of interval ({interval_min}m)")
    horizon_bars = args.horizon // interval_min
    step_bars = max(1, step_min // interval_min)
    entry_price = args.entry + args.cost

    print("=" * 78)
    print("BTC UP/DOWN BACKTEST  (rules-based)")
    print("=" * 78)
    print(f"Symbol {args.symbol} | interval {args.interval} | horizon {args.horizon}m | "
          f"lookback {args.lookback} | step {step_min}m | history {args.days}d")
    print(f"Modelled entry price = {entry_price:.3f}  (entry {args.entry} + cost {args.cost})  "
          f"=> break-even accuracy = {entry_price*100:.1f}%")
    print("Downloading candles from Binance...")
    candles = get_history_days(args.symbol, args.interval, args.days)
    if len(candles) < args.lookback + horizon_bars + 5:
        raise SystemExit("Not enough candles returned to backtest; try fewer --days restrictions or check connectivity.")
    d0 = datetime.fromtimestamp(candles[0].ts / 1000, timezone.utc)
    d1 = datetime.fromtimestamp(candles[-1].ts / 1000, timezone.utc)
    print(f"Got {len(candles)} candles ({d0:%Y-%m-%d} -> {d1:%Y-%m-%d})")

    p_up = base_rate_up(candles, horizon_bars, args.lookback, step_bars)
    print(f"Base rate: P(UP)={p_up*100:.1f}%  P(DOWN)={(1-p_up)*100:.1f}%  "
          f"(naive 'always majority' accuracy = {max(p_up, 1-p_up)*100:.1f}%)")
    print("-" * 78)

    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    results = [simulate(candles, n, horizon_bars=horizon_bars, lookback=args.lookback,
                        step_bars=step_bars, entry_price=entry_price, size=args.size) for n in names]

    hdr = f"{'strategy':10} {'bets':>6} {'acc%':>7} {'cover%':>7} {'edge%':>7} {'PnL$':>10} {'ROI%':>8} {'PF':>6} {'maxDD$':>9}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(results, key=lambda x: x.pnl, reverse=True):
        pf = "inf" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
        print(f"{r.strategy:10} {r.bets:>6} {r.accuracy*100:>7.1f} {r.coverage*100:>7.1f} "
              f"{r.edge*100:>+7.1f} {r.pnl:>10.2f} {r.roi*100:>+8.1f} {pf:>6} {r.max_drawdown:>9.2f}")

    print("-" * 78)
    best = max(results, key=lambda x: x.pnl)
    if best.edge > 0 and best.pnl > 0:
        print(f"Best: '{best.strategy}' — accuracy {best.accuracy*100:.1f}% vs break-even "
              f"{entry_price*100:.1f}% (edge {best.edge*100:+.1f} pts). Worth paper-trading & stress-testing.")
    else:
        print("No strategy beats the break-even price under this cost model. "
              "That means NO demonstrated edge — do not risk real money yet.")
    print("Note: entry price is modelled (no Polymarket price history). Try --cost to be "
          "realistic about spread/fees; edge must survive it.")


if __name__ == "__main__":
    main()
