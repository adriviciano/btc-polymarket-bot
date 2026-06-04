import csv, statistics as st
from collections import defaultdict
rows=[]
with open("paper_trades.csv", newline="", encoding="utf-8") as f:
    for x in csv.reader(f):
        if x and x[0].startswith("btc-"): rows.append(x)
def ff(v):
    try: return float(v)
    except: return None
T=[]
sigs=set()
for x in rows:
    o=ff(x[7]); e=ff(x[8])
    sigs.add(x[1])
    if not o: continue
    T.append(dict(sig=x[1], side=x[2], entry=ff(x[3]), size=ff(x[4]),
        tleft=int(x[6]), move=abs((e-o)/o*10000), hour=int(x[5][11:13]),
        day=x[5][:10], won=(x[14]=="True"), pnl=ff(x[15])))

lag=[t for t in T if t["sig"]=="LAG"]
print(f"señales en csv: {sigs}  | total filas LAG analizadas: {len(lag)}")

W=[t for t in lag if t["won"]]
L=[t for t in lag if not t["won"]]
gp=sum(t["pnl"] for t in W)
gl=sum(t["pnl"] for t in L)
net=gp+gl
print("\n=== GLOBAL LAG ===")
print(f"  operaciones : {len(lag)}")
print(f"  aciertos    : {len(W)} ({len(W)/len(lag):.1%})")
print(f"  fallos      : {len(L)} ({len(L)/len(lag):.1%})")
print(f"  ganado bruto: ${gp:+.2f}")
print(f"  perdido brut: ${gl:+.2f}")
print(f"  NETO        : ${net:+.2f}")
print(f"  ganancia media por acierto: ${gp/len(W):+.3f}")
print(f"  perdida media por fallo   : ${gl/len(L):+.3f}")
print(f"  mejor trade : ${max(t['pnl'] for t in lag):+.2f}")
print(f"  peor trade  : ${min(t['pnl'] for t in lag):+.2f}")

print("\n=== POR DIA (neto) ===")
d=defaultdict(list)
for t in lag: d[t["day"]].append(t)
for k in sorted(d):
    g=d[k]; w=sum(t["won"] for t in g); p=sum(t["pnl"] for t in g)
    print(f"  {k}: {len(g):3d} ops | {w/len(g):5.1%} win | neto ${p:+8.2f}")

def grp(name, keyfn, edges, labs):
    print(f"\n=== {name} ===")
    G=defaultdict(list)
    for t in lag:
        v=keyfn(t)
        for i,e in enumerate(edges):
            if v<e: G[labs[i]].append(t); break
        else: G[labs[-1]].append(t)
    for lb in labs:
        g=G.get(lb,[])
        if not g: print(f"  {lb:>12}: 0 ops"); continue
        w=sum(t["won"] for t in g); p=sum(t["pnl"] for t in g)
        gw=sum(t["pnl"] for t in g if t["won"]); gv=sum(t["pnl"] for t in g if not t["won"])
        print(f"  {lb:>12}: {len(g):3d} ops | {w/len(g):5.1%} win | gana ${gw:+7.2f} pierde ${gv:+7.2f} | neto ${p:+8.2f}")

grp("PRECIO ENTRADA (coste favorito)", lambda t:t["entry"],
    [0.55,0.65,0.72,0.78], ["<0.55","0.55-0.65","0.65-0.72","0.72-0.78",">=0.78"])
grp("MOVIMIENTO BTC (bps al entrar)", lambda t:t["move"],
    [8,10,15], ["<8","8-10","10-15",">=15"])
grp("HORA UTC", lambda t:t["hour"],
    [4,8,12,16,20], ["00-04","04-08","08-12","12-16","16-20","20-24"])
grp("SEGUNDOS RESTANTES", lambda t:t["tleft"],
    [70,90,110], ["<70","70-90","90-110",">=110"])

print("\n=== LADO ===")
for s in ("UP","DOWN"):
    g=[t for t in lag if t["side"]==s]
    w=sum(t["won"] for t in g); p=sum(t["pnl"] for t in g)
    print(f"  {s:>4}: {len(g):3d} ops | {w/len(g):5.1%} win | neto ${p:+8.2f}")
