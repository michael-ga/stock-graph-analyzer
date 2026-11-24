"""Backtest of the live-trade-watch scanner rules (stockanalyzer/setups/vwap_rules.py, the code
watch_setups.py runs, + common.plan + track_position.py)
on the cached 5-min history (.local/replay-ab/hist). Each underlying is treated as having a +2x and a -2x ETF
(daily-reset mapping from the previous close, as common.mapped does). $300 stake, $3 round-trip fees.

Exit variants:
  plan     stop / target / time stop only (the orders in the ACTION block)
  +be      plan, plus the tracker's +1R -> breakeven+fees and "gave back half" exits
  +vwap    +be, plus the tracker's "underlying 5-min close crossed VWAP against you: sell at the bid"
"""
import sys, pickle, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
from stockanalyzer.setups import vwap_rules as VR
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)

FEE, MIN_STOP, RR_MIN, STAKE, LEV = 3.0, 0.04, 1.5, 300.0, 2.0
UND = ['AMD', 'INTC', 'MU', 'NVDA', 'PLTR', 'SMCI', 'TSLA', 'SNDK', 'NBIS']
H = {tk: pickle.load(open(f'.local/replay-ab/hist/2026-11-01/{tk}.pkl', 'rb')) for tk in UND}


def mins(t): return int(t[:2]) * 60 + int(t[3:])


def etf(lev, und_prev, u):                       # ETF price, previous ETF close normalised to 100
    return 100.0 * (1 + lev * (u / und_prev - 1))


def plan(entry, stop, target):
    if (entry - stop) / entry < MIN_STOP:
        stop = entry * (1 - MIN_STOP)
    sh = STAKE / entry
    rr = (sh * (target - entry) - FEE) / (sh * (entry - stop) + FEE)
    return stop, rr


def simulate(d, i, lev, pc, entry, stop, target, exit_t, vwap, variant):
    """Walk bars i.. of day frame d. Returns (etf_exit_price, reason, exit clock, mfe, mae)."""
    risk = entry - stop; peak = entry; be_done = False; cur_stop = stop
    sh = STAKE / entry; be_stop = entry + FEE / sh
    mfe = mae = 0.0
    for k in range(i, len(d)):
        b = d.iloc[k]; t = d.index[k].strftime('%H:%M')
        if mins(t) >= mins(exit_t):
            return etf(lev, pc, b.open), 'time', t, mfe, mae
        hi_u, lo_u = (b.high, b.low) if lev > 0 else (b.low, b.high)      # underlying price at the ETF's high / low
        e_hi, e_lo, e_cl = etf(lev, pc, hi_u), etf(lev, pc, lo_u), etf(lev, pc, b.close)
        mae = min(mae, e_lo / entry - 1)
        if e_lo <= cur_stop:
            return cur_stop, ('breakeven' if be_done else 'stop'), t, mfe, mae
        mfe = max(mfe, e_hi / entry - 1)
        if e_hi >= target:
            return target, 'target', t, mfe, mae
        peak = max(peak, e_hi)
        if variant != 'plan':
            if not be_done and e_hi >= entry + risk:
                be_done = True; cur_stop = max(cur_stop, be_stop)
            if peak >= entry + 1.5 * risk and e_cl <= entry + 0.5 * (peak - entry):
                return e_cl, 'giveback', t, mfe, mae
        if variant == '+vwap' and k > i:          # tracker needs one completed bar after it starts
            against = b.close > vwap.iloc[k] if lev < 0 else b.close < vwap.iloc[k]
            if against:
                return e_cl, 'vwap', t, mfe, mae
    return etf(lev, pc, d.close.iloc[-1]), 'eod', d.index[-1].strftime('%H:%M'), mfe, mae


def scan_day(tk, d, pc, variant, short_exit='14:55', long_exit='15:50'):
    """The watcher's rules, called from stockanalyzer.setups.vwap_rules (the code the live
    watcher runs), on each completed bar; the entry is the next bar's open."""
    out = []
    vwap = VR.session_vwap(d)
    below = False; reclaimed = False; busy_until = -1; fb_last = None
    sent = set()
    for i in range(1, len(d) - 1):                # bar i just completed; bar i+1 is forming
        b = d.iloc[i]; t_now = d.index[i + 1].strftime('%H:%M')
        px = float(d.open.iloc[i + 1]); vw = float(vwap.iloc[i]); lod = float(min(d.low.iloc[:i + 1].min(), px))
        cands = []
        sig, iH, _ = VR.oversold_bounce(d.high.values[:i + 1], d.low.values[:i + 1], d.close.values[:i + 1], vw,
                                        vix_eased=False, late=mins(t_now) >= 15 * 60)
        if iH is not None and ('b', iH) not in sent:
            sent.add(('b', iH))
            if sig is not None:
                cands.append(sig)
        tp = VR.trend_pullback(d.iloc[:i + 1], vw, lod)
        if tp is not None:
            cands.append(tp)
        fb = VR.failed_bounce(b, vw, lod)
        if fb is not None and (fb_last is None or i - fb_last >= 6):      # the watcher's 30-min cooldown
            fb_last = i
            cands.append(fb)
        rc = VR.vwap_reclaim(float(b.close), vw, below, reclaimed, lod)
        if rc is not None:
            reclaimed = True
            cands.append(rc)
        below = below or b.close < vw
        if b.close < vw * 0.995:
            reclaimed = False
        if i + 1 <= busy_until:
            continue
        for sg in cands:
            lev = -LEV if sg.side == 'short' else LEV
            exit_t = short_exit if sg.side == 'short' else long_exit
            name = f"{sg.rule}-{sg.side}"
            entry = etf(lev, pc, px)
            stop_raw = etf(lev, pc, sg.stop_und) if sg.stop_und else entry * (1 - MIN_STOP)
            target = etf(lev, pc, sg.target_und)
            stop, rr = plan(entry, min(stop_raw, entry * (1 - MIN_STOP)), target)
            late = mins(exit_t) - mins(t_now) < 45
            row = dict(ticker=tk, day=d.index[0].tz_localize(None).normalize(), t=t_now, setup=name, rr=round(rr, 2),
                       stop_pct=(entry - stop) / entry * 100, tgt_pct=(target - entry) / entry * 100)
            if late or rr < RR_MIN:
                out.append({**row, 'taken': False, 'why_skip': 'late' if late else 'rr'})
                continue
            x, reason, t_out, mfe, mae = simulate(d, i + 1, lev, pc, entry, stop, target, exit_t, vwap, variant)
            ret = x / entry - 1
            out.append({**row, 'taken': True, 'why_skip': '', 'exit': reason, 't_out': t_out, 'etf_ret_pct': ret * 100,
                        'usd': STAKE * ret - FEE, 'mfe_pct': mfe * 100, 'mae_pct': mae * 100})
            busy_until = next((k for k in range(i + 1, len(d)) if d.index[k].strftime('%H:%M') >= t_out), len(d))
            break
    return out


def run(variant):
    rows = []
    for tk in UND:
        i5 = H[tk]['i5']; rg = i5.between_time('09:30', '15:55'); days = rg.index.tz_localize(None).normalize()
        for day in H[tk]['days']:
            d = rg[days == day]; pc = H[tk]['prev_close'].get(day)
            if len(d) < 70 or not pc: continue
            rows += scan_day(tk, d, float(pc), variant)
    return pd.DataFrame(rows)


def stat(g):
    by = g.groupby('day').usd.sum()
    t = by.mean() / (by.std(ddof=1) / np.sqrt(len(by))) if len(by) > 2 and by.std(ddof=1) > 0 else np.nan
    return pd.Series({'n': len(g), 'days': len(by), 'win%': round(100 * (g.usd > 0).mean()), 'avg_etf%': round(g.etf_ret_pct.mean(), 2),
                      'avg_$': round(g.usd.mean(), 2), 'total_$': round(g.usd.sum(), 0), 'no_best_day_$': round(g[g.day != by.idxmax()].usd.mean(), 2) if len(by) > 1 else np.nan,
                      'day_t': round(t, 2), 'avg_stop%': round(g.stop_pct.mean(), 1), 'avg_tgt%': round(g.tgt_pct.mean(), 1)})


if __name__ == '__main__':
    S = sys.argv[1]
    res = {v: run(v) for v in ('plan', '+be', '+vwap')}
    base = res['plan']
    all_days = sorted(set().union(*[set(H[t]['days']) for t in UND])); w30 = all_days[-30:]
    print('sessions', all_days[0].date(), '->', all_days[-1].date(), len(all_days), '| last 30 from', w30[0].date())
    print('\n=== signal funnel (all scanner candidates, 59 sessions x 9 underlyings) ===')
    f = base.groupby('setup').agg(candidates=('taken', 'size'), taken=('taken', 'sum'),
                                  skip_rr=('why_skip', lambda x: (x == 'rr').sum()), skip_late=('why_skip', lambda x: (x == 'late').sum()),
                                  median_rr=('rr', 'median'))
    print(f.to_string())
    for v, df in res.items():
        tk = df[df.taken]
        print(f'\n=== exits: {v} | ALL 59 sessions ==='); print(tk.groupby('setup').apply(stat).to_string())
        print(f'--- {v} | LAST 30 sessions ---'); print(tk[tk.day.isin(w30)].groupby('setup').apply(stat).to_string())
        print(f'--- {v} | PRIOR 29 sessions ---'); print(tk[~tk.day.isin(w30)].groupby('setup').apply(stat).to_string())
        print('exit reasons:'); print(tk.pivot_table(index='setup', columns='exit', values='usd', aggfunc=['size', 'mean']).round(1).fillna('').to_string())
    tk = res['+vwap'][res['+vwap'].taken]
    tk['hour'] = tk.t.str[:2]
    print('\n=== +vwap (full tracker) by entry hour, all 59 ==='); print(tk.pivot_table(index='setup', columns='hour', values='usd', aggfunc=['size', 'sum']).round(0).fillna('').to_string())
    print('\n=== gross edge before the $3 fee (avg ETF % per trade), plan exits ===')
    tkp = base[base.taken]; print(tkp.groupby('setup').agg(n=('usd', 'size'), avg_etf_pct=('etf_ret_pct', 'mean'), med_mfe=('mfe_pct', 'median'), med_mae=('mae_pct', 'median')).round(2).to_string())
    print('\n=== what the R:R gate skipped: would those have made money? (plan exits, no gate) ===')
    for v in res: res[v].to_pickle(f'{S}/skill_{v.strip("+")}.pkl')
