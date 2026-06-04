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
    T.append(dict(entry=ff(x[3]), size=ff(x[4]), stake=ff(x[3])*ff(x[4]),
        move=abs((e-o)/o*10000), won=(x[14]=="True"), pnl=ff(x[15]),
        t=datetime.strptime(x[5],"%Y-%m-%d %H:%M:%S")))

# span de tiempo
ts=[t["t"] for t in T]
span=(max(ts)-min(ts)).total_seconds()/86400
print(f"periodo: {min(ts)} -> {max(ts)}  = {span:.2f} dias")
print(f"trades totales: {len(T)}  ->  {len(T)/span:.1f}/dia")

# liquidez: tamaño/stake real
sizes=[t["size"] for t in T]; stakes=[t["stake"] for t in T]
print(f"\nLIQUIDEZ (lo que el libro tragó):")
print(f"  shares/trade: media {st.mean(sizes):.1f}  max {max(sizes):.0f}")
print(f"  stake $/trade: media ${st.mean(stakes):.2f}  max ${max(stakes):.2f}")

def kelly(W,p):
    b=(1-p)/p
    return W - (1-W)/b

print("\n=== SUBCONJUNTO ALTO-EDGE (move>=8 & entry<=0.75) ===")
g=[t for t in T if t["move"]>=8 and t["entry"]<=0.75]
W=sum(t["won"] for t in g)/len(g); p=st.mean([t["entry"] for t in g])
print(f"  n={len(g)} en {span:.1f}d = {len(g)/span:.1f} trades/dia")
print(f"  winrate={W:.1%}  precio medio={p:.1%}  edge={W-p:+.1%}")
f=kelly(W,p)
print(f"  Kelly completo: apostar {f:.0%} del bankroll por trade (medio Kelly {f/2:.0%})")
# crecimiento esperado por trade a fraccion f de bankroll (log growth approx)
import math
for frac,lab in [(f,"full"),(f/2,"half"),(f/4,"quarter")]:
    b=(1-p)/p
    g_win=1+frac*b; g_los=1-frac
    glog=W*math.log(g_win)+(1-W)*math.log(g_los)
    per_day=glog*(len(g)/span)
    mult_month=math.exp(per_day*30)
    print(f"  {lab:8s} Kelly: +{(math.exp(glog)-1)*100:.1f}%/trade log -> x{mult_month:,.1f} al mes (teorico, sin limites)")

print("\n=== SUBCONJUNTO MAS LAXO (move>=8) ===")
g=[t for t in T if t["move"]>=8]
W=sum(t["won"] for t in g)/len(g); p=st.mean([t["entry"] for t in g])
print(f"  n={len(g)} = {len(g)/span:.1f}/dia  winrate={W:.1%} precio={p:.1%} edge={W-p:+.1%}")
