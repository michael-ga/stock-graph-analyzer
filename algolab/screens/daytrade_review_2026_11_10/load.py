import json, glob, os, collections, sys
import pandas as pd
sys.path.insert(0, 'scripts')
def load(fp):
    rows = []; days=set(); rep = collections.Counter()
    for f in glob.glob(f'.local/replay-ab/cache/{fp}-*.json'):
        tk = os.path.basename(f).split('-')[1]
        d = json.load(open(f))
        for day, row in d.items():
            days.add(day); rep[tk] += bool(row.get('replayed'))
            for t in row['trades']:
                rows.append({'ticker': tk, 'day': day, **t})
    return pd.DataFrame(rows), sorted(days), rep
if __name__ == '__main__':
    from pathlib import Path
    import algo_replay_ab as ab
    print('current tree fp', ab.tree_fingerprint(Path('.'))[:16])
    for fp in ['e2ebbcf9def23543','7baef32d8499cf00','39e5a9d501c2d2b7','a7e22d259abeb087']:
        df, days, rep = load(fp)
        print(fp, len(df), days[0], days[-1], len(days), dict(rep))
        print(df.groupby('trader').agg(n=('r','size'), net=('r','sum')).round(2).T.to_string())
