"""Random-entry null: same day, same instrument, same risk %, same R target, entry
at a random bar in the idea's window. Does the pattern's timing beat chance?
Run after `screen.py --part all`."""
import numpy as np
import pandas as pd

import screen as S

rng = np.random.default_rng(7)
data = {tk: {D.day: D for D in S.load(tk)} for tk in S.TRADED}
tr = pd.read_pickle(S.DATA / "trades_all.pkl")
for idea, win in [("I9 extension-dbl-bottom", ("09:50", "15:00")),
                  ("I1 silver-bullet", ("09:35", "10:25")),
                  ("I2 judas-prevPM", ("09:30", "10:55"))]:
    g = tr[tr.idea == idea]
    sims = []
    for rep in range(400):
        nets = []
        for _, t in g.iterrows():
            D = data[t.tk][t.day]
            w = S.window(D.s, *win)
            k = int(rng.choice(w))
            e = float(D.s["close"].iloc[k])
            risk = e * t.risk_pct / 100
            rr = None if pd.isna(t.target) else (t.target - t.entry) / (t.entry - t.stop)
            st, px = S.judge(D.s, k, e, e - risk, None if rr is None else e + rr * risk)
            cost = ((S.COST / S.COST_PRICE[t.tk]) / (2 * risk / e) if t.tk in S.COST_PRICE
                    else S.COST / risk)
            nets.append((px - e) / risk - cost)
        sims.append(np.mean(nets))
    sims = np.array(sims)
    ud = g.groupby(["und", "day"])["net"].mean()            # cluster by underlying-day
    t = ud.mean() / (ud.std(ddof=1) / np.sqrt(len(ud)))
    print(f"{idea:<24} n={len(g)} ud={len(ud)} actual {g.net.mean():+.3f}R | null mean "
          f"{sims.mean():+.3f} sd {sims.std():.3f} | p(null>=actual)="
          f"{np.mean(sims >= g.net.mean()):.3f} | cluster t={t:.2f}")
