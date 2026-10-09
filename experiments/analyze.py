#!/usr/bin/env python3
"""Regenerates the summary tables in FINDINGS.md from data/*.csv.
Usage: python3 experiments/analyze.py phase2|phase3|phase4|phase5"""
import sys, os, numpy as np, pandas as pd
D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
def ms(x): x = pd.Series(x).dropna(); return f'{x.mean():.2f} ± {x.std():.2f}' if len(x) > 1 else f'{x.mean():.2f}'
def table(df, by, cols, fmt=ms):
    g = df.groupby(by, sort=False)
    lines = ['| ' + ' | '.join(([by] if isinstance(by, str) else by) + cols) + ' |', '|' + '---|' * (len(cols) + (1 if isinstance(by, str) else len(by)))]
    for k, x in g:
        k = k if isinstance(k, tuple) else (k,)
        lines.append('| ' + ' | '.join(map(str, k)) + ' | ' + ' | '.join(fmt(x[c]) for c in cols) + ' |')
    return '\n'.join(lines)

def phase2():
    d = pd.read_csv(f'{D}/phase2_interval.csv'); print('### interval (ms)\n' + table(d, ['counter', 'load'], ['median_ms', 'p5_ms', 'p95_ms', 'max_ms'], lambda x: f'{x.mean():.4f} (sd {x.std():.4f})'))
    d = pd.read_csv(f'{D}/phase2_smt.csv'); print('\n### SMT\n' + table(d, 'loaded', ['cpu2_W', 'cpu34_W', 'cpu3_W']))
    d = pd.read_csv(f'{D}/phase2_crosscheck.csv'); print('\n### crosscheck\n' + table(d.assign(x='8 burn'), 'x', list(d.columns[1:])))
    if os.path.exists(f'{D}/phase2_neighbors.csv'):
        d = pd.read_csv(f'{D}/phase2_neighbors.csv'); print('\n### neighbors\n' + table(d, 'neighbors', ['core0_W', 'core0_mhz', 'core0_iters_s', 'mean_nbr_W', 'pkg_W']))
    if os.path.exists(f'{D}/phase2_bmcrate.csv'):
        d = pd.read_csv(f'{D}/phase2_bmcrate.csv'); s = d[d.kind == 'sample']
        for rep, g in s.groupby('rep'):
            ch = g[g.watts.diff() != 0].t.values[1:]; gaps = np.diff(ch)
            print(f'bmcrate rep {rep}: samples {len(g)}, value changes {len(ch)}, median gap between changes {np.median(gaps):.3f}s, p10 {np.percentile(gaps,10):.3f} p90 {np.percentile(gaps,90):.3f}')
    if os.path.exists(f'{D}/phase2_sumpkg.csv'):
        d = pd.read_csv(f'{D}/phase2_sumpkg.csv'); d['cfg'] = d.workload + d.n.astype(str)
        d['core_frac_of_pkg'] = d.sumcore_W / d.pkg_W
        print('\n### sum-of-cores vs package\n' + table(d.sort_values(['workload', 'n']), 'cfg', ['pkg_W', 'sumcore_W', 'uncore_W', 'sum64_W', 'hsmp_W', 'bmc_W', 'ddr_GBs', 'busy_mhz']))
        # slopes: uncore vs ddr bandwidth, bmc-pkg vs ddr
        for name, sub in (('all', d), ('mem only', d[d.workload != 'burn'])):
            for y in ('uncore_W', 'bmc_W'):
                A = np.c_[np.ones(len(sub)), sub.ddr_GBs]; c, *_ = np.linalg.lstsq(A, sub[y], rcond=None)
                print(f'fit {name}: {y} = {c[0]:.2f} + {c[1]:.3f} * ddr_GBs')
            sub2 = sub.assign(nonpkg=sub.bmc_W - sub.pkg_W)
            A = np.c_[np.ones(len(sub2)), sub2.ddr_GBs]; c, *_ = np.linalg.lstsq(A, sub2.nonpkg, rcond=None)
            print(f'fit {name}: (bmc - pkg) = {c[0]:.2f} + {c[1]:.3f} * ddr_GBs')

if __name__ == '__main__': globals()[sys.argv[1]]()
