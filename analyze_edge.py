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
    T.append(dict(entry=ff(x[3]), move=abs((e-o)/o*10000),
        won=(x[14]=="True"), pnl=ff(x[15])))

def line(name, g):
    if not g: print(f"{name:14s}: 0"); return
    n=len(g); w=sum(t["won"] for t in g)/n
    imp=st.mean([t["entry"] for t in g])  # precio = prob implícita
    print(f"{name:14s}: n={n:3d} | winrate={w:5.1%} | precio medio={imp:5.1%} | EDGE={w-imp:+5.1%}")

print("winrate = % aciertos reales | precio = lo que pagas (prob implícita) | EDGE = ventaja real")
print("EDGE>0 = ganas a la larga ; EDGE<0 = pierdes aunque aciertes mucho\n")
line("move <8 bps", [t for t in T if t["move"]<8])
line("move 8-10",   [t for t in T if 8<=t["move"]<10])
line("move 10-15",  [t for t in T if 10<=t["move"]<15])
line("move >=15",   [t for t in T if t["move"]>=15])
print()
line("TODO (299)",  T)
print("\n--- combinado precio+move ---")
line("move>=8 & entry<=0.75", [t for t in T if t["move"]>=8 and t["entry"]<=0.75])
line("move<8 & entry>=0.78",  [t for t in T if t["move"]<8 and t["entry"]>=0.78])
