# BTC Polymarket Bot

A small toolkit for the **Bitcoin "Up or Down"** markets on Polymarket. It bundles
two independent bots, a research backtester, and a shared live web dashboard:

| Component | Command | What it does | Risks money? |
|-----------|---------|--------------|:---:|
| 🧪 **Predictor bot** | `python -m src.predictor_bot` | **Paper-trades** BTC UP/DOWN markets to measure whether two signals (RSI + lag) actually have edge. Logs real entry prices and settled outcomes to a CSV. | ❌ Never (paper) |
| 💱 **Arbitrage bot** | `python -m src.simple_arb_bot` | Buys **both** sides (UP + DOWN) when their combined price is `< $1`, for a guaranteed spread. Has a `DRY_RUN` simulation mode. | ⚠️ Only if `DRY_RUN=false` |
| 📈 **Backtester** | `python -m src.backtest` | Replays the UP/DOWN game over real historical BTC candles and reports each strategy's accuracy and P&L vs. a break-even price. | ❌ Never |
| 📊 **Dashboard** | (auto-starts) | Live web page showing balance, prices, counters and the bot's activity feed. Started automatically by either bot. | ❌ Never |

> **Honest status:** backtests over ~60 days show **no robust edge** for the prediction
> rules once realistic spread/fees are included — trend rules lose, RSI only reaches
> break-even. The predictor bot runs in **paper mode** precisely to validate (or reject)
> edge with real fills before any money is risked. The only component with a sound
> mathematical basis is the **arbitrage** bot.

---

## 🚀 Quick start

```bash
# 1. Clone + install
git clone https://github.com/Jonmaa/btc-polymarket-bot.git
cd btc-polymarket-bot
python -m venv .venv
.\.venv\Scripts\activate          # Windows
# source .venv/bin/activate       # Linux/Mac
pip install -r requirements.txt

# 2. Configure
cp .env.example .env              # then edit it (see Configuration below)
python -m src.generate_api_key    # derive API creds from your private key

# 3. Run the safe one first (paper, never sends orders)
python -m src.predictor_bot
```

The predictor bot opens the dashboard automatically (the URL is printed in the logs,
e.g. `📊 Dashboard en vivo: http://127.0.0.1:8765`).

---

## 🧪 Predictor bot (paper trading)

`python -m src.predictor_bot`

This bot **never sends real orders.** It watches the live Polymarket BTC markets,
applies two signals, and records what *would* have happened — using the **real**
order-book entry prices and the **real** settled outcome — to `paper_trades.csv`.
That CSV is the whole point: it tells you whether either signal has genuine edge
before you risk a cent.

### Which market it trades

By default it follows the **5-minute** markets (`btc-updown-5m-<epoch>`). These open on
aligned 5-minute boundaries and close 5 minutes later, so the bot computes the
currently-live market deterministically instead of scraping a listing page. Set
`MARKET_MINUTES=15` to follow the 15-minute markets instead.

### What it "predicts" — the two signals

**1. RSI — mean reversion, near the OPEN** ([predictor_bot.py](src/predictor_bot.py))
Computes `RSI(14)` on the last 1-minute BTC candles (Binance) during the first
`RSI_OPEN_WINDOW_S` seconds after the market opens:
- `RSI ≤ 30` (oversold) → bet **UP** (expects a bounce).
- `RSI ≥ 70` (overbought) → bet **DOWN** (expects a pullback).
- Otherwise → no bet.
A contrarian bet. In backtests this only reached break-even.

**2. LAG — mispricing, near the CLOSE** ([predictor_bot.py](src/predictor_bot.py))
This is not really a prediction — it exploits Polymarket's order book reacting *slower*
than BTC's spot price. In the last `LAG_TRIGGER_S` seconds before close:
- If BTC has already moved ≥ `LAG_MIN_BPS` from the market's open price, the winning
  side is nearly decided.
- If that near-certain winner is still buyable at `ask ≤ LAG_MAX_PRICE` (default `0.80`)
  with at least 1 share of depth, it buys it, betting it settles at $1.
Closer to arbitrage than to forecasting. The break-even ask equals the win rate, so the
default cap stays below the measured LAG win rate (an ask above it is −EV). The paper
stake is **edge-scaled** (cheaper favorite ⇒ more shares, see `LAG_ORDER_SIZE_MAX`) and the
entry price/size come from **walking the ask book** under a `LAG_MAX_PRICE` limit — so a
thin book yields a worse average price and a partial fill, like a real limit order.

### Reading the dashboard

| Tile | Meaning |
|------|---------|
| **pUSD real (no se arriesga)** | Your real wallet balance. Shown for reference only — **paper mode never touches it.** |
| **Tiempo restante** | Time left in the current market window. |
| **Escaneos** | Number of poll loops completed (the bot is alive). |
| **Oportunidades** | Paper bets placed. |
| **Trades** | Paper bets that have settled. |
| **Invertido** | Paper stake (shares × entry price). |
| **Beneficio esperado** | Cumulative paper P&L. |
| **Precios actuales** | Best asks for UP/DOWN, their total, and the LAG threshold (`Umbral`). |
| **Posiciones** | Currently-open paper bets per side. |

### Tunables (environment variables)

| Variable | Default | What it controls |
|----------|---------|------------------|
| `MARKET_MINUTES` | `5` | Market interval to follow (`5` or `15`). |
| `ENABLE_RSI` | `true` | Master switch for the RSI signal. Set `false` to run **LAG-only** (RSI showed no edge in paper). |
| `ENABLE_LAG` | `true` | Master switch for the LAG signal. |
| `RSI_OPEN_WINDOW_S` | `90` | How long after the open the RSI signal stays active. |
| `RSI_PERIOD` | `14` | RSI lookback period. |
| `LAG_TRIGGER_S` | `120` | How close to the close the LAG signal activates. Lower it (e.g. `75`) to bet nearer the close, where reversals are rarer. |
| `LAG_MIN_BPS` | `5` | Min BTC move from open (basis points) to trust the LAG signal. |
| `LAG_MAX_PRICE` | `0.80` | Only buy a favorite priced at or below this. The break-even ask equals the win rate, so an ask above the measured LAG win rate is −EV. |
| `LAG_ORDER_SIZE` | `ORDER_SIZE` | Base shares for a LAG bet (at `LAG_MAX_PRICE`). |
| `LAG_ORDER_SIZE_MAX` | `LAG_ORDER_SIZE` | Max shares for a LAG bet, reached at `LAG_CHEAP_REF`. Set above `LAG_ORDER_SIZE` to stake more on cheaper (higher-edge) favorites; leave equal for a flat size. |
| `LAG_CHEAP_REF` | `0.55` | Ask at (and below) which LAG sizing maxes out. |
| `ORDER_SIZE` | `50`* | Paper position size (shares). Same variable as the arb bot; the shipped `.env.example` sets it to `5`. |
| `PAPER_CSV` | `paper_trades.csv` | Output file for settled paper trades. |

It also accepts `--poll <seconds>` (default `3`) for the loop interval.

> Note: with 5-minute markets the RSI (first 90s) and LAG (last 120s) windows cover
> most of the 300s cycle. Tightening them (e.g. `RSI_OPEN_WINDOW_S=60`,
> `LAG_TRIGGER_S=90`) keeps each signal closer to its ideal moment.

---

## 💱 Arbitrage bot

`python -m src.simple_arb_bot`

A pure-arbitrage bot for the **15-minute** BTC markets, implementing
[Jeremy Whittaker's strategy](https://jeremywhittaker.com/index.php/2024/09/24/arbitrage-in-polymarket-com/).

**The idea:** at close, exactly one of UP/DOWN pays $1.00 per share. If you buy *both*
sides for a combined cost below $1.00, you profit the difference no matter who wins.

```
UP   (goes up):   $0.48
DOWN (goes down): $0.51
──────────────────────
Total:            $0.99   ✅ < $1.00  →  $0.01/share guaranteed
```

It auto-discovers the active 15-min market, scans the order book continuously, and
triggers when `UP_ask + DOWN_ask < TARGET_PAIR_COST`.

**Paired-execution safety:** in live mode it submits both legs, then polls `get_order`
to confirm **both** filled before counting the trade. If only one leg fills, it
best-effort cancels the other and tries to flatten the filled leg with a `FAK` sell.
This is risk-*reduction*, not a guarantee — keep `ORDER_TYPE=FOK` for entries.

**Simulation vs. live:** keep `DRY_RUN=true` to scan without placing orders. Set
`DRY_RUN=false` only with funded pUSD (see [Configuration](#-configuration) and
[CLOB V2 notes](#-clob-v2-notes-pusd-collateral)).

| Variable | Default | What it controls |
|----------|---------|------------------|
| `TARGET_PAIR_COST` | `0.991` | Buy when `UP + DOWN` is below this. Lower = stricter, safer margin. |
| `ORDER_SIZE` | `5` | Shares per side (minimum 5). |
| `ORDER_TYPE` | `FOK` | Time-in-force: `FOK` (fill-or-kill), `FAK`, or `GTC`. |
| `DRY_RUN` | `true` | `true` = simulate, `false` = real orders. |
| `SIM_BALANCE` | `100` | Starting cash used while `DRY_RUN=true`. |
| `COOLDOWN_SECONDS` | `10` | Minimum seconds between executions. |
| `USE_WSS` | `false` | Stream the order book over WebSocket instead of polling. |

---

## 📈 Backtester

`python -m src.backtest`

Reconstructs the UP/DOWN game from real BTC candles (Binance): at each decision point
it asks a strategy which way the next `horizon` minutes will go, then checks the real
outcome and the simulated P&L.

```bash
python -m src.backtest                          # all strategies, defaults
python -m src.backtest --horizon 5 --days 90 --cost 0.03
```

The entry price is **modelled** (≈$0.50 + `--cost` for spread/fees) because there is no
historical Polymarket price series. A strategy only has edge if its accuracy is clearly
above `entry + cost`. Strategies live in [strategy.py](src/strategy.py): `momentum`,
`ma` (moving-average crossover), `rsi` (mean reversion), `breakout`.

---

## 📊 Live dashboard

Both bots start a lightweight, dependency-free web dashboard automatically. You can also
run a standalone balance watcher with `python -m src.dashboard`.

It shows, in real time: balance, the active market and time remaining, current UP/DOWN
prices vs. the threshold, scan/opportunity/trade counters, open positions, and a live
feed of the bot's activity.

| Variable | Default | What it controls |
|----------|---------|------------------|
| `DASHBOARD_PORT` | `8765` | Port (falls back to a random free port if taken). |
| `DASHBOARD_HOST` | `127.0.0.1` | Bind address. Set `0.0.0.0` to expose it on your LAN (e.g. a headless Pi). |
| `DASHBOARD_NO_BROWSER` | `0` | Set `1`/`true` to not auto-open a browser. |

---

## ⚙️ Configuration

`.env` is loaded **without** overriding existing environment variables, so values set in
your terminal/CI take precedence. Copy `.env.example` to `.env` to start.

### Required credentials

| Variable | How to get it |
|----------|---------------|
| `POLYMARKET_PRIVATE_KEY` | Your wallet's private key (`0x…`), from MetaMask/your wallet. |
| `POLYMARKET_API_KEY` / `POLYMARKET_API_SECRET` / `POLYMARKET_API_PASSPHRASE` | Run `python -m src.generate_api_key` (derived from your private key). |

> The API credentials are derived from the private key. Change the key → regenerate them.

### Wallet / signature type

| Variable | Description |
|----------|-------------|
| `POLYMARKET_SIGNATURE_TYPE` | `0` = EOA (MetaMask/hardware signs and funds directly) · `1` = POLY_PROXY (email/Magic login) · `2` = POLY_GNOSIS_SAFE · `3` = POLY_1271 (smart-contract wallets). |
| `POLYMARKET_FUNDER` | The wallet that **holds your pUSD**. Leave **empty** for type `0`; for `1`/`2`/`3` set it to your Polymarket proxy/Safe address. |

> ⚠️ The CLOB checks your balance based on `signature_type` + `funder`. If your pUSD lives
> in a proxy wallet but you run as `signature_type=0`, the CLOB checks your empty EOA and
> reports `$0`. **Magic.link / email users** are type `1`: your `POLYMARKET_FUNDER` is the
> "Copy address" value on your Polymarket profile, **not** your on-chain Polygon address.

The predictor (paper) bot only needs the private key to read your balance for display —
it never trades. Live arbitrage needs the full credential + funder setup above.

See the per-bot tables earlier for trading/predictor/dashboard tunables.

---

## 🛠️ Utilities

| Command | Purpose |
|---------|---------|
| `python -m src.generate_api_key` | Derive the API key/secret/passphrase from your private key. Run once, paste the output into `.env`. |
| `python -m src.test_balance` | Verify your wallet config and show your pUSD balance (via the API and directly on Polygon). |
| `python -m src.diagnose_config` | Diagnose `"invalid signature"` / `$0 balance` problems (checks funder, signature type, neg-risk detection). |

---

## 🥧 Deploy as a service (Linux / Raspberry Pi)

The [`deploy/`](deploy/) folder has a `systemd` unit that runs the **predictor bot** 24/7
with the dashboard exposed on the LAN (`0.0.0.0:8765`) and auto-restart on failure.

```bash
bash deploy/install-service.sh          # from the project root, NOT with sudo (it asks for your password only for the systemd steps)
journalctl -u btc-predictor -f          # follow the logs
```

---

## 🆕 CLOB V2 notes (pUSD collateral)

Polymarket migrated trading to **CLOB V2** on April 28, 2026; the legacy `py-clob-client`
and V1-signed orders no longer work against production. This project uses
`py-clob-client-v2`. Handled automatically: V2 client (`chain_id=137`),
`create_or_derive_api_key()`, and a `/balance-allowance/update` sync on startup (otherwise
a funded wallet can read a stale `$0`).

**What you must do manually:** hold collateral as **pUSD** (an ERC-20 backed 1:1 by USDC).
The polymarket.com UI wraps it for you; API-only traders wrap USDC.e → pUSD via the
Collateral Onramp `wrap()`. Put the pUSD in the wallet that matches your signature type.

| Contract (Polygon, chain 137) | Address |
|----------|---------|
| pUSD (collateral) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` |
| Collateral Onramp (`wrap()`) | `0x93070a847efEf7F70739046A929D47a521F5B8ee` |
| CTF Exchange V2 | `0xE111180000d2663C0091e4f400237545B87B996B` |
| Neg Risk CTF Exchange V2 | `0xe2222d279d744050d28e00520010520000310F59` |

> ℹ️ Always re-check [docs.polymarket.com/resources/contracts](https://docs.polymarket.com/resources/contracts) before sending funds.

---

## 📁 Project structure

```
btc-polymarket-bot/
├── src/
│   ├── predictor_bot.py   # 🧪 Paper-trading predictor (RSI + LAG signals)
│   ├── simple_arb_bot.py  # 💱 Pure-arbitrage bot (buy both legs < $1)
│   ├── backtest.py        # 📈 Strategy backtester over historical BTC candles
│   ├── strategy.py        # Rules-based signals (momentum, MA, RSI, breakout)
│   ├── marketdata.py      # BTC OHLCV + spot price from Binance (public API)
│   ├── dashboard.py       # 📊 Live web dashboard (stdlib only)
│   ├── trading.py         # CLOB V2 client + order execution helpers
│   ├── lookup.py          # Resolve token IDs from a Polymarket market slug
│   ├── wss_market.py      # Optional WebSocket order-book feed
│   ├── config.py          # .env loader
│   ├── generate_api_key.py / test_balance.py / diagnose_config.py   # utilities
│   └── __init__.py
├── deploy/                # systemd service + install script
├── .env.example           # configuration template
├── requirements.txt
└── README.md
```

---

## ⚠️ Warnings & disclaimer

- Start with the **predictor bot** (paper) and `DRY_RUN=true` on the arb bot.
- Do **not** set `DRY_RUN=false` without funded pUSD in the correct wallet.
- Spreads and fees can erase arbitrage profit — verify liquidity.
- Markets close every few minutes; don't accumulate positions.
- **Never share your private key.**
- This software is **educational only**. Trading involves risk; you are responsible for
  your own funds. Never invest more than you can afford to lose.

---

## 📚 Resources

- [Jeremy Whittaker's arbitrage article](https://jeremywhittaker.com/index.php/2024/09/24/arbitrage-in-polymarket-com/)
- [BTC 5-min markets](https://polymarket.com/crypto/5M) · [15-min markets](https://polymarket.com/crypto/15M)
- [py-clob-client-v2 (CLOB V2 SDK)](https://github.com/Polymarket/py-clob-client-v2)
- [Polymarket contract addresses](https://docs.polymarket.com/resources/contracts)

---

## 💰 Donations

- **Bitcoin**: bc1q7g34820ja90aeltmlkc7va04eqk7u0z7830hdt
- **Ethereum**: 0x2536eF5E8613dec01b7919A6a7933053da027414
- **PayPal**: https://www.paypal.me/jonmarcos17
