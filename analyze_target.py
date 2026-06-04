import csv, statistics as st
from datetime import datetime
rows=[]
with open("paper_trades.csv", newline="", encoding="utf-8") as f:
    for x in csv.reader(f):
        if x and x[0].startswith("btc-"): rows.append(x)
def ff(v):
    try: return float(v)
    except: return None
T=[]
for x in rows:
    o=ff(x[7]); e=ff(x[8])
    if not o: continue
    T.append(dict(entry=ff(x[3]),size=ff(x[4]),move=abs((e-o)/o*10000),
        won=(x[14]=="True"),pnl=ff(x[15]),t=datetime.strptime(x[5],"%Y-%m-%d %H:%M:%S")))
span=(max(t["t"] for t in T)-min(t["t"] for t in T)).total_seconds()/86400

TARGET=2000  # $/mes
print(f"OBJETIVO: ${TARGET}/mes = ${TARGET/30:.1f}/dia\n")

def calc(name,sub,haircut):
    n=len(sub); W=sum(t["won"] for t in sub)/n; p=st.mean([t["entry"] for t in sub])
    edge=(W-p)*haircut
    perday=n/span
    trades_mes=perday*30
    if edge<=0:
        print(f"{name}: edge<=0 tras haircut, descartado"); return
    stake=TARGET/(trades_mes*edge)
    # bankroll si stake = 8% banca (riesgo bajo)
    bank=stake/0.08
    print(f"{name}")
    print(f"   {perday:.1f} trades/dia ({trades_mes:.0f}/mes) | winrate {W:.1%} | edge papel {(W-p):+.1%} -> usado {edge:+.1%} (haircut x{haircut})")
    print(f"   stake necesario: ${stake:.1f}/trade  (hoy el libro tragó ~$12 medio, $22 max)")
    print(f"   banca a 8%/trade: ~${bank:.0f}")
    print()

print("=== si edge REAL = igual que papel (optimista) ===")
calc("ALTO-EDGE (move>=8 & entry<=0.75)",[t for t in T if t["move"]>=8 and t["entry"]<=0.75],1.0)
calc("LAXO (move>=8)",[t for t in T if t["move"]>=8],1.0)

print("=== si edge REAL = MITAD del papel (realista, dinero real) ===")
calc("ALTO-EDGE",[t for t in T if t["move"]>=8 and t["entry"]<=0.75],0.5)
calc("LAXO (move>=8)",[t for t in T if t["move"]>=8],0.5)

print("=== si edge REAL = 1/3 del papel (pesimista) ===")
calc("LAXO (move>=8)",[t for t in T if t["move"]>=8],0.333)
