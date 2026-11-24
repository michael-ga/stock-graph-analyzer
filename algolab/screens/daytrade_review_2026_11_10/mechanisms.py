import sys, pickle, glob, os, numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore')
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
S = sys.argv[1]
H = {}
for f in glob.glob('.local/replay-ab/hist/2026-11-01/*.pkl'):
    tk = os.path.basename(f)[:-4]; H[tk] = pickle.load(open(f, 'rb'))
TK = sorted(H); print('tickers', TK)


def reg(tk):
    i5 = H[tk]['i5']; r = i5.between_time('09:30', '15:55').copy()
    r['day'] = r.index.tz_localize(None).normalize(); r['t'] = r.index.strftime('%H:%M'); return r


R = {tk: reg(tk) for tk in TK}

print('\n##### A. RVOL gate (orb.MIN_BREAKOUT_RVOL 1.5, relative_volume tail(20) on the pre/post frame) #####')
rows = []
for tk in TK:
    i5 = H[tk]['i5']; vol = i5['volume'].astype(float)
    rv = vol / vol.rolling(20).mean()          # == relative_volume on the as-of frame
    r = R[tk].copy(); r['rvol'] = rv.reindex(r.index)
    piv = r.pivot_table(index='day', columns='t', values='volume')
    tod = piv / piv.shift(1).rolling(10, min_periods=5).mean()   # same clock bar, prior 10 sessions
    r['rvol_tod'] = [tod.at[d, t] if t in tod.columns else np.nan for d, t in zip(r['day'], r['t'])]
    rows.append(r.assign(ticker=tk))
A = pd.concat(rows)
pmv = pd.concat([H[t]['i5'].between_time('04:00', '09:25')['volume'] for t in TK])
print('share of pre-market bars with volume == 0:', round(float((pmv == 0).mean()), 3))
w = A[(A.t >= '09:45') & (A.t <= '11:30')]
g = w.groupby('t').agg(pass_current=('rvol', lambda x: (x > 1.5).mean()),
                       pass_tod=('rvol_tod', lambda x: (x > 1.5).mean()), n=('rvol', 'size')).round(2)
print(g.T.to_string())

print('\n##### B. Gap measured from the pre-market open (current) vs the 09:30 open #####')
rows = []
for tk in TK:
    i5 = H[tk]['i5']; idx = i5.index.tz_localize(None).normalize()
    for d in H[tk]['days']:
        pc = H[tk]['prev_close'].get(d); b = i5[idx == d]; rg = b.between_time('09:30', '15:55')
        pm = b.between_time('04:00', '09:25')
        if not pc or rg.empty: continue
        rows.append(dict(ticker=tk, day=d, gap_pm=b['open'].iloc[0] / pc - 1, gap_reg=rg['open'].iloc[0] / pc - 1,
                         pm_low=pm['low'].min() if len(pm) else np.nan, reg_low=rg['low'].min(),
                         pm_high=pm['high'].max() if len(pm) else np.nan, reg_high=rg['high'].max()))
G = pd.DataFrame(rows)
print('ticker-days', len(G), '| gap_up>=2% by pre-market open:', int((G.gap_pm >= .02).sum()), '| by 09:30 open:', int((G.gap_reg >= .02).sum()),
      '| both:', int(((G.gap_pm >= .02) & (G.gap_reg >= .02)).sum()), '| pm only:', int(((G.gap_pm >= .02) & (G.gap_reg < .02)).sum()),
      '| 09:30 only:', int(((G.gap_pm < .02) & (G.gap_reg >= .02)).sum()))
print('median |gap_pm - gap_reg| (pp):', round(float((G.gap_pm - G.gap_reg).abs().median() * 100), 2),
      ' p90:', round(float((G.gap_pm - G.gap_reg).abs().quantile(.9) * 100), 2))
print('knife guard (gap <= -2%): pm', int((G.gap_pm <= -.02).sum()), ' 09:30', int((G.gap_reg <= -.02).sum()),
      ' pm-only', int(((G.gap_pm <= -.02) & (G.gap_reg > -.02)).sum()), ' 09:30-only', int(((G.gap_pm > -.02) & (G.gap_reg <= -.02)).sum()))
print('day-card session LOW is a pre-market low on', round(float((G.pm_low < G.reg_low).mean() * 100)),
      '% of ticker-days; session HIGH is pre-market on', round(float((G.pm_high > G.reg_high).mean() * 100)), '%')

T = pd.read_pickle(S + '/trades.pkl')
T['day'] = pd.to_datetime(T['day'])


def path(tr):
    r = R[tr.ticker]; d = r[r.day == tr.day]; return d[d.t > tr['in']], d[d.t <= tr['in']], d


print('\n##### C. ORB geometry: how far do entries run? (bot-gap-fixed, 59 sessions) #####')
rows = []
for _, tr in T[T.trader == 'bot-gap-fixed'].iterrows():
    after, before, d = path(tr)
    orl = d[d.t < '09:45']['low'].min() * 0.999; risk = tr.entry - orl
    if risk <= 0 or after.empty: continue
    hit = after['low'] <= orl; k = hit.values.argmax() if hit.any() else len(after)
    mfe_live = (after['high'].iloc[:max(k, 1)].max() - tr.entry) / risk
    rows.append(dict(ticker=tr.ticker, day=tr.day.date(), t=tr['in'], risk_pct=risk / tr.entry * 100, r=tr.r, mfe_before_stop=mfe_live))
C = pd.DataFrame(rows)
print(C.describe().round(2).loc[['count', 'mean', '25%', '50%', '75%', 'max'], ['risk_pct', 'r', 'mfe_before_stop']].to_string())
for x in (0.5, 1.0, 1.5, 2.0):
    print(f'  reached +{x}R before the stop: {int((C.mfe_before_stop >= x).sum())}/{len(C)}')
p = T[T.trader.isin(['bot-gap-fixed', 'bot-gap-mgd'])].pivot_table(index=['ticker', 'day'], columns='trader', values='r')
dlt = (p['bot-gap-mgd'] - p['bot-gap-fixed']).dropna()
print(f'managed - fixed, paired: n={len(dlt)} mean {dlt.mean():+.3f}R  median {dlt.median():+.3f}  '
      f't={dlt.mean() / (dlt.std(ddof=1) / np.sqrt(len(dlt))):.2f}  managed better on {int((dlt > 0).sum())}, worse on {int((dlt < 0).sum())}')
print('entry clock of ORB fires:', T[T.trader == 'bot-gap-fixed']['in'].value_counts().sort_index().to_dict())

print('\n##### D. Reversal bots: risk per share and cost sensitivity (the book charges fixed bots NO spread) #####')
rows = []
for _, tr in T[T.trader.str.startswith('rev-')].iterrows():
    if tr.why == 'stop_hit': risk = tr.entry - tr.exit
    elif tr.r: risk = (tr.exit - tr.entry) / tr.r
    else: continue
    rows.append(dict(trader=tr.trader, ticker=tr.ticker, day=tr.day, t=tr['in'], entry=tr.entry, risk=risk,
                     risk_pct=risk / tr.entry * 100, r=tr.r, why=tr.why))
D = pd.DataFrame(rows)
print('risk % of price: median', round(D.risk_pct.median(), 2), ' p25', round(D.risk_pct.quantile(.25), 2), ' p75', round(D.risk_pct.quantile(.75), 2),
      '| risk $/share: median', round(D.risk.median(), 3), ' min', round(D.risk.min(), 3))
costs = {'book as is (no cost)': D.r * 0, '$0.02/share round trip (harness cost)': 0.02 / D.risk,
         '5 bps of price round trip': 0.0005 * D.entry / D.risk, '10 bps of price round trip': 0.001 * D.entry / D.risk}
for label, c in costs.items():
    net = D.r - c
    print(f'  {label:40s} ' + '  '.join(f'{k}: {v:+.1f}R' for k, v in net.groupby(D.trader).sum().items()) + f'   | avg cost {c.mean():.2f}R/trade')
D['price'] = np.where(D.entry < 60, 'under $60', '$60+')
print(D.assign(c2=0.02 / D.risk).groupby('price').agg(n=('r', 'size'), gross_R=('r', 'sum'), cost_2c_R=('c2', 'sum'), med_risk_usd=('risk', 'median')).round(2).to_string())

print('\n##### E. Stop-run rate: after a stop-out, did price come back? (crowded-stop proxy) #####')
rows = []
for _, tr in T[T.why.isin(['stop_hit'])].iterrows():
    if tr.trader == 'bot-gap-mgd': continue
    after, before, d = path(tr); stop = tr.exit
    hit = after['low'] <= stop
    if not hit.any(): continue
    k = hit.values.argmax(); post = after.iloc[k:]; risk = tr.entry - stop
    rows.append(dict(trader=tr.trader, overshoot_R=(stop - post['low'].iloc[:7].min()) / risk,
                     back_above_entry_6bars=bool((post['close'].iloc[:7] > tr.entry).any()),
                     back_above_entry_eod=bool((post['close'] > tr.entry).any()),
                     reached_1R_after=bool((post['high'] >= tr.entry + risk).any()),
                     reached_3pct_after=bool((post['high'] >= tr.entry * 1.03).any())))
E = pd.DataFrame(rows)
print(E.groupby('trader').agg(stops=('overshoot_R', 'size'), med_overshoot_R=('overshoot_R', 'median'),
                              back_6bars=('back_above_entry_6bars', 'mean'), back_eod=('back_above_entry_eod', 'mean'),
                              later_1R=('reached_1R_after', 'mean'), later_3pct=('reached_3pct_after', 'mean')).round(2).to_string())

print('\n##### F. Late-day flow: 09:30->15:00 vs 15:00->close (leveraged-ETF rebalance predicts continuation; the skill assumes a 15:00 reversal) #####')
rows = []
for tk in TK:
    r = R[tk]
    for d, g in r.groupby('day'):
        if len(g) < 70: continue
        o = g['open'].iloc[0]; pre = g[g.t < '15:00']; p15 = pre['close'].iloc[-1]; c = g['close'].iloc[-1]
        p1530 = g[g.t < '15:30']['close'].iloc[-1]
        vw = ((g.high + g.low + g.close) / 3 * g.volume).cumsum() / g.volume.cumsum()
        rows.append(dict(ticker=tk, day=d, r_day=p15 / o - 1, r_last=c / p15 - 1, r_1500_1530=p1530 / p15 - 1,
                         r_last30=c / p1530 - 1, below_vwap=bool(p15 < vw[g.t < '15:00'].iloc[-1])))
F = pd.DataFrame(rows); und = F[~F.ticker.isin(['SNXX', 'SNDQ', 'NBIZ'])]


def clus(df, col):
    by = df.groupby('day')[col].mean(); return by.mean() * 100, by.mean() / (by.std(ddof=1) / np.sqrt(len(by))), len(by)


for name, sub in (('all 9 underlyings', und), ('SNDK+NBIS+AMD+INTC (have 2x ETFs)', und[und.ticker.isin(['SNDK', 'NBIS', 'AMD', 'INTC'])])):
    print(name, '| spearman(r_day, r_last hour) =', round(sub[['r_day', 'r_last']].corr(method='spearman').iloc[0, 1], 3), ' n', len(sub))
    for lab, s in (('down <= -3% at 15:00', sub[sub.r_day <= -.03]), ('up >= +3% at 15:00', sub[sub.r_day >= .03]),
                   ('weak (<-1%) & below VWAP at 15:00', sub[(sub.r_day < -.01) & sub.below_vwap])):
        if len(s) < 5: continue
        m1, t1, n1 = clus(s, 'r_1500_1530'); m2, t2, _ = clus(s, 'r_last30'); m3, t3, _ = clus(s, 'r_last')
        print(f'   {lab:36s} n={len(s):3d} dates={n1:2d} | 15:00-15:30 {m1:+.2f}% (t {t1:+.1f}) | 15:30-close {m2:+.2f}% (t {t2:+.1f}) | 15:00-close {m3:+.2f}% (t {t3:+.1f})')

print('\n##### G. Do the peers agree at entry? (pre-stated: with-peers > against-peers) #####')
stocks = ['AMD', 'INTC', 'MU', 'NVDA', 'PLTR', 'SMCI', 'TSLA', 'SNDK', 'NBIS']
ret = {}
for tk in stocks:
    r = R[tk]; o = r.groupby('day')['open'].transform('first'); ret[tk] = (r['close'] / o - 1).rename(tk)
B = pd.concat(ret.values(), axis=1)
B['day'] = B.index.tz_localize(None).normalize(); B['t'] = B.index.strftime('%H:%M'); B = B.set_index(['day', 't'])
INV = {'SNDQ': 'SNDK', 'NBIZ': 'NBIS'}; LONG = {'SNXX': 'SNDK'}
rows = []
for _, tr in T[T.trader != 'bot-gap-mgd'].iterrows():
    if (tr.day, tr['in']) not in B.index: continue
    row = B.loc[(tr.day, tr['in'])]; und_t = INV.get(tr.ticker) or LONG.get(tr.ticker) or tr.ticker
    peers = row.drop(labels=[und_t], errors='ignore').dropna(); sign = -1 if tr.ticker in INV else 1
    rows.append(dict(trader=tr.trader, r=tr.r, day=tr.day, peers=sign * peers.median(), own=sign * row.get(und_t, np.nan)))
Gm = pd.DataFrame(rows)
Gm['with_peers'] = np.where(Gm.peers > 0, 'agree', 'against'); Gm['own_dir'] = np.where(Gm.own > 0, 'above open', 'below open')


def st(g):
    by = g.groupby('day').r.sum()
    return pd.Series(dict(n=len(g), days=len(by), win=round(100 * (g.r > 0).mean()), avgR=round(g.r.mean(), 2), netR=round(g.r.sum(), 1)))


print(Gm.groupby(['trader', 'with_peers']).apply(st).unstack().to_string())
print(Gm.groupby(['trader', 'own_dir']).apply(st).unstack().to_string())
w30 = sorted(T.day.unique())[-30:]
print('last 30 sessions only:'); print(Gm[Gm.day.isin(w30)].groupby(['trader', 'with_peers']).apply(st).unstack().to_string())
