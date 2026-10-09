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
            e = d[(d.kind == 'edge') & (d.rep == rep)].t.values; lo, hi = g.watts.quantile(0.1), g.watts.quantile(0.9)
            lags = [a.t.iloc[0] - t for t in e for a in [g[(g.t > t) & (g.t < t + 3) & (g.watts > (lo + hi) / 2)]] if len(a)]
            print(f'bmcrate rep {rep}: low {lo:.0f} W high {hi:.0f} W, rise lag to 50%: median {np.median(lags):.2f}s max {max(lags):.2f}s')
            print(f'bmcrate rep {rep}: samples {len(g)}, value changes {len(ch)}, median gap between changes {np.median(gaps):.3f}s, p10 {np.percentile(gaps,10):.3f} p90 {np.percentile(gaps,90):.3f}')
    if os.path.exists(f'{D}/phase2_sumpkg.csv'):
        d = pd.read_csv(f'{D}/phase2_sumpkg.csv'); d['cfg'] = d.workload + d.n.astype(str)
        d['core_frac_of_pkg'] = d.sumcore_W / d.pkg_W; d['bmc_minus_pkg_W'] = d.bmc_W - d.pkg_W
        print('\n### sum-of-cores vs package\n' + table(d.sort_values(['workload', 'n']), 'cfg', ['pkg_W', 'sumcore_W', 'uncore_W', 'sum64_W', 'hsmp_W', 'bmc_W', 'bmc_minus_pkg_W', 'ddr_GBs', 'throughput']))
        # slopes: uncore vs ddr bandwidth, bmc-pkg vs ddr
        for name, sub in (('all', d), ('mem only', d[d.workload != 'burn'])):
            for y in ('uncore_W', 'bmc_W'):
                A = np.c_[np.ones(len(sub)), sub.ddr_GBs]; c, *_ = np.linalg.lstsq(A, sub[y], rcond=None)
                print(f'fit {name}: {y} = {c[0]:.2f} + {c[1]:.3f} * ddr_GBs')
            sub2 = sub.assign(nonpkg=sub.bmc_W - sub.pkg_W)
            A = np.c_[np.ones(len(sub2)), sub2.ddr_GBs]; c, *_ = np.linalg.lstsq(A, sub2.nonpkg, rcond=None)
            print(f'fit {name}: (bmc - pkg) = {c[0]:.2f} + {c[1]:.3f} * ddr_GBs')


def phase3():
    d = pd.read_csv(f'{D}/phase3_smt.csv')
    d['naive_over_counter'] = d.naive_sum_tasks_core2_J / d.core2_counter_J
    d['smtaware_over_counter'] = (d.smtaware_burn_J + d.smtaware_mem_J) / d.core2_counter_J
    d['alone_sum_over_shared'] = (d.ref_burn_alone_J + d.ref_mem_alone_J) / d.core2_counter_J
    print('### SMT pair (burn cpu2 + mem cpu34)\n' + table(d.assign(x='pair'), 'x', ['core2_counter_J', 'naive_burn_J', 'naive_mem_J', 'naive_over_counter', 'smtaware_burn_J', 'smtaware_mem_J', 'smtaware_over_counter', 'ref_burn_alone_J', 'ref_mem_alone_J', 'alone_sum_over_shared']))
    if os.path.exists(f'{D}/phase3_pingpong.csv'):
        p = pd.read_csv(f'{D}/phase3_pingpong.csv')
        ref = p[p.work_us == 20000]
        Pc = (ref.pp_cpu_naive_J / ref.pp_cpu_runtime_s).mean(); Pm = (ref.pp_mem_naive_J / ref.pp_mem_runtime_s).mean()
        print(f'\nreference power (20 ms turns): pp_cpu {Pc:.3f} W, pp_mem {Pm:.3f} W')
        p['truth_cpu_J'] = Pc * p.pp_cpu_runtime_s; p['truth_mem_J'] = Pm * p.pp_mem_runtime_s
        p['naive_cpu_err_pct'] = 100 * (p.pp_cpu_naive_J / p.truth_cpu_J - 1); p['naive_mem_err_pct'] = 100 * (p.pp_mem_naive_J / p.truth_mem_J - 1)
        p['naive_cpu_W'] = p.pp_cpu_naive_J / p.pp_cpu_runtime_s; p['naive_mem_W'] = p.pp_mem_naive_J / p.pp_mem_runtime_s
        p['mean_interval_us'] = 1e6 * p.pp_cpu_runtime_s / p.pp_cpu_n
        # time-proportional (equal-power) split of the busy energy
        busy = p.pp_cpu_naive_J + p.pp_mem_naive_J
        p['timesplit_cpu_err_pct'] = 100 * (busy * p.pp_cpu_runtime_s / (p.pp_cpu_runtime_s + p.pp_mem_runtime_s) / p.truth_cpu_J - 1)
        p['cpu_zero_frac'] = p.pp_cpu_zero_dE_frac
        p['ipc_cpu'] = p.pp_cpu_instr / p.pp_cpu_cycles; p['ipc_mem'] = p.pp_mem_instr / p.pp_mem_cycles
        print('\n### ping-pong on one CPU\n' + table(p, 'work_us', ['mean_interval_us', 'cpu_zero_frac', 'naive_cpu_W', 'naive_mem_W', 'naive_cpu_err_pct', 'naive_mem_err_pct', 'timesplit_cpu_err_pct', 'ipc_cpu', 'ipc_mem']))
    if os.path.exists(f'{D}/phase3_uncore.csv'):
        u = pd.read_csv(f'{D}/phase3_uncore.csv')
        # NOTE: true_uncore_W/pkg_W in the first run of phase3_uncore.csv were computed with an out-of-order bug
        # (see phase3.py); use the window-based phase 2 measurement at the same n as the reference instead.
        r2 = pd.read_csv(f'{D}/phase2_sumpkg.csv'); ref = r2[r2.workload != 'mem'].groupby('n').uncore_W.mean()
        u['ref_uncore_W_phase2'] = u.n.map(ref); u['overcount_x'] = u.efs_uncore_W / u.ref_uncore_W_phase2
        u = u[['rep', 'n', 'efs_uncore_W', 'ref_uncore_W_phase2', 'overcount_x', 'events']]
        print('\n### EFS-style uncore vs true pkg - sum(core)\n' + table(u, 'n', ['efs_uncore_W', 'ref_uncore_W_phase2', 'overcount_x', 'events']))


def phase5():
    d = pd.read_csv(f'{D}/phase5.csv')
    idle = d[d.workload == 'idle'].groupby('mhz')[['bmc_meanW', 'pkg_J', 'elapsed_s']].mean()
    idle_w = (idle.bmc_meanW).to_dict(); idle_pkg_w = (idle.pkg_J / idle.elapsed_s).to_dict()
    print('idle power by cap (MHz -> wall W, pkg W):', {k: (round(idle_w[k], 1), round(idle_pkg_w[k], 1)) for k in idle_w})
    w = d[d.workload.isin(['burn', 'mem', 'redis'])].copy()
    w['cfg'] = w.placement + '@' + w.mhz.astype(str)
    w['bmc_dyn_J'] = w.bmc_J - w.mhz.map(idle_w) * w.elapsed_s
    w['pkg_dyn_J'] = w.pkg_J - w.mhz.map(idle_pkg_w) * w.elapsed_s
    rows = []
    for wl, g in w.groupby('workload'):
        base = g[g.cfg == 'default@3800']
        b = {c: base[c].mean() for c in ('elapsed_s', 'pkg_J', 'bmc_J', 'bmc_dyn_J', 'pkg_dyn_J', 'perf')}
        for cfg, x in g.groupby('cfg', sort=False):
            r = dict(workload=wl, cfg=cfg, n=len(x))
            for c in ('elapsed_s', 'pkg_J', 'bmc_J', 'bmc_dyn_J', 'pkg_dyn_J'):
                r[c] = f'{x[c].mean():.1f} ± {x[c].std():.1f}'
                r[c + '_rel'] = f'{100 * (x[c].mean() / b[c] - 1):+.1f}%'
            r['cv_bmc_J'] = f'{100 * x.bmc_J.std() / x.bmc_J.mean():.1f}%'
            # significant vs baseline? (difference > 2 * pooled sd)
            sd = np.sqrt((x.bmc_J.var() + base.bmc_J.var()) / 2)
            r['bmc_sig'] = 'yes' if abs(x.bmc_J.mean() - b['bmc_J']) > 2 * sd else 'no'
            sdp = np.sqrt((x.pkg_J.var() + base.pkg_J.var()) / 2)
            r['pkg_sig'] = 'yes' if abs(x.pkg_J.mean() - b['pkg_J']) > 2 * sdp else 'no'
            if wl == 'redis': r['krps'] = f'{x.perf.mean() / 1e3:.0f} ± {x.perf.std() / 1e3:.0f}'
            rows.append(r)
    o = pd.DataFrame(rows); o.to_csv(f'{D}/phase5_summary.csv', index=False)
    print('| ' + ' | '.join(o.columns) + ' |\n|' + '---|' * len(o.columns))
    for _, r in o.iterrows(): print('| ' + ' | '.join(str(v) for v in r.values) + ' |')

if __name__ == '__main__': globals()[sys.argv[1]]()
