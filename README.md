# Bitcoin 15min Arbitrage Bot - Polymarket

Simple arbitrage bot implementing **Jeremy Whittaker's strategy** for Bitcoin 15-minute markets on Polymarket.

## 🎯 Strategy

**Pure arbitrage**: Buy both sides (UP + DOWN) when total cost < $1.00 to guarantee profit regardless of outcome.

### Example:
```
BTC goes up (UP):     $0.48
BTC goes down (DOWN): $0.51
─────────────────────────
Total:                $0.99  ✅ < $1.00
Profit:               $0.01 per share (1.01%)
```

**Why does it work?**
- At close, ONE of the two sides pays $1.00 per share
- If you paid $0.99 total, you earn $0.01 no matter which side wins
- It's **guaranteed profit** (pure arbitrage)

---

## 🆕 Polymarket CLOB V2 (April 28, 2026 migration)

Polymarket migrated its trading infrastructure to **CLOB V2** on April 28, 2026. The
legacy `py-clob-client` SDK and V1-signed orders **no longer work against production**.
This bot has been migrated to the official V2 SDK (`py-clob-client-v2`).

**What changed in this bot (automatic — you don't need to do anything):**
- Uses `py-clob-client-v2` instead of `py-clob-client`.
- The client still takes `chain_id=137` (Polygon) — V2 did **not** rename it to `chain`.
- API-key derivation uses the renamed `create_or_derive_api_key()` method.
- On startup the bot calls `/balance-allowance/update` (`update_balance_allowance`) to
  refresh the CLOB's cached collateral balance/allowance, which can otherwise read a
  stale `$0` for a funded wallet.
- Balance and on-chain checks now target **pUSD**, the new collateral token.

**What YOU must do manually (the bot cannot do these for you):**
1. **Hold collateral as pUSD.** V2 collateral is **pUSD (Polymarket USD)**, an ERC-20 on
   Polygon backed 1:1 by USDC, not the old USDC.e. On polymarket.com the UI wraps it for
   you automatically. API-only traders wrap USDC.e → pUSD via the **Collateral Onramp**
   contract's `wrap()` function.
2. **Put the pUSD in the wallet the CLOB actually checks** for your `POLYMARKET_SIGNATURE_TYPE`
   (your deposit/proxy wallet for proxy types, or your EOA for `signature_type=0`). See the
   signature-type table below.

**Key contract addresses on Polygon (Chain ID 137), verified against
`py_clob_client_v2.config` and [docs.polymarket.com/resources/contracts](https://docs.polymarket.com/resources/contracts):**

| Contract | Address |
|----------|---------|
| pUSD (collateral) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` |
| Collateral Onramp (`wrap()`) | `0x93070a847efEf7F70739046A929D47a521F5B8ee` |
| CTF Exchange V2 | `0xE111180000d2663C0091e4f400237545B87B996B` |
| Neg Risk CTF Exchange V2 | `0xe2222d279d744050d28e00520010520000310F59` |

> ℹ️ Always re-check the official contracts page before sending funds; addresses above are
> what the installed V2 SDK uses.

---

## 🚀 Installation

### 1. Clone the repository:
```bash
git clone https://github.com/Jonmaa/btc-polymarket-bot.git
cd btc-polymarket-bot
```

### 2. Create virtual environment and install dependencies:
```bash
python -m venv .venv
.\.venv\Scripts\activate  # Windows
# or: source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 3. Configure environment variables:

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Then configure each variable (see detailed explanation below).

---

## 🔐 Environment Variables (.env)

> Note: `.env` is loaded without overriding existing environment variables.
> This means values you set in the terminal / CI will take precedence over `.env`.

### Required Variables

| Variable | Description | How to Get It |
|----------|-------------|---------------|
| `POLYMARKET_PRIVATE_KEY` | Your wallet's private key (starts with `0x`) | Export from your wallet (MetaMask, etc.) or use the one linked to your Polymarket account |
| `POLYMARKET_API_KEY` | API key for Polymarket CLOB | Run `python -m src.generate_api_key` |
| `POLYMARKET_API_SECRET` | API secret for Polymarket CLOB | Run `python -m src.generate_api_key` |
| `POLYMARKET_API_PASSPHRASE` | API passphrase for Polymarket CLOB | Run `python -m src.generate_api_key` |

### Wallet Configuration

| Variable | Description | Value |
|----------|-------------|-------|
| `POLYMARKET_SIGNATURE_TYPE` | Type of wallet signature (CLOB V2 `SignatureTypeV2`) | `0` = EOA (MetaMask/hardware wallet signs and funds directly)<br>`1` = POLY_PROXY (EOA that owns a Polymarket proxy wallet — e.g. email/Magic login)<br>`2` = POLY_GNOSIS_SAFE (EOA that owns a Polymarket Gnosis Safe)<br>`3` = POLY_1271 (EIP-1271 smart-contract wallets / vaults) |
| `POLYMARKET_FUNDER` | The wallet that **holds your pUSD** (proxy/Safe/vault address). | Leave **empty** for `signature_type=0` (EOA). For types `1`/`2`/`3`, set it to the address that holds your collateral. |

> ⚠️ **The CLOB checks balance based on `signature_type` + `funder`.** If your pUSD lives in a
> Polymarket proxy wallet but you run with `signature_type=0` (EOA), the CLOB checks your
> empty EOA and reports `$0`. Match the signature type to the wallet that actually holds the pUSD.

#### ⚠️ Important: Magic.link users (signature_type=1)

If you use **email login** on Polymarket (Magic.link), you have **two addresses**:

1. **Signer address** (derived from your private key): This is the wallet that signs transactions.
2. **Proxy wallet address** (POLYMARKET_FUNDER): This is where your funds actually live on Polymarket.

**To find your proxy wallet address:**
1. Go to your Polymarket profile: `https://polymarket.com/@YOUR_USERNAME`
2. Click the **"Copy address"** button next to your balance
3. This is your `POLYMARKET_FUNDER` — it should look like `0x...` and is **different** from your signer address

**Common mistake:** Setting `POLYMARKET_FUNDER` to your Polygon wallet address (where you might have USDC on-chain) instead of the Polymarket proxy address. This causes `"invalid signature"` errors.

**How to verify:** Run `python -m src.test_balance`:
- "Getting USDC balance" shows the balance via Polymarket API (should show your funds)
- "Balance on-chain" queries Polygon directly (may show $0 if your funds are in the proxy, which is normal)

### Trading Configuration

| Variable | Description | Default | Recommended |
|----------|-------------|---------|-------------|
| `TARGET_PAIR_COST` | Maximum combined cost to trigger arbitrage | `0.991` | `0.99` - `0.995` |
| `ORDER_SIZE` | Number of shares per trade (minimum is 5) | `5` | Start with `5`, increase after testing |
| `ORDER_TYPE` | Order time-in-force (`FOK`, `FAK`, `GTC`) | `FOK` | Use `FOK` to avoid leaving one leg open |
| `DRY_RUN` | Simulation mode | `true` | Start with `true`, change to `false` for live trading |
| `SIM_BALANCE` | Starting cash used in simulation mode (`DRY_RUN=true`) | `0` | e.g. `100` |
| `COOLDOWN_SECONDS` | Minimum seconds between executions | `10` | Increase if you see repeated triggers |

### Optional

| Variable | Description |
|----------|-------------|
| `POLYMARKET_MARKET_SLUG` | Force a specific market slug (leave empty for auto-discovery) |
| `USE_WSS` | Enable Polymarket Market WebSocket feed (`true`/`false`) |
| `POLYMARKET_WS_URL` | Base WSS URL (default: `wss://ws-subscriptions-clob.polymarket.com`) |

---

## 🔑 Generating API Keys

Before running the bot, you need to generate your Polymarket API credentials.

### Step 1: Set your private key

Edit `.env` and add your private key:
```env
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
```

### Step 2: Run the API key generator

```bash
python -m src.generate_api_key
```

This will output something like:
```
API Key: abc123...
Secret: xyz789...
Passphrase: mypassphrase
```

### Step 3: Add the credentials to `.env`

```env
POLYMARKET_API_KEY=abc123...
POLYMARKET_API_SECRET=xyz789...
POLYMARKET_API_PASSPHRASE=mypassphrase
```

> ⚠️ **Important**: The API credentials are derived from your private key. If you change the private key, you'll need to regenerate the API credentials.

---

## � Diagnosing Configuration Issues

If you get `"invalid signature"` errors, run the diagnostic tool:

```bash
python -m src.diagnose_config
```

This will check:
- Whether your `POLYMARKET_FUNDER` is correctly set (required for Magic.link accounts)
- Whether the signer and funder addresses are different (they should be for Magic.link)
- Whether the bot can detect `neg_risk` for BTC 15min markets
- Your current pUSD balance via the Polymarket API (after a CLOB V2 balance/allowance sync)

**Common causes of "invalid signature":**
1. `POLYMARKET_FUNDER` is empty for Magic.link accounts
2. `POLYMARKET_FUNDER` is set to your Polygon wallet address instead of your Polymarket proxy wallet
3. API credentials were generated with a different private key or configuration
4. The `neg_risk` flag is incorrectly detected (fixed in latest version - bot now forces `neg_risk=True` for BTC 15min markets)

**About "Balance on-chain" showing $0:**
This is **normal** for Magic.link accounts. Your funds are held in a Polymarket proxy contract, not directly in your Polygon wallet. The "USDC balance" via API should show your correct balance.

---

## �💰 Checking Your Balance

Before trading, verify that your wallet is configured correctly and has funds:

```bash
python -m src.test_balance
```

This will show:
```
======================================================================
POLYMARKET BALANCE TEST
======================================================================
Host: https://clob.polymarket.com
Signature Type: 1
Private Key: ✓
API Key: ✓
API Secret: ✓
API Passphrase: ✓
======================================================================

1. Creating ClobClient...
   ✓ Client created

2. Deriving API credentials from private key...
   ✓ Credentials configured

3. Getting wallet address...
   ✓ Address: 0x52e78F6071719C...

4. Getting pUSD balance (COLLATERAL)...
   → Syncing balance/allowance with the CLOB (V2)...
   💰 BALANCE pUSD: $25.123456

5. Verifying balance directly on Polygon...
   🔗 pUSD on-chain (0x...): $25.123456

======================================================================
TEST COMPLETED
======================================================================
```

> ⚠️ If balance shows `$0.00` but you have funds on Polymarket, check your `POLYMARKET_SIGNATURE_TYPE` and `POLYMARKET_FUNDER` settings.

---

## 💻 Usage

### Simulation mode (recommended first):

Make sure `DRY_RUN=true` in `.env`, then:
```bash
python -m src.simple_arb_bot
```

The bot will scan for opportunities but won't place real orders.

### 📊 Live web dashboard

When you run the bot it automatically launches a small local web dashboard and opens it
in your browser. It shows, in real time: your pUSD balance, the active market and time
remaining, current UP/DOWN prices vs. the threshold, scan/opportunity/trade counters,
open positions, and a live feed of the bot's activity.

- It binds to **localhost only** (`http://127.0.0.1:<port>`), preferring port **8765** and
  falling back to a random free port if it's taken.
- The URL is printed in the logs (`📊 Dashboard en vivo: http://127.0.0.1:8765`).

Optional environment variables:

| Variable | Description |
|----------|-------------|
| `DASHBOARD_PORT` | Force a specific port instead of 8765 |
| `DASHBOARD_NO_BROWSER` | Set to `1`/`true` to not auto-open the browser |

You can also run **just** a balance watcher (no trading logic) with:
```bash
python -m src.dashboard
```

### Optional: WebSocket market data (lower latency)

By default the bot polls the CLOB order book over HTTPS. You can optionally enable
the Polymarket CLOB **Market WebSocket** feed to receive pushed order book updates
and reduce per-scan latency.

Set the following in your `.env`:

```env
USE_WSS=true
POLYMARKET_WS_URL=wss://ws-subscriptions-clob.polymarket.com
```

Notes on WSS mode:
- The Market channel can send either a single JSON object or a JSON array (batched events). The bot handles both.
- If the connection drops or a proxy/firewall blocks WSS, the bot will reconnect and print the error reason.
- Internally, WSS mode maintains an in-memory L2 book using `book` snapshots + `price_change` deltas.

Then run the bot the same way:

```bash
python -m src.simple_arb_bot
```

### Live trading mode:

1. Change `DRY_RUN=false` in `.env`
2. Ensure you have USDC in your Polymarket wallet
3. Run:
```bash
python -m src.simple_arb_bot
```

### Paired execution safety (avoids “one-leg fills”)

In real trading, it’s possible for **only one leg** (UP or DOWN) to fill if the book moves.
To reduce the risk of ending up with an imbalanced position, the bot now:

- **Submits both legs**, then **verifies** each order by polling `get_order`.
- Only logs **“EXECUTED (BOTH LEGS FILLED)”** and increments `trades_executed` when **both** legs are confirmed filled.
- If only one leg fills, it will **best-effort cancel** the remaining order(s) and attempt to **flatten exposure** by submitting a
   `SELL` on the filled leg at the current `best_bid` using `FAK` (fill-and-kill).

Recommendation:
- Keep `ORDER_TYPE=FOK` for entries (fill-or-kill) to avoid leaving open orders.

Important:
- This is **risk-reduction**, not a perfect guarantee. In fast markets, unwind orders can also fail or partially fill.
- Always monitor your positions on Polymarket, especially if you see a “Partial fill detected” warning.

---

## 📊 Features

✅ **Auto-discovers** active BTC 15min market  
✅ **Detects opportunities** when price_up + price_down < threshold  
✅ **Execution-aware pricing**: uses order book asks (not last trade price)  
✅ **Depth-aware sizing**: walks the ask book to ensure `ORDER_SIZE` can fill (uses a conservative “worst fill” price)  
✅ **Continuous scanning** with no delays (maximum speed)  
✅ **Lower latency polling**: fetches UP/DOWN order books concurrently  
✅ **Auto-switches** to next market when current one closes  
✅ **Final summary** with total investment, profit and market result  
✅ **Simulation mode** for risk-free testing  
✅ **Balance verification** before executing trades  
✅ **Paired execution verification**: confirms both legs filled (otherwise cancels + attempts to unwind)  

---

## 📈 Example Output

```
🚀 BITCOIN 15MIN ARBITRAGE BOT STARTED
======================================================================
Market: btc-updown-15m-1765301400
Time remaining: 12m 34s
Mode: 🔸 SIMULATION
Cost threshold: $0.99
Order size: 5 shares
======================================================================

[Scan #1] 12:34:56
No arbitrage: UP=$0.48 + DOWN=$0.52 = $1.00 (needs < $0.99)

🎯 ARBITRAGE OPPORTUNITY DETECTED
======================================================================
UP price (goes up):   $0.4800
DOWN price (goes down): $0.5100
Total cost:           $0.9900
Profit per share:     $0.0100
Profit %:             1.01%
----------------------------------------------------------------------
Order size:           5 shares each side
Total investment:     $4.95
Expected payout:      $5.00
EXPECTED PROFIT:      $0.05
======================================================================
✅ ARBITRAGE EXECUTED SUCCESSFULLY

🏁 MARKET CLOSED - FINAL SUMMARY
======================================================================
Market: btc-updown-15m-1765301400
Result: UP (goes up) 📈
Mode: 🔴 REAL TRADING
----------------------------------------------------------------------
Total opportunities detected:  3
Total trades executed:         3
Total shares bought:           30
----------------------------------------------------------------------
Total invested:                $14.85
Expected payout at close:      $15.00
Expected profit:               $0.15 (1.01%)
======================================================================
```

---

## 📁 Project Structure

```
Bot/
├── src/
│   ├── simple_arb_bot.py    # Main arbitrage bot
│   ├── config.py            # Configuration loader
│   ├── lookup.py            # Market ID fetcher
│   ├── trading.py           # Order execution
│   ├── generate_api_key.py  # API key generator utility
│   └── test_balance.py      # Balance verification utility
├── tests/
│   └── test_state.py        # Unit tests
├── .env                     # Environment variables (create from .env.example)
├── .env.example             # Environment template
├── requirements.txt         # Dependencies
└── README.md                # This file
```

---

## ⚠️ Warnings

- ⚠️ **DO NOT use `DRY_RUN=false` without funds** in your Polymarket wallet
- ⚠️ **Spreads** can eliminate profit (verify liquidity)
- ⚠️ Markets close every **15 minutes** (don't accumulate positions)
- ⚠️ Start with **small orders** (ORDER_SIZE=5)
- ⚠️ This software is **educational only** - use at your own risk
- ⚠️ **Never share your private key** with anyone

---

## 🔧 Troubleshooting

### "Invalid signature" error
- Verify `POLYMARKET_SIGNATURE_TYPE` matches your wallet type
- Regenerate API credentials with `python -m src.generate_api_key`

### Balance shows $0 but I have funds (CLOB V2)
Work through these in order:
1. **Are your funds in pUSD?** V2 collateral is **pUSD**, not USDC.e. If your USDC.e was
   never wrapped, the CLOB sees `$0`. Wrap on polymarket.com (automatic) or via the
   Collateral Onramp `wrap()` for API-only flows.
2. **Does `signature_type` match the wallet holding the pUSD?** The CLOB checks balance based
   on `POLYMARKET_SIGNATURE_TYPE` + `POLYMARKET_FUNDER`. EOA (`0`) checks your signer address;
   proxy types (`1`/`2`/`3`) check the `funder` address. A mismatch reports `$0`.
3. **Verify on-chain.** Run `python -m src.test_balance` — it prints your wallet address and
   queries pUSD directly on Polygon (requires `web3`; install with `pip install web3`). If
   on-chain pUSD is `$0` for both your signer and funder, the funds are in a different wallet.
4. The balance/allowance sync now runs automatically; a persistent `$0` after the sync means
   the issue is one of the three above, not the SDK.

### "No active BTC 15min market found"
- Markets open every 15 minutes; wait for the next one
- Check your internet connection
- Try visiting https://polymarket.com/crypto/15M manually

---

## 📚 Resources

- [Jeremy Whittaker's original article](https://jeremywhittaker.com/index.php/2024/09/24/arbitrage-in-polymarket-com/)
- [Polymarket](https://polymarket.com/)
- [BTC 15min Markets](https://polymarket.com/crypto/15M)
- [py-clob-client-v2 (CLOB V2 SDK)](https://github.com/Polymarket/py-clob-client-v2)
- [Polymarket CLOB V2 migration guide](https://docs.polymarket.com/v2-migration)
- [Polymarket contract addresses](https://docs.polymarket.com/resources/contracts)

---

## 💰 Donations

If you find this project helpful, consider supporting me with a donation:

- **Bitcoin**: bc1q7g34820ja90aeltmlkc7va04eqk7u0z7830hdt
- **Ethereum**: 0x2536eF5E8613dec01b7919A6a7933053da027414
- **PayPal**: https://www.paypal.me/jonmarcos17 

---

## ⚖️ Disclaimer

This software is for educational purposes only. Trading involves risk. I am not responsible for financial losses. Always do your own research and never invest more than you can afford to lose.
