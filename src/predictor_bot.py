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
import logging
import os
import re
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
        self.rsi_open_window_s = int(os.getenv("RSI_OPEN_WINDOW_S", "90"))
        self.rsi_period = int(os.getenv("RSI_PERIOD", "14"))
        self.lag_trigger_s = int(os.getenv("LAG_TRIGGER_S", "120"))   # consider lag bet when <120s left
        self.lag_min_bps = float(os.getenv("LAG_MIN_BPS", "5"))        # BTC moved > 5 bps from open
        self.lag_max_price = float(os.getenv("LAG_MAX_PRICE", "0.90")) # only buy a favorite below this
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

        try:
            self.start_balance = get_balance(settings)
        except Exception:
            self.start_balance = None

        logger.info("=" * 70)
        logger.info("🧪 PREDICTOR BOT — PAPER MODE (no se envían órdenes reales)")
        logger.info(f"   Mercado: {SLUG_PREFIX} (ventana {WINDOW_SECONDS}s = {MARKET_MINUTES}min)")
        logger.info(f"   Señales: RSI-open (period {self.rsi_period}) + LAG near-expiry "
                    f"(<{self.lag_trigger_s}s, ask≤{self.lag_max_price})")
        logger.info(f"   Tamaño paper: {self.size} shares | CSV: {self.csv_path}")
        logger.info("=" * 70)

    # --- helpers ----------------------------------------------------------
    def _best_ask(self, token_id: str) -> Tuple[Optional[float], float]:
        try:
            book = self.client.get_order_book(token_id=token_id)
            asks = book.get("asks") if isinstance(book, dict) else getattr(book, "asks", None)
            best_p, best_s = None, 0.0
            for lvl in (asks or []):
                p = float(lvl["price"]) if isinstance(lvl, dict) else float(lvl.price)
                s = float(lvl["size"]) if isinstance(lvl, dict) else float(lvl.size)
                if best_p is None or p < best_p:
                    best_p, best_s = p, s
            return best_p, best_s
        except Exception as e:
            logger.debug(f"best_ask error: {e}")
            return None, 0.0

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
    def _place_paper_bet(self, signal: str, side: str, token_id: str,
                         entry_price: float, btc_now: float):
        key = f"{self.market['slug']}:{signal}"
        if key in self.open_bets:
            return
        bet = PaperBet(
            market=self.market["slug"], signal=signal, side=side,
            entry_price=round(entry_price, 4), size=self.size,
            placed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            time_left_s=self.time_left(), btc_open=self.market["btc_open"] or 0.0,
            btc_at_entry=btc_now, window_end=self.market["end"], token_id=token_id,
        )
        self.open_bets[key] = bet
        self.totals["bets"] += 1
        self.totals["staked"] += self.size * entry_price
        stake = self.size * entry_price
        logger.info(f"📝 PAPER {signal} bet {side} @ {entry_price:.3f} "
                    f"(stake ${stake:.2f}, {self.time_left()}s left, BTC ${btc_now:.0f})")

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
        if 0 <= elapsed <= self.rsi_open_window_s and rsi_key not in self.open_bets:
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
        if (0 < left <= self.lag_trigger_s and lag_key not in self.open_bets
                and self.market["btc_open"] and btc_now):
            move_bps = (btc_now - self.market["btc_open"]) / self.market["btc_open"] * 10_000
            if abs(move_bps) >= self.lag_min_bps:
                leader = "UP" if move_bps > 0 else "DOWN"
                token = self.market["yes"] if leader == "UP" else self.market["no"]
                ask, ask_sz = self._best_ask(token)
                if ask is not None and ask <= self.lag_max_price and ask_sz >= 1:
                    logger.info(f"⚡ LAG -> {leader} winning by {move_bps:+.0f}bps, "
                                f"ask {ask:.3f} ≤ {self.lag_max_price} (cheap favorite)")
                    self._place_paper_bet("LAG", leader, token, ask, btc_now)

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

    async def run(self):
        logger.info("🚀 Predictor bot iniciado (PAPER). Ctrl+C para parar.")
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
