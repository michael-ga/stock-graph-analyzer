"""Account view: what each bot / setup earns per trade in % of the stake, and what that leaves after the
user's real cost ($3 round trip). Setups are re-run through the project's own harness on the cached bars
(no network) to get each trade's risk %; bots use the replay book's pnl_pct."""
import sys, pickle, glob, os, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
from stockanalyzer.setups import SETUPS, harness
from stockanalyzer.data.schema import Timeframe
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
S = sys.argv[1]
H = {}
for f in glob.glob('.local/replay-ab/hist/2026-11-01/*.pkl'):
    h = pickle.load(open(f, 'rb')); d = h['daily'][Timeframe.Y1].copy()
    if d.index.tz is not None: d.index = d.index.tz_localize(None)
    H[os.path.basename(f)[:-4]] = {'i5': h['i5'], 'daily': d, 'days': h['days']}
variants = [(m, cfg) for m in SETUPS.values() for cfg in m.VARIANTS]
tr = harness.run(H, variants, days=59, offset=0)
tr['pct'] = tr.net_r * tr.risk_pct; tr['gross_pct'] = tr.gross_r * tr.risk_pct
tr.to_pickle(S + '/harness.pkl')
print('harness rerun on cache:', tr.groupby('variant').agg(n=('net_r', 'size'), net=('net_r', 'sum')).round(2).to_dict('index'))
T = pd.read_pickle(S + '/trades.pkl'); T['day'] = pd.to_datetime(T['day'])
bots = T[T.family.isin(['orb', 'rev'])].rename(columns={'trader': 'variant'})[['ticker', 'day', 'variant', 'r', 'pnl_pct']]
bots = bots.rename(columns={'pnl_pct': 'gross_pct'}); bots['risk_pct'] = np.where(bots.r != 0, (bots.gross_pct / bots.r).abs(), np.nan)
allt = pd.concat([bots, tr[['ticker', 'day', 'variant', 'gross_r', 'gross_pct', 'risk_pct']].rename(columns={'gross_r': 'r'})])
ETF = {'SNXX', 'SNDQ', 'NBIZ'}; allt['kind'] = np.where(allt.ticker.isin(ETF), '2xETF', 'stock')
days = sorted(allt.day.unique()); w30 = days[-30:]


def view(g):
    m = g.gross_pct.mean()
    return pd.Series({'n': len(g), 'days': g.day.nunique(), 'med_risk%': round(g.risk_pct.median(), 2), 'avg_gross_R': round(g.r.mean(), 2),
                      'avg_gross_%': round(m, 3), '$300 net/trade': round(3 * m - 3, 2), '$500 net/trade': round(5 * m - 3, 2),
                      '$1000 net/trade': round(10 * m - 3, 2), 'breakeven stake $': (round(300 / m) if m > 0 else np.inf),
                      'fee as R at $300': round(1.0 / g.risk_pct.median(), 2)})


print('\n=== ACCOUNT VIEW, all 59 sessions (gross % of stake per trade; $3 round trip) ===')
print(allt.groupby('variant').apply(view).to_string())
print('\n=== by instrument type ==='); print(allt.groupby(['variant', 'kind']).apply(view).to_string())
print('\n=== LAST 30 sessions ==='); print(allt[allt.day.isin(w30)].groupby('variant').apply(view).to_string())

# H8's pre-registered next variant: xfade only when the daily bias is up (close > 20-day mean) -- exploratory read
from stockanalyzer.setups.base import daily_bias_up
x = tr[tr.variant == 'xfade-dbl-bottom'].copy()
x['bias_up'] = [bool(daily_bias_up(H[t]['daily'][H[t]['daily'].index < d])) for t, d in zip(x.ticker, x.day)]
x['kind'] = np.where(x.ticker.isin(ETF), '2xETF', 'stock')


def st(g):
    by = g.groupby('day').net_r.sum(); t = by.mean() / (by.std(ddof=1) / np.sqrt(len(by))) if len(by) > 2 else np.nan
    return pd.Series({'n': len(g), 'days': len(by), 'win%': round(100 * (g.net_r > 0).mean()), 'avg_net_R': round(g.net_r.mean(), 2), 'day_t': round(t, 2)})


print('\n=== xfade-dbl-bottom by daily bias (H8 next variant; EXPLORATORY) ===')
print(x.groupby('bias_up').apply(st).to_string()); print(x.groupby(['kind', 'bias_up']).apply(st).to_string())
x['half'] = np.where(x.day.isin(w30), 'last30', 'prior29'); print(x.groupby(['half', 'bias_up']).apply(st).to_string())
s = tr[tr.variant == 'sb-am-bias'].copy(); s['half'] = np.where(s.day.isin(w30), 'last30', 'prior29')
print('\n=== sb-am-bias exits ==='); print(s.groupby(['half', 'status']).agg(n=('net_r', 'size'), avg=('net_r', 'mean'), rr=('rr', 'median'), risk=('risk_pct', 'median')).round(2).to_string())
print('\nstop distance vs noise: median trade risk % / median 5-min bar range % of the same ticker')
rng = {}
for tk, h in H.items():
    r = h['i5'].between_time('09:30', '15:55'); rng[tk] = float(((r.high - r.low) / r.close).median() * 100)
allt['bar_rng'] = allt.ticker.map(rng); allt['stop_in_bars'] = allt.risk_pct / allt.bar_rng
print(allt.groupby('variant').agg(med_risk_pct=('risk_pct', 'median'), med_bar_range_pct=('bar_rng', 'median'), stop_in_bars=('stop_in_bars', 'median')).round(2).to_string())
print(pd.Series(rng).round(2).to_dict())
