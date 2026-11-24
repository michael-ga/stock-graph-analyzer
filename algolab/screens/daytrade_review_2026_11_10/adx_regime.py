"""Murphy ch.15 regime check (pre-stated): fades should do better when the 5-min ADX is low (range),
momentum entries when it is high (trend). ADX(14) on the regular-session + prior-day 5-min bars at the entry bar."""
import sys, pickle, glob, os, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
from stockanalyzer.analysis.indicators import adx
S = sys.argv[1]
pd.set_option('display.width', 250)
H = {os.path.basename(f)[:-4]: pickle.load(open(f, 'rb')) for f in glob.glob('.local/replay-ab/hist/2026-11-01/*.pkl')}
AX = {}
for tk, h in H.items():
    r = h['i5'].between_time('09:30', '15:55'); a = adx(r.high, r.low, r.close, 14)
    a['day'] = r.index.tz_localize(None).normalize(); a['t'] = r.index.strftime('%H:%M'); AX[tk] = a.set_index(['day', 't'])
hz = pd.read_pickle(S + '/harness.pkl'); hz['r'] = hz.net_r; hz = hz[['ticker', 'day', 't', 'variant', 'r']]
T = pd.read_pickle(S + '/trades.pkl'); T['day'] = pd.to_datetime(T['day'])
b = T[T.family.isin(['orb', 'rev']) & (T.trader != 'bot-gap-mgd')].rename(columns={'trader': 'variant', 'in': 't'})[['ticker', 'day', 't', 'variant', 'r']]
A = pd.concat([hz, b])


def prev_bar(tk, day, t):                      # ADX known when the entry bar OPENS = the previous bar's value
    a = AX[tk]
    try:
        loc = a.index.get_loc((day, t)); return a.iloc[loc - 1]
    except Exception:
        return pd.Series({'adx': np.nan, 'plus_di': np.nan, 'minus_di': np.nan})


x = [prev_bar(tk, d, t) for tk, d, t in zip(A.ticker, A.day, A.t)]
A['adx'] = [v['adx'] for v in x]; A['up'] = [v['plus_di'] > v['minus_di'] for v in x]
A['regime'] = pd.cut(A.adx, [0, 20, 30, 100], labels=['chop <20', 'mid 20-30', 'trend >=30'])


def st(g):
    by = g.groupby('day').r.sum(); t = by.mean() / (by.std(ddof=1) / np.sqrt(len(by))) if len(by) > 2 and by.std(ddof=1) > 0 else np.nan
    return pd.Series({'n': len(g), 'days': len(by), 'win%': round(100 * (g.r > 0).mean()), 'avgR': round(g.r.mean(), 2), 'netR': round(g.r.sum(), 1), 'day_t': round(t, 2)})


print(A.groupby(['variant', 'regime'], observed=True).apply(st).to_string())
print('\n+DI over -DI at entry (5-min trend up) vs down:')
print(A.groupby(['variant', 'up']).apply(st).to_string())
w30 = sorted(A.day.unique())[-30:]
print('\nsplit halves for the fade (xfade) by regime:')
A['half'] = np.where(A.day.isin(w30), 'last30', 'prior29')
print(A[A.variant == 'xfade-dbl-bottom'].groupby(['half', 'regime'], observed=True).apply(st).to_string())
