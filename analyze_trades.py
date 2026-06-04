import csv, statistics as st
from collections import defaultdict

rows = []
with open("paper_trades.csv", newline="", encoding="utf-8") as f:
    r = csv.reader(f)
    for x in r:
        if x and x[0].startswith("btc-"):
            rows.append(x)

def f(v):
    try: return float(v)
    except: return None

trades = []
for x in rows:
    t = dict(
        market=x[0], signal=x[1], side=x[2],
        entry=f(x[3]), size=f(x[4]), placed=x[5],
        tleft=int(x[6]), btc_open=f(x[7]), btc_entry=f(x[8]),
        wend=int(x[9]), status=x[11], outcome=x[12],
        btc_close=f(x[13]), won=(x[14]=="True"), pnl=f(x[15]),
    )
    if t["btc_open"]:
        t["move_bps"] = (t["btc_entry"]-t["btc_open"])/t["btc_open"]*10000
        t["close_bps"] = (t["btc_close"]-t["btc_open"])/t["btc_open"]*10000
        # margin held from entry to close (positive = leader stayed ahead in same dir)
        t["hour"] = int(t["placed"][11:13])
        t["day"] = t["placed"][:10]
    trades.append(t)

N=len(trades)
wins=sum(t["won"] for t in trades)
pnl=sum(t["pnl"] for t in trades)
staked=sum(t["size"]*t["entry"] for t in trades)
print(f"=== OVERALL ===")
print(f"trades={N}  wins={wins}  winrate={wins/N:.1%}")
print(f"total pnl=${pnl:+.2f}  staked=${staked:.2f}  ROI={pnl/staked:.1%}")
print(f"avg pnl/trade=${pnl/N:+.3f}")

def bucket(name, keyfn, edges, labels):
    print(f"\n=== {name} ===")
    groups=defaultdict(list)
    for t in trades:
        v=keyfn(t)
        if v is None: continue
        for i,e in enumerate(edges):
            if v<e:
                groups[labels[i]].append(t); break
        else:
            groups[labels[-1]].append(t)
    for lab in labels:
        g=groups.get(lab,[])
        if not g:
            print(f"  {lab:>14}: n=0")
            continue
        w=sum(t["won"] for t in g); p=sum(t["pnl"] for t in g)
        s=sum(t["size"]*t["entry"] for t in g)
        print(f"  {lab:>14}: n={len(g):3d}  win={w/len(g):5.1%}  pnl=${p:+8.2f}  ROI={p/s:+6.1%}")

bucket("ENTRY PRICE (favorite cost)", lambda t:t["entry"],
       [0.55,0.65,0.72,0.78,0.85], ["<0.55","0.55-0.65","0.65-0.72","0.72-0.78","0.78-0.85",">=0.85"])

bucket("TIME LEFT (s)", lambda t:t["tleft"],
       [40,70,90,110], ["<40","40-70","70-90","90-110",">=110"])

bucket("MOVE BPS (|abs| at entry)", lambda t:abs(t["move_bps"]),
       [5,10,15,25,40], ["<5","5-10","10-15","15-25","25-40",">=40"])

bucket("HOUR UTC", lambda t:t["hour"],
       [4,8,12,16,20], ["00-04","04-08","08-12","12-16","16-20","20-24"])

print("\n=== SIDE ===")
for sd in ("UP","DOWN"):
    g=[t for t in trades if t["side"]==sd]
    w=sum(t["won"] for t in g); p=sum(t["pnl"] for t in g)
    print(f"  {sd:>4}: n={len(g):3d}  win={w/len(g):5.1%}  pnl=${p:+8.2f}")

print("\n=== BY DAY ===")
days=defaultdict(list)
for t in trades: days[t["day"]].append(t)
for d in sorted(days):
    g=days[d]; w=sum(t["won"] for t in g); p=sum(t["pnl"] for t in g)
    print(f"  {d}: n={len(g):3d}  win={w/len(g):5.1%}  pnl=${p:+8.2f}")

# Reversal analysis: how close was the entry margin vs final
print("\n=== ENTRY MARGIN vs OUTCOME (reversals) ===")
# margin_bps = how far ahead leader was at entry (abs move)
wins_m=[abs(t["move_bps"]) for t in trades if t["won"]]
loss_m=[abs(t["move_bps"]) for t in trades if not t["won"]]
print(f"  avg |move_bps| WINS  = {st.mean(wins_m):.1f}  (median {st.median(wins_m):.1f})")
print(f"  avg |move_bps| LOSSES= {st.mean(loss_m):.1f}  (median {st.median(loss_m):.1f})")
wins_t=[t["tleft"] for t in trades if t["won"]]
loss_t=[t["tleft"] for t in trades if not t["won"]]
print(f"  avg time_left WINS   = {st.mean(wins_t):.1f}")
print(f"  avg time_left LOSSES = {st.mean(loss_t):.1f}")
wins_e=[t["entry"] for t in trades if t["won"]]
loss_e=[t["entry"] for t in trades if not t["won"]]
print(f"  avg entry_price WINS = {st.mean(wins_e):.3f}")
print(f"  avg entry_price LOSS = {st.mean(loss_e):.3f}")

# EV check: is buying favorites above their implied prob -EV?
print("\n=== EV: realized winrate vs entry price (implied prob) ===")
for lo,hi in [(0.0,0.6),(0.6,0.7),(0.7,0.75),(0.75,0.8),(0.8,1.0)]:
    g=[t for t in trades if lo<=t["entry"]<hi]
    if not g: continue
    w=sum(t["won"] for t in g)/len(g)
    implied=st.mean([t["entry"] for t in g])
    print(f"  entry {lo:.2f}-{hi:.2f}: n={len(g):3d} realized_win={w:5.1%} implied={implied:5.1%} edge={w-implied:+.1%}")
