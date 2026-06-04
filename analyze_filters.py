import csv, statistics as st
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
    T.append(dict(entry=ff(x[3]), size=ff(x[4]), tleft=int(x[6]),
        move=abs((e-o)/o*10000), hour=int(x[5][11:13]),
        won=(x[14]=="True"), pnl=ff(x[15])))

def rpt(name, sub):
    if not sub:
        print(f"{name:42s}: n=0"); return
    n=len(sub); w=sum(t["won"] for t in sub); p=sum(t["pnl"] for t in sub)
    s=sum(t["size"]*t["entry"] for t in sub)
    print(f"{name:42s}: n={n:3d} win={w/n:5.1%} pnl=${p:+8.2f} ROI={p/s:+6.1%}")

print("=== FILTER SIMULATIONS (vs current all-299 baseline) ===")
rpt("baseline (current rules)", T)
rpt("entry<=0.72", [t for t in T if t["entry"]<=0.72])
rpt("entry<=0.75", [t for t in T if t["entry"]<=0.75])
rpt("move>=8bps", [t for t in T if t["move"]>=8])
rpt("move>=10bps", [t for t in T if t["move"]>=10])
rpt("drop hour 20-24", [t for t in T if not (20<=t["hour"]<24)])
rpt("entry<=0.72 AND move>=8", [t for t in T if t["entry"]<=0.72 and t["move"]>=8])
rpt("entry<=0.75 AND move>=8", [t for t in T if t["entry"]<=0.75 and t["move"]>=8])
rpt("entry<=0.75 & move>=8 & not h20-24",
    [t for t in T if t["entry"]<=0.75 and t["move"]>=8 and not(20<=t["hour"]<24)])
rpt("entry<=0.72 & move>=8 & not h20-24",
    [t for t in T if t["entry"]<=0.72 and t["move"]>=8 and not(20<=t["hour"]<24)])

# correlation move vs entry
import math
xs=[t["move"] for t in T]; ys=[t["entry"] for t in T]
mx,my=st.mean(xs),st.mean(ys)
cov=sum((a-mx)*(b-my) for a,b in zip(xs,ys))
den=math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
print(f"\ncorr(move_bps, entry_price) = {cov/den:+.3f}")
print(f"(if negative: bigger BTC move -> CHEAPER favorite = the lag edge)")
