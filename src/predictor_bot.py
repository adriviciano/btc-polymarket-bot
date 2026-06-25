"""Paper-trading predictor bot for Polymarket BTC UP/DOWN markets.

Goal: validate whether there is REAL edge before risking money. It runs entirely
in paper mode (no orders are sent) and logs the REAL Polymarket entry prices plus
the actual settled outcomes to a CSV, so we can measure true profitability — the
one thing the backtester had to model.

Two signals:
  1. RSI-5m (mean reversion) near the market OPEN — the only rule that reached
     break-even in backtests. Bets ~0.50.
  2. LAG / mispricing detector near the market CLOSE — when BTC spot has already
     moved clearly and the winning side is still buyable below its near-certain
     value (Polymarket's thin book lagging spot). This is the more promising edge.

Everything is published to the live dashboard. Run:
    python -m src.predictor_bot
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import dashboard
from .config import load_settings
from .lookup import fetch_market_from_slug
from .marketdata import get_klines, get_klines_range, last_price
from .strategy import evaluate
from .trading import get_client, get_balance

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Market interval (minutes). Polymarket runs btc-updown-{N}m-<epoch> markets that
# open on aligned N-minute boundaries (epoch divisible by the window) and close one
# window later. Default 5 min; override with MARKET_MINUTES (e.g. 15).
MARKET_MINUTES = int(os.getenv("MARKET_MINUTES", "5"))
WINDOW_SECONDS = MARKET_MINUTES * 60
SLUG_PREFIX = f"btc-updown-{MARKET_MINUTES}m"

# Hang protection. The CLOB SDK calls (get_order_book) have no internal timeout, so a
# dropped connection can freeze a tick forever — the process stays "alive" (systemd
# Restart=always never fires) but stops trading. This explains the multi-hour gaps in
# the paper log. A watchdog thread force-exits the process if a tick hasn't completed
# within WATCHDOG_TIMEOUT seconds; systemd then restarts it cleanly within seconds.
WATCHDOG_TIMEOUT = float(os.getenv("WATCHDOG_TIMEOUT", "120"))


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass
class PaperBet:
    market: str
    signal: str            # "RSI" | "LAG"
    side: str              # "UP" | "DOWN"
    entry_price: float     # REAL Polymarket best-ask paid
    size: float
    placed_at: str
    time_left_s: int
    btc_open: float
    btc_at_entry: float
    window_end: int
    token_id: str
    status: str = "open"   # "open" | "settled"
    outcome: str = ""       # "UP" | "DOWN"
    btc_close: float = 0.0
    won: bool = False
    pnl: float = 0.0
    quoted_ask: float = 0.0  # best ask AT decision time (vs entry_price = realized fill)


class AdaptiveSlippage:
    """Self-tuning entry ceiling that learns real slippage and protects the EV edge.

    The bot decides on the *quoted* best ask but actually pays the *realized* fill
    price (worse, because filling size walks the book — and in live trading also
    because of latency and competition eating the cheap level first). This tracks an
    EMA of ``realized - quoted`` and lowers the effective LAG_MAX_PRICE by that gap,
    so the price we ACTUALLY pay stays under the EV ceiling instead of the price we
    hoped to pay. In paper mode ``realized`` is the book-walk average; in live mode it
    is the exchange fill — identical code path, so the controller keeps adapting once
    real money (and its bigger slippage) is in play. State is persisted so the learned
    slippage survives restarts.
    """

    def __init__(self, base_max: float, floor: float, alpha: float,
                 path: str, enabled: bool):
        self.base_max = base_max          # configured LAG_MAX_PRICE (upper bound)
        self.floor = floor                # never tighten below this
        self.alpha = alpha                # EMA weight on the newest observation
        self.path = path
        self.enabled = enabled
        self.slip_ema = 0.0
        self.n = 0
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.slip_ema = float(d.get("slip_ema", 0.0))
            self.n = int(d.get("n", 0))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"adaptive load error: {e}")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"slip_ema": round(self.slip_ema, 6), "n": self.n,
                           "base_max": self.base_max}, f)
        except Exception as e:
            logger.debug(f"adaptive save error: {e}")

    def effective_max(self) -> float:
        """Current LAG_MAX_PRICE after subtracting learned slippage (clamped)."""
        if not self.enabled:
            return self.base_max
        eff = self.base_max - max(0.0, self.slip_ema)
        return round(max(self.floor, min(self.base_max, eff)), 4)

    def observe(self, quoted: float, realized: float) -> float:
        """Feed one (quoted, realized) pair; returns the new effective ceiling."""
        slip = realized - quoted
        self.slip_ema = slip if self.n == 0 else (
            (1 - self.alpha) * self.slip_ema + self.alpha * slip)
        self.n += 1
        self._save()
        return self.effective_max()


def find_current_btc_market() -> str:
    """Return the slug of the BTC market that is open RIGHT NOW.

    These markets open on aligned WINDOW_SECONDS boundaries (epoch divisible by the
    window) and close exactly one window later, so the live market is computed
    deterministically: start = floor(now / window) * window. This avoids scraping the
    listing page, which often shows only future markets and was the cause of the bot
    locking onto a market a day ahead (TIEMPO RESTANTE in the thousands of minutes).
    """
    now = int(time.time())
    start = (now // WINDOW_SECONDS) * WINDOW_SECONDS
    return f"{SLUG_PREFIX}-{start}"


class PredictorBot:
    def __init__(self, settings, poll: float = 3.0):
        self.settings = settings
        self.poll = poll
        self.size = float(settings.order_size)

        # signal parameters (tunable via env)
        # RSI showed no edge in paper -> default OFF (LAG-only). Set ENABLE_RSI=true to re-enable.
        self.enable_rsi = _env_bool("ENABLE_RSI", False)
        self.enable_lag = _env_bool("ENABLE_LAG", True)
        self.rsi_open_window_s = int(os.getenv("RSI_OPEN_WINDOW_S", "90"))
        self.rsi_period = int(os.getenv("RSI_PERIOD", "14"))
        self.lag_trigger_s = int(os.getenv("LAG_TRIGGER_S", "120"))   # consider lag bet when <Ns left
        # ...but NOT when too little time is left. The paper sample showed bets placed
        # with <40s remaining won only 43% (avg -1.40/trade) — a clear -EV tail (late,
        # thin-edge entries). Floor the entry window so the bot bets in [floor, trigger]s.
        self.lag_min_time_left = int(os.getenv("LAG_MIN_TIME_LEFT", "45"))
        # BTC must have moved >= N bps from the open. Raised 5 -> 8: the paper sample
        # showed <8 bps trades are net -EV (winrate 70% but you pay ~74c for it), while
        # >=8 bps carry a real +EV edge (the book lag only appears after a clear move).
        self.lag_min_bps = float(os.getenv("LAG_MIN_BPS", "8"))
        # Only buy a favorite at/below this ask. The break-even ask equals the win rate,
        # so any ask above your measured LAG win rate is -EV. The >=0.78 bucket was a
        # net loser in the paper sample -> 0.75 cuts the expensive thin-edge bets. The
        # AdaptiveSlippage controller lowers the *effective* ceiling further to absorb
        # realized-vs-quoted slippage (see below).
        self.lag_max_price = float(os.getenv("LAG_MAX_PRICE", "0.75"))
        # Self-tuning ceiling: learns realized-vs-quoted slippage and protects the edge.
        self.lag_adaptive = _env_bool("LAG_ADAPTIVE", True)
        self.adaptive = AdaptiveSlippage(
            base_max=self.lag_max_price,
            floor=float(os.getenv("LAG_MAX_PRICE_FLOOR", "0.55")),
            alpha=float(os.getenv("LAG_SLIP_ALPHA", "0.1")),
            path=os.getenv("PREDICTOR_STATE", "predictor_state.json"),
            enabled=self.lag_adaptive,
        )
        # Edge-scaled paper stake: the cheaper the favorite (= bigger book lag = bigger
        # edge), the more shares we stake. Scales linearly from LAG_ORDER_SIZE at
        # lag_max_price up to LAG_ORDER_SIZE_MAX at LAG_CHEAP_REF (and stays at the max
        # below that). Defaults to a flat ORDER_SIZE when the two sizes are equal.
        self.lag_size = float(os.getenv("LAG_ORDER_SIZE", str(self.size)))
        self.lag_size_max = float(os.getenv("LAG_ORDER_SIZE_MAX", str(self.lag_size)))
        self.lag_cheap_ref = float(os.getenv("LAG_CHEAP_REF", "0.55"))
        self.csv_path = os.getenv("PAPER_CSV", "paper_trades.csv")

        self.client = get_client(settings)
        self.dash = dashboard.get_state()
        try:
            url = dashboard.ensure_started()
            if url:
                logger.info(f"📊 Dashboard en vivo: {url}")
        except Exception as e:
            logger.warning(f"Dashboard no disponible: {e}")

        self.market: Optional[dict] = None
        self.open_bets: Dict[str, PaperBet] = {}   # key: f"{slug}:{signal}"
        self.history: List[PaperBet] = []
        self.totals = {"bets": 0, "wins": 0, "pnl": 0.0, "staked": 0.0}
        self._ticks = 0
        self._last_progress = time.time()   # heartbeat for the hang watchdog

        try:
            self.start_balance = get_balance(settings)
        except Exception:
            self.start_balance = None

        logger.info("=" * 70)
        logger.info("🧪 PREDICTOR BOT — PAPER MODE (no se envían órdenes reales)")
        logger.info(f"   Mercado: {SLUG_PREFIX} (ventana {WINDOW_SECONDS}s = {MARKET_MINUTES}min)")
        active = ", ".join(s for s, on in (("RSI", self.enable_rsi), ("LAG", self.enable_lag)) if on) or "NINGUNA"
        logger.info(f"   Señales activas: {active}")
        if self.enable_rsi:
            logger.info(f"   RSI-open period {self.rsi_period} (ventana {self.rsi_open_window_s}s)")
        if self.enable_lag:
            logger.info(f"   LAG entrada {self.lag_min_time_left}–{self.lag_trigger_s}s, "
                        f"move≥{self.lag_min_bps}bps, ask≤{self.lag_max_price}")
            if self.lag_adaptive:
                logger.info(f"   LAG adaptativo ON: techo efectivo {self.adaptive.effective_max():.3f} "
                            f"(slippage aprendido {self.adaptive.slip_ema:+.3f} sobre {self.adaptive.n} fills)")
        if self.lag_size_max > self.lag_size:
            logger.info(f"   Tamaño paper LAG: {self.lag_size:g}→{self.lag_size_max:g} shares "
                        f"(escala por edge, máx en ask≤{self.lag_cheap_ref}) | CSV: {self.csv_path}")
        else:
            logger.info(f"   Tamaño paper: {self.size:g} shares | CSV: {self.csv_path}")
        logger.info("=" * 70)

    # --- helpers ----------------------------------------------------------
    def _ask_levels(self, token_id: str) -> List[Tuple[float, float]]:
        """Ask book as (price, size) levels sorted by price ascending ([] on error)."""
        try:
            book = self.client.get_order_book(token_id=token_id)
            asks = book.get("asks") if isinstance(book, dict) else getattr(book, "asks", None)
            levels = []
            for lvl in (asks or []):
                p = float(lvl["price"]) if isinstance(lvl, dict) else float(lvl.price)
                s = float(lvl["size"]) if isinstance(lvl, dict) else float(lvl.size)
                levels.append((p, s))
            levels.sort(key=lambda x: x[0])
            return levels
        except Exception as e:
            logger.debug(f"ask_levels error: {e}")
            return []

    def _best_ask(self, token_id: str) -> Tuple[Optional[float], float]:
        levels = self._ask_levels(token_id)
        return levels[0] if levels else (None, 0.0)

    def _fill_for_size(self, token_id: str, want: float, max_price: float
                       ) -> Tuple[Optional[float], float]:
        """Simulate a marketable buy of ``want`` shares with a ``max_price`` limit.

        Walks the asks from the cheapest level, taking only levels priced at or below
        ``max_price``, and returns the volume-weighted average fill price plus the
        shares actually filled (which can be < want when the book is thin). This models
        a real limit order far better than assuming the whole size fills at the best ask
        — slippage that matters once LAG sizes are scaled up. By construction the avg
        price never exceeds ``max_price``.
        """
        remaining, cost, filled = want, 0.0, 0.0
        for price, size in self._ask_levels(token_id):
            if price > max_price or remaining <= 0:
                break
            take = min(remaining, size)
            cost += take * price
            filled += take
            remaining -= take
        if filled <= 0:
            return None, 0.0
        return cost / filled, round(filled, 2)

    def _btc_price_at(self, epoch_s: int) -> Optional[float]:
        """BTC open price of the 1m candle covering epoch_s (Binance)."""
        try:
            ms = (epoch_s // 60) * 60 * 1000
            candles = get_klines_range("BTCUSDT", "1m", ms, ms + 120_000)
            return candles[0].open if candles else None
        except Exception as e:
            logger.debug(f"btc_price_at error: {e}")
            return None

    # --- market lifecycle -------------------------------------------------
    def load_market(self):
        slug = find_current_btc_market()
        info = fetch_market_from_slug(slug)
        m = re.search(rf"{SLUG_PREFIX}-(\d+)", slug)
        start = int(m.group(1))
        btc_open = self._btc_price_at(start)
        self.market = {
            "slug": slug,
            "yes": info["yes_token_id"],
            "no": info["no_token_id"],
            "start": start,
            "end": start + WINDOW_SECONDS,
            "btc_open": btc_open,
        }
        logger.info(f"🎯 Mercado activo: {slug} (open ${btc_open:.2f})" if btc_open
                    else f"🎯 Mercado activo: {slug} (open ref no disponible)")
        self.dash.update(mode="PAPER", market_slug=slug,
                         up_token=info["yes_token_id"], down_token=info["no_token_id"],
                         threshold=self.lag_max_price,
                         balance=round(self.start_balance, 2) if self.start_balance is not None else None,
                         balance_label="pUSD real (no se arriesga)")

    def time_left(self) -> int:
        return int(self.market["end"] - time.time()) if self.market else 0

    # --- signals ----------------------------------------------------------
    def _lag_size_for(self, ask: float) -> float:
        """Edge-scaled paper stake for a LAG bet.

        Cheaper favorite == bigger book lag == bigger edge, so we stake more. Scales
        linearly from ``lag_size`` at ``lag_max_price`` up to ``lag_size_max`` at
        ``lag_cheap_ref`` (clamped both ends). When the two sizes are equal it returns
        the flat size, so behaviour is unchanged unless LAG_ORDER_SIZE_MAX is set.
        """
        if self.lag_size_max <= self.lag_size or self.lag_max_price <= self.lag_cheap_ref:
            return self.lag_size
        frac = (self.lag_max_price - ask) / (self.lag_max_price - self.lag_cheap_ref)
        frac = max(0.0, min(1.0, frac))
        return round(self.lag_size + frac * (self.lag_size_max - self.lag_size), 2)

    def _place_paper_bet(self, signal: str, side: str, token_id: str,
                         entry_price: float, btc_now: float, size: Optional[float] = None,
                         quoted_ask: Optional[float] = None):
        key = f"{self.market['slug']}:{signal}"
        if key in self.open_bets:
            return
        size = self.size if size is None else size
        bet = PaperBet(
            market=self.market["slug"], signal=signal, side=side,
            entry_price=round(entry_price, 4), size=size,
            placed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            time_left_s=self.time_left(), btc_open=self.market["btc_open"] or 0.0,
            btc_at_entry=btc_now, window_end=self.market["end"], token_id=token_id,
            quoted_ask=round(quoted_ask if quoted_ask is not None else entry_price, 4),
        )
        self.open_bets[key] = bet
        self.totals["bets"] += 1
        self.totals["staked"] += size * entry_price
        stake = size * entry_price
        logger.info(f"📝 PAPER {signal} bet {side} @ {entry_price:.3f} "
                    f"(stake ${stake:.2f}, {size:g} sh, {self.time_left()}s left, BTC ${btc_now:.0f})")

    def evaluate_signals(self):
        if not self.market:
            return
        now = time.time()
        elapsed = now - self.market["start"]
        left = self.time_left()
        btc_now = None
        try:
            btc_now = last_price("BTCUSDT")
        except Exception:
            pass

        # --- Signal 1: RSI near the open ---
        rsi_key = f"{self.market['slug']}:RSI"
        if self.enable_rsi and 0 <= elapsed <= self.rsi_open_window_s and rsi_key not in self.open_bets:
            try:
                candles = get_klines("BTCUSDT", "1m", limit=max(self.rsi_period + 6, 30))
                sig = evaluate("rsi", candles, {"period": self.rsi_period})
                if sig.side:
                    token = self.market["yes"] if sig.side == "UP" else self.market["no"]
                    ask, _ = self._best_ask(token)
                    if ask is not None:
                        logger.info(f"📈 RSI -> {sig.side} ({sig.reason})")
                        self._place_paper_bet("RSI", sig.side, token, ask, btc_now or 0.0)
            except Exception as e:
                logger.debug(f"RSI eval error: {e}")

        # --- Signal 2: LAG / mispricing near the close ---
        lag_key = f"{self.market['slug']}:LAG"
        if (self.enable_lag and self.lag_min_time_left <= left <= self.lag_trigger_s
                and lag_key not in self.open_bets
                and self.market["btc_open"] and btc_now):
            move_bps = (btc_now - self.market["btc_open"]) / self.market["btc_open"] * 10_000
            if abs(move_bps) >= self.lag_min_bps:
                leader = "UP" if move_bps > 0 else "DOWN"
                token = self.market["yes"] if leader == "UP" else self.market["no"]
                eff_max = self.adaptive.effective_max()
                ask, ask_sz = self._best_ask(token)
                if ask is not None and ask <= eff_max and ask_sz >= 1:
                    want = self._lag_size_for(ask)
                    avg_price, filled = self._fill_for_size(token, want, eff_max)
                    if avg_price is not None and filled >= 1:
                        new_eff = self.adaptive.observe(ask, avg_price)
                        logger.info(f"⚡ LAG -> {leader} winning by {move_bps:+.0f}bps, "
                                    f"best {ask:.3f}, fill {filled:g}/{want:g} sh @ avg {avg_price:.3f} "
                                    f"(ceil {eff_max:.3f}, slip {self.adaptive.slip_ema:+.3f} -> ceil {new_eff:.3f})")
                        self._place_paper_bet("LAG", leader, token, avg_price, btc_now,
                                              size=filled, quoted_ask=ask)

    # --- settlement -------------------------------------------------------
    def settle_due(self):
        now = time.time()
        for key in list(self.open_bets.keys()):
            bet = self.open_bets[key]
            if now < bet.window_end + 5:
                continue
            btc_close = self._btc_price_at(bet.window_end)
            if btc_close is None:
                continue
            outcome = "UP" if btc_close > bet.btc_open else "DOWN"
            won = (bet.side == outcome)
            pnl = bet.size * ((1.0 - bet.entry_price) if won else -bet.entry_price)
            bet.status, bet.outcome, bet.btc_close = "settled", outcome, btc_close
            bet.won, bet.pnl = won, round(pnl, 2)

            self.totals["wins"] += 1 if won else 0
            self.totals["pnl"] += pnl
            self.history.append(bet)
            del self.open_bets[key]
            self._write_csv(bet)
            tag = "✅ WIN" if won else "❌ LOSS"
            logger.info(f"{tag} {bet.signal} {bet.side} @ {bet.entry_price:.3f} -> {outcome} "
                        f"(BTC {bet.btc_open:.0f}->{btc_close:.0f}) pnl ${pnl:+.2f} | "
                        f"total ${self.totals['pnl']:+.2f}")

    def _write_csv(self, bet: PaperBet):
        try:
            new = not os.path.exists(self.csv_path)
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(asdict(bet).keys()))
                if new:
                    w.writeheader()
                w.writerow(asdict(bet))
        except Exception as e:
            logger.warning(f"No se pudo escribir CSV: {e}")

    # --- dashboard --------------------------------------------------------
    def publish(self, up_ask=None, down_ask=None):
        try:
            n = self.totals["bets"]
            wins = self.totals["wins"]
            settled = len(self.history)
            winrate = (wins / settled * 100) if settled else 0.0
            self.dash.update(
                time_remaining=self._fmt_left(), scans=self._ticks,
                opportunities=n, trades=settled,
                total_invested=round(self.totals["staked"], 2),
                expected_profit=round(self.totals["pnl"], 2),
                price_up=round(up_ask, 4) if up_ask is not None else None,
                price_down=round(down_ask, 4) if down_ask is not None else None,
                total_cost=round((up_ask + down_ask), 4) if (up_ask is not None and down_ask is not None) else None,
                positions={"up": sum(1 for b in self.open_bets.values() if b.side == "UP"),
                           "down": sum(1 for b in self.open_bets.values() if b.side == "DOWN")},
            )
            if settled and self._ticks % 20 == 0:
                logger.info(f"📊 Paper: {settled} liquidadas, win rate {winrate:.0f}%, "
                            f"PnL ${self.totals['pnl']:+.2f}, {n - settled} abiertas")
        except Exception:
            pass

    def _fmt_left(self) -> str:
        s = self.time_left()
        if s <= 0:
            return "CLOSED"
        return f"{s // 60}m {s % 60}s"

    # --- main loop --------------------------------------------------------
    def tick(self):
        self._ticks += 1
        if self.market is None or time.time() > self.market["end"] + 5:
            self.settle_due()  # settle anything from the previous window first
            if not self.open_bets:
                try:
                    self.load_market()
                except Exception as e:
                    logger.warning(f"No se pudo cargar mercado: {e}")
                    return
        up_ask, _ = self._best_ask(self.market["yes"]) if self.market else (None, 0)
        down_ask, _ = self._best_ask(self.market["no"]) if self.market else (None, 0)
        self.evaluate_signals()
        self.settle_due()
        self.publish(up_ask, down_ask)
        self._last_progress = time.time()  # tick completed -> feed the watchdog

    def _start_watchdog(self):
        """Force-exit if a tick stalls past WATCHDOG_TIMEOUT so systemd can restart us.

        The CLOB SDK has no per-call timeout; a frozen socket would otherwise hang the
        loop silently for hours. Network I/O releases the GIL, so this daemon thread
        keeps running even while the main thread is blocked, and os._exit guarantees the
        process actually dies (sys.exit would just raise in the stuck thread).
        """
        if WATCHDOG_TIMEOUT <= 0:
            return

        def _watch():
            while True:
                time.sleep(min(WATCHDOG_TIMEOUT / 4, 15))
                stalled = time.time() - self._last_progress
                if stalled > WATCHDOG_TIMEOUT:
                    logger.error(f"⏱️ WATCHDOG: tick congelado {stalled:.0f}s "
                                 f"(>{WATCHDOG_TIMEOUT:.0f}s). Forzando salida para reinicio.")
                    sys.stderr.flush()
                    os._exit(1)

        threading.Thread(target=_watch, name="watchdog", daemon=True).start()
        logger.info(f"🐕 Watchdog activo: salida forzada si un tick supera {WATCHDOG_TIMEOUT:.0f}s")

    async def run(self):
        logger.info("🚀 Predictor bot iniciado (PAPER). Ctrl+C para parar.")
        self._last_progress = time.time()
        self._start_watchdog()
        try:
            while True:
                self.tick()
                await asyncio.sleep(self.poll)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.settle_due()
            settled = len(self.history)
            wr = (self.totals["wins"] / settled * 100) if settled else 0.0
            logger.info("=" * 70)
            logger.info(f"🛑 Parado. Paper bets liquidadas: {settled} | win rate {wr:.0f}% | "
                        f"PnL ${self.totals['pnl']:+.2f} | abiertas {len(self.open_bets)}")
            logger.info(f"   Datos guardados en {self.csv_path}")
            logger.info("=" * 70)


async def main():
    ap = argparse.ArgumentParser(description="Polymarket BTC paper-trading predictor")
    ap.add_argument("--poll", type=float, default=3.0, help="segundos entre ticks")
    args = ap.parse_args()
    settings = load_settings()
    if not settings.private_key:
        logger.error("❌ POLYMARKET_PRIVATE_KEY no configurado en .env")
        return
    bot = PredictorBot(settings, poll=args.poll)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
