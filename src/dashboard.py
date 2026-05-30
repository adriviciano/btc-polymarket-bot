"""Lightweight live web dashboard for the BTC 15min arbitrage bot.

Stdlib-only (no extra dependencies). Serves a single auto-refreshing page plus a
JSON state endpoint. The bot pushes structured fields (balance, prices, counters)
into a shared DashboardState, and a logging handler captures the bot's activity
feed automatically.

Usage:
  - Integrated: simple_arb_bot starts it automatically (see ensure_started).
  - Standalone balance watcher:  python -m src.dashboard
"""

import json
import logging
import os
import random
import socket
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class DashboardState:
    """Thread-safe snapshot of what the bot is doing."""

    def __init__(self, max_log: int = 400):
        self._lock = threading.Lock()
        self.data = {
            "mode": None,                 # "SIMULATION" / "LIVE"
            "balance": None,
            "balance_label": "Balance (pUSD)",
            "market_slug": None,
            "time_remaining": None,
            "threshold": None,
            "order_size": None,
            "scans": 0,
            "opportunities": 0,
            "trades": 0,
            "price_up": None,
            "price_down": None,
            "total_cost": None,
            "up_token": None,
            "down_token": None,
            "total_invested": 0.0,
            "expected_profit": 0.0,
            "positions": {"up": 0, "down": 0},
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": None,
        }
        self.logs = deque(maxlen=max_log)

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if v is not None:
                    self.data[k] = v
            self.data["updated_at"] = datetime.now().strftime("%H:%M:%S")

    def add_log(self, msg: str, level: str = "INFO"):
        with self._lock:
            self.logs.append({
                "t": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "msg": msg,
            })

    def snapshot(self) -> dict:
        with self._lock:
            d = dict(self.data)
            d["logs"] = list(self.logs)
            return d


class _StateLogHandler(logging.Handler):
    """Forwards log records into the dashboard activity feed."""

    def __init__(self, state: DashboardState):
        super().__init__()
        self.state = state

    def emit(self, record: logging.LogRecord):
        try:
            self.state.add_log(record.getMessage(), record.levelname)
        except Exception:
            pass


# --- module-level singletons (survive bot re-init / market rollover) ----------
_STATE = DashboardState()
_STARTED = False
_SERVER = None
_URL = None
_LOCK = threading.Lock()


def get_state() -> DashboardState:
    return _STATE


_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BTC 15m Arbitrage Bot</title>
<style>
  :root{
    --bg:#0b0f17; --card:#121826; --card2:#0e1420; --line:#1f2937;
    --txt:#e5e7eb; --muted:#94a3b8; --green:#22c55e; --red:#ef4444;
    --amber:#f59e0b; --blue:#3b82f6; --accent:#38bdf8;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
    padding:14px 20px;border-bottom:1px solid var(--line);background:var(--card2);}
  header h1{font-size:16px;margin:0;font-weight:700;letter-spacing:.3px}
  .badge{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700}
  .badge.sim{background:rgba(59,130,246,.18);color:#93c5fd;border:1px solid #1d4ed8}
  .badge.live{background:rgba(239,68,68,.18);color:#fca5a5;border:1px solid #b91c1c}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
  .dot.ok{background:var(--green)} .dot.bad{background:var(--red)}
  .spacer{flex:1}
  .muted{color:var(--muted);font-size:12px}
  main{padding:20px;max-width:1200px;margin:0 auto}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
  .card .k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
  .card .v{font-size:26px;font-weight:800;margin-top:6px}
  .card.balance .v{font-size:34px;color:var(--green)}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
  @media(max-width:760px){.row2{grid-template-columns:1fr}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
  .panel h2{font-size:13px;margin:0 0 12px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
  .price{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px dashed var(--line)}
  .price:last-child{border-bottom:none}
  .price .lbl{color:var(--muted)}
  .price .num{font-weight:700}
  .total.good{color:var(--green)} .total.bad{color:var(--amber)}
  #log{height:360px;overflow:auto;background:var(--card2);border:1px solid var(--line);
    border-radius:10px;padding:10px;font-size:12.5px;line-height:1.5}
  .l{white-space:pre-wrap;word-break:break-word}
  .l .ts{color:#64748b;margin-right:8px}
  .l.INFO{color:#cbd5e1}
  .l.WARNING{color:var(--amber)}
  .l.ERROR{color:var(--red)}
  .l.CRITICAL{color:var(--red);font-weight:700}
</style>
</head>
<body>
<header>
  <h1>₿ BTC 15m Arbitrage Bot</h1>
  <span id="mode" class="badge sim">—</span>
  <span class="muted" id="market">—</span>
  <span class="spacer"></span>
  <span class="muted"><span id="dot" class="dot bad"></span><span id="conn">conectando…</span></span>
  <span class="muted">act: <span id="updated">—</span></span>
</header>
<main>
  <div class="grid">
    <div class="card balance"><div class="k" id="balLabel">Balance (pUSD)</div><div class="v" id="balance">—</div></div>
    <div class="card"><div class="k">Tiempo restante</div><div class="v" id="timeRem">—</div></div>
    <div class="card"><div class="k">Escaneos</div><div class="v" id="scans">0</div></div>
    <div class="card"><div class="k">Oportunidades</div><div class="v" id="opps">0</div></div>
    <div class="card"><div class="k">Trades</div><div class="v" id="trades">0</div></div>
    <div class="card"><div class="k">Invertido</div><div class="v" id="invested">$0.00</div></div>
    <div class="card"><div class="k">Beneficio esperado</div><div class="v" id="profit">$0.00</div></div>
  </div>
  <div class="row2">
    <div class="panel">
      <h2>Precios actuales</h2>
      <div class="price"><span class="lbl">UP (sube)</span><span class="num" id="pUp">—</span></div>
      <div class="price"><span class="lbl">DOWN (baja)</span><span class="num" id="pDown">—</span></div>
      <div class="price"><span class="lbl">Total UP+DOWN</span><span class="num total" id="pTotal">—</span></div>
      <div class="price"><span class="lbl">Umbral (compra si ≤)</span><span class="num" id="thr">—</span></div>
      <div class="price"><span class="lbl">Posiciones</span><span class="num" id="pos">UP 0 / DOWN 0</span></div>
    </div>
    <div class="panel">
      <h2>Actividad del bot</h2>
      <div id="log"></div>
    </div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const money = v => (v===null||v===undefined) ? "—" : "$"+Number(v).toFixed(2);
const price = v => (v===null||v===undefined) ? "—" : "$"+Number(v).toFixed(4);
let lastLogLen = -1;

async function tick(){
  try{
    const s = await (await fetch('/api/state',{cache:'no-store'})).json();
    $('dot').className='dot ok'; $('conn').textContent='conectado';
    const live = s.mode==='LIVE';
    $('mode').textContent = s.mode || '—';
    $('mode').className = 'badge '+(live?'live':'sim');
    $('market').textContent = s.market_slug || '—';
    $('updated').textContent = s.updated_at || '—';
    $('balLabel').textContent = s.balance_label || 'Balance (pUSD)';
    $('balance').textContent = money(s.balance);
    $('timeRem').textContent = s.time_remaining || '—';
    $('scans').textContent = s.scans ?? 0;
    $('opps').textContent = s.opportunities ?? 0;
    $('trades').textContent = s.trades ?? 0;
    $('invested').textContent = money(s.total_invested);
    $('profit').textContent = money(s.expected_profit);
    $('pUp').textContent = price(s.price_up);
    $('pDown').textContent = price(s.price_down);
    $('thr').textContent = price(s.threshold);
    const tot = $('pTotal');
    tot.textContent = price(s.total_cost);
    tot.className = 'num total';
    if(s.total_cost!=null && s.threshold!=null)
      tot.classList.add(s.total_cost<=s.threshold ? 'good':'bad');
    if(s.positions) $('pos').textContent = 'UP '+s.positions.up+' / DOWN '+s.positions.down;

    if(s.logs && s.logs.length !== lastLogLen){
      lastLogLen = s.logs.length;
      const box = $('log');
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.innerHTML = s.logs.map(l =>
        '<div class="l '+l.level+'"><span class="ts">'+l.t+'</span>'+
        l.msg.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</div>').join('');
      if(atBottom) box.scrollTop = box.scrollHeight;
    }
  }catch(e){
    $('dot').className='dot bad'; $('conn').textContent='desconectado';
  }
}
setInterval(tick, 1500); tick();
</script>
</body>
</html>"""


def _make_handler(state: DashboardState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # silence default per-request console logging

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path.startswith("/api/state"):
                self._send(json.dumps(state.snapshot()).encode("utf-8"),
                           "application/json; charset=utf-8")
            else:
                self._send(_PAGE.encode("utf-8"), "text/html; charset=utf-8")

    return Handler


def _can_bind(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pick_port(preferred: int, host: str = "127.0.0.1", insist: bool = False) -> int:
    # When a fixed port was requested (e.g. DASHBOARD_PORT, for a stable URL), retry it
    # for a few seconds before giving up: on a service restart the previous instance may
    # still hold the port for ~1s, and silently jumping to a random port makes the
    # dashboard "disappear" from its usual address.
    attempts = 12 if insist else 1
    for i in range(attempts):
        if _can_bind(host, preferred):
            return preferred
        if i < attempts - 1:
            time.sleep(0.5)
    if insist:
        logger.warning(f"Port {preferred} still busy after retries; falling back to a random port")
    for _ in range(30):
        port = random.randint(8000, 9899)
        if _can_bind(host, port):
            return port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def ensure_started(preferred_port: int = 8765, open_browser: bool = True):
    """Start the dashboard server once (idempotent). Returns the URL or None."""
    global _STARTED, _SERVER, _URL
    with _LOCK:
        if _STARTED:
            return _URL

        # Capture the bot's activity into the feed (attach once, to the root logger).
        handler = _StateLogHandler(_STATE)
        handler.setLevel(logging.INFO)
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            # don't lower below WARNING for the console, but let our handler see INFO
            handler.addFilter(lambda r: r.levelno >= logging.INFO)

        env_port = os.getenv("DASHBOARD_PORT") or os.getenv("POLYMARKET_DASHBOARD_PORT")
        insist = False
        if env_port:
            try:
                preferred_port = int(env_port)
                insist = True  # an explicit port means the user wants a stable URL
            except ValueError:
                pass

        # Bind host: 127.0.0.1 by default (only reachable from this machine).
        # Set DASHBOARD_HOST=0.0.0.0 to expose it on the LAN (e.g. a headless
        # Raspberry Pi you want to reach from your laptop/phone).
        host = (os.getenv("DASHBOARD_HOST") or "127.0.0.1").strip() or "127.0.0.1"

        port = _pick_port(preferred_port, host, insist=insist)
        try:
            server = ThreadingHTTPServer((host, port), _make_handler(_STATE))
        except OSError as e:
            logger.warning(f"Could not start dashboard server: {e}")
            return None

        thread = threading.Thread(target=server.serve_forever, daemon=True,
                                  name="dashboard-http")
        thread.start()
        _SERVER = server
        _URL = f"http://{host}:{port}"
        _STARTED = True

        if open_browser and os.getenv("DASHBOARD_NO_BROWSER", "").lower() not in ("1", "true"):
            try:
                import webbrowser
                webbrowser.open(_URL)
            except Exception:
                pass
        return _URL


def _standalone_balance_watcher():
    """Run the dashboard by itself and poll the pUSD balance periodically."""
    import time as _time
    from .config import load_settings
    from .trading import get_balance

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")
    settings = load_settings()
    url = ensure_started()
    print("=" * 60)
    print(f"📊 Balance dashboard: {url}")
    print("=" * 60)
    _STATE.update(mode="LIVE" if not settings.dry_run else "SIMULATION",
                  balance_label="Balance (pUSD)", threshold=settings.target_pair_cost,
                  order_size=settings.order_size)
    while True:
        try:
            bal = get_balance(settings)
            _STATE.update(balance=round(bal, 6))
            logger.info(f"Balance pUSD: ${bal:.6f}")
        except Exception as e:
            logger.warning(f"No se pudo leer el balance: {e}")
        _time.sleep(15)


if __name__ == "__main__":
    try:
        _standalone_balance_watcher()
    except KeyboardInterrupt:
        print("\nDashboard detenido.")
