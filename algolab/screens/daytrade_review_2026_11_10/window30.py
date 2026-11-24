import sys, numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1]); from load import load
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
df, days, rep = load('e2ebbcf9def23543')
df2, _, _ = load('a7e22d259abeb087')
# consistency on ETF tickers
etf = ['SNXX','SNDQ','NBIZ','NBIS']
a = df[df.ticker.isin(etf)].groupby('trader').r.agg(['size','sum']).round(2)
b = df2.groupby('trader').r.agg(['size','sum']).round(2)
print('12-ticker cache restricted to ETF list vs 4-ticker all-days cache\n', a.join(b, lsuffix='_full', rsuffix='_fast').to_string())
w30 = days[-30:]; w29 = days[:-30]
print('\nwindow30', w30[0], w30[-1], '| prior', w29[0], w29[-1], len(w29))
ETF = {'SNXX','SNDQ','NBIZ'}
df['kind'] = np.where(df.ticker.isin(ETF), '2xETF', 'stock')
df['win'] = df.r > 0
def stat(g):
    byday = g.groupby('day').r.sum()
    t = byday.mean() / (byday.std(ddof=1) / np.sqrt(len(byday))) if len(byday) > 2 and byday.std(ddof=1) > 0 else np.nan
    nb = g[g.day != byday.idxmax()].r
    return pd.Series({'n': len(g), 'days': g.day.nunique(), 'win%': round(100*g.win.mean()), 'avgR': round(g.r.mean(), 2),
                      'netR': round(g.r.sum(), 2), 'no_best_day': round(nb.mean(), 2) if len(nb) else np.nan,
                      'day_t': round(t, 2), 'avgW': round(g[g.win].r.mean(), 2) if g.win.any() else np.nan,
                      'avgL': round(g[~g.win].r.mean(), 2) if (~g.win).any() else np.nan})
for name, d in (('LAST 30 SESSIONS', df[df.day.isin(w30)]), ('PRIOR 29 SESSIONS', df[df.day.isin(w29)]), ('ALL 59', df)):
    print(f'\n=== {name} ===')
    print(d.groupby('trader').apply(stat).to_string())
d = df[df.day.isin(w30)]
print('\n=== LAST 30: by trader x kind ===')
print(d.groupby(['trader','kind']).apply(stat).to_string())
print('\n=== ALL 59: by trader x kind ===')
print(df.groupby(['trader','kind']).apply(stat).to_string())
print('\n=== LAST 30: by trader x ticker (n, netR) ===')
print(d.pivot_table(index='trader', columns='ticker', values='r', aggfunc=['size','sum']).round(1).fillna('').to_string())
df['hour'] = df['in'].str[:2] + ':' + np.where(df['in'].str[3:5].astype(int) < 30, '00', '30')
print('\n=== ALL 59: by trader x entry half-hour (n / netR) ===')
p = df.pivot_table(index='trader', columns='hour', values='r', aggfunc=['size','sum']).round(1).fillna('')
print(p.to_string())
print('\n=== LAST 30: by trader x entry half-hour ===')
print(d.assign(hour=df.hour).pivot_table(index='trader', columns='hour', values='r', aggfunc=['size','sum']).round(1).fillna('').to_string())
print('\n=== exit reasons, all 59 ===')
print(df.pivot_table(index='trader', columns='why', values='r', aggfunc=['size','mean']).round(2).fillna('').to_string())
df.to_pickle(sys.argv[1] + '/trades.pkl')
