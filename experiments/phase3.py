#!/usr/bin/env python3
"""Phase 3: test the per-task accounting used by efs.bpf.c against references, offline from a
context-switch trace (bin/taskacct). Usage: sudo python3 experiments/phase3.py {smt|pingpong|uncore} [reps]
Writes data/phase3_<exp>.csv (summaries) and data/traces/phase3_<exp>_rep<k>.csv.gz (filtered traces)."""
import sys, os, time, subprocess, gzip, random
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
UNIT = 2 ** -16
TR = os.path.join(DATA, 'traces'); os.makedirs(TR, exist_ok=True)

def trace(secs, path):
    return subprocess.Popen([f'{BIN}/taskacct', str(secs), path], stderr=subprocess.PIPE, text=True)

def load(path, cpus=None):
    df = pd.read_csv(path)
    if cpus is not None: df = df[df.cpu.isin(cpus)]
    return df.sort_values(['cpu', 'ts_ns']).reset_index(drop=True)

def intervals(df):
    """One row per run interval: [ts_prev_event, ts_event) on a CPU, owned by event.prev_pid.
    dE = core counter delta (J) seen by that CPU over the interval (32-bit wrap handled)."""
    out = []
    for cpu, g in df.groupby('cpu'):
        g = g.sort_values('ts_ns')
        d = pd.DataFrame({'cpu': cpu, 't0': g.ts_ns.values[:-1], 't1': g.ts_ns.values[1:],
                          'pid': g.prev_pid.values[1:], 'comm': g.prev_comm.values[1:],
                          'dE': ((g.core_raw.values[1:] - g.core_raw.values[:-1]) % 2**32) * UNIT,
                          'dPkg': ((g.pkg_raw.values[1:] - g.pkg_raw.values[:-1]) % 2**32) * UNIT,
                          'dI': np.diff(g.instr.values), 'dC': np.diff(g.cycles.values), 'dM': np.diff(g.dram_fills.values)})
        out.append(d)
    r = pd.concat(out); r['dt'] = (r.t1 - r.t0) / 1e9
    return r

def efs_naive(iv):
    """efs.bpf.c sched_stopping: charge each non-idle interval's core delta to the task (skips intervals
    whose counter did not change; that energy is then lost)."""
    x = iv[(iv.pid != 0)]
    return x.groupby('comm').dE.sum()

def smt_aware(iv, cpus_of_core, p_idle_thread):
    """Fix: per physical core, energy between consecutive counter reads on either sibling is split across
    the tasks running on both siblings in proportion to their run time; idle threads get a fixed
    idle share (p_idle_thread * idle time). Uses a >=10 ms accumulation window per core."""
    res = {}
    for core, cpus in cpus_of_core.items():
        x = iv[iv.cpu.isin(cpus)].sort_values('t1')
        if x.empty: continue
        # counter is shared: total core energy = delta over the whole run seen by one sibling
        win = 10e6
        t_start = x.t0.min(); edges = np.arange(t_start, x.t1.max() + win, win)
        # energy per window from the sibling with more events (both read same counter)
        c0 = x[x.cpu == cpus[0]]
        cum_t = np.concatenate([[c0.t0.iloc[0]], c0.t1.values]); cum_e = np.concatenate([[0], np.cumsum(c0.dE.values)])
        Ew = np.diff(np.interp(edges, cum_t, cum_e))
        for k in range(len(edges) - 1):
            a, b = edges[k], edges[k + 1]
            ov = (np.minimum(x.t1.values, b) - np.maximum(x.t0.values, a)).clip(0) / 1e9
            sel = ov > 0
            if not sel.any(): continue
            w = pd.Series(ov[sel], index=x.comm.values[sel]).groupby(level=0).sum().to_dict()
            idle_t = sum(w.pop('swapper/%d' % c, 0) for c in cpus); w = pd.Series(w, dtype=float)
            e = max(0.0, Ew[k] - p_idle_thread * idle_t)
            if w.sum() > 0:
                for c, v in (w / w.sum() * e).items(): res[c] = res.get(c, 0) + v
    return pd.Series(res)

def exp_smt(reps, secs=8):
    """burn on cpu2 + mem on cpu34 (SMT siblings); references: burn alone on cpu3, mem alone on cpu4 (and cpu36/35 idle)."""
    rows = []
    for rep in range(reps):
        time.sleep(2)
        procs = launch('burn', [2, 3], secs + 4) + launch('mem', [34, 4], secs + 4, ['1024', '1024', '0', '1', '64', '50'])
        time.sleep(1.5)
        path = f'/tmp/phase3_smt.csv'; t = trace(secs, path); t.communicate(); wait_all(procs)
        df = load(path, [2, 34, 3, 35, 4, 36])
        df.to_csv(os.path.join(TR, f'phase3_smt_rep{rep}.csv.gz'), index=False, compression='gzip')
        iv = intervals(df)
        dur = (iv.t1.max() - iv.t0.min()) / 1e9
        core2 = iv[iv.cpu == 2].dE.sum(); core3 = iv[iv.cpu == 3].dE.sum(); core4 = iv[iv.cpu == 4].dE.sum()
        nv = iv[iv.pid != 0]
        naive_core2_tasks = nv[nv.cpu.isin([2, 34])].groupby('comm').dE.sum()
        sa = smt_aware(iv[iv.cpu.isin([2, 34])], {2: [2, 34]}, 0.0)
        rows.append(dict(rep=rep, dur_s=dur, core2_counter_J=core2, naive_sum_tasks_core2_J=naive_core2_tasks.sum(),
                         naive_burn_J=naive_core2_tasks.get('burn', 0), naive_mem_J=naive_core2_tasks.get('mem_miss', 0),
                         smtaware_burn_J=sa.get('burn', 0), smtaware_mem_J=sa.get('mem_miss', 0),
                         ref_burn_alone_J=core3, ref_mem_alone_J=core4))
        print({k: round(v, 3) for k, v in rows[-1].items()})
    pd.DataFrame(rows).to_csv(os.path.join(DATA, 'phase3_smt.csv'), index=False)

def exp_pingpong(reps, secs=8):
    """pp_cpu and pp_mem alternate on cpu 6 with work_us per turn. Truth per task = its power measured
    in the 20 ms-turn run (intervals >> 1 ms counter period) x its run time."""
    rows = []
    for rep in range(reps):
        for wus in (20000, 1000, 200, 50):
            time.sleep(2)
            p = subprocess.Popen(['taskset', '-c', '6', f'{BIN}/pingpong', str(wus), str(secs + 3)], stdout=subprocess.PIPE, text=True)
            time.sleep(2.5)
            path = '/tmp/phase3_pp.csv'; t = trace(secs, path); t.communicate(); p.communicate()
            df = load(path, [6, 38]); 
            df.to_csv(os.path.join(TR, f'phase3_pingpong_w{wus}_rep{rep}.csv.gz'), index=False, compression='gzip')
            iv = intervals(df[df.cpu == 6])
            g = iv.groupby('comm').agg(E=('dE', 'sum'), T=('dt', 'sum'), n=('dt', 'size'), I=('dI', 'sum'), C=('dC', 'sum'), M=('dM', 'sum'))
            row = dict(rep=rep, work_us=wus, core_total_J=iv.dE.sum(), dur_s=iv.dt.sum())
            for c in ('pp_cpu', 'pp_mem', 'swapper/6'):
                if c in g.index:
                    k = c.split('/')[0]
                    row.update({f'{k}_naive_J': g.loc[c, 'E'], f'{k}_runtime_s': g.loc[c, 'T'], f'{k}_n': g.loc[c, 'n'],
                                f'{k}_instr': g.loc[c, 'I'], f'{k}_cycles': g.loc[c, 'C'], f'{k}_dram': g.loc[c, 'M'],
                                f'{k}_zero_dE_frac': float((iv[iv.comm == c].dE == 0).mean())})
            rows.append(row); print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})
    pd.DataFrame(rows).to_csv(os.path.join(DATA, 'phase3_pingpong.csv'), index=False)

def exp_uncore(reps, secs=6):
    """Replicate efs.bpf.c's uncore sum: for each non-idle interval add max(0, dPkg - dCore). Compare with
    true pkg - sum(core) over the same period, for n burners."""
    rows = []
    for rep in range(reps):
        for n in (0, 1, 8, 32, 64):
            time.sleep(2)
            procs = launch('burn', list(range(min(n, 32))) + list(range(32, 32 + max(0, n - 32))), secs + 4) if n else []
            time.sleep(1.5)
            path = '/tmp/phase3_unc.csv'; t = trace(secs, path); t.communicate(); wait_all(procs)
            df = load(path); iv = intervals(df)
            dur = (df.ts_ns.max() - df.ts_ns.min()) / 1e9
            efs = (iv[iv.pid != 0].dPkg - iv[iv.pid != 0].dE).clip(lower=0).sum()
            pk = df.sort_values('ts_ns'); pkgJ = ((np.diff(pk.pkg_raw.values) % 2**32) * UNIT).sum()
            coreJ = sum(iv[iv.cpu == c].dE.sum() for c in range(32))
            rows.append(dict(rep=rep, n=n, dur_s=dur, events=len(df), efs_uncore_W=efs / dur, true_uncore_W=(pkgJ - coreJ) / dur,
                             pkg_W=pkgJ / dur, sumcore_W=coreJ / dur, ratio=efs / max(1e-9, pkgJ - coreJ)))
            print({k: round(v, 3) for k, v in rows[-1].items()})
            os.remove(path)
    pd.DataFrame(rows).to_csv(os.path.join(DATA, 'phase3_uncore.csv'), index=False)

if __name__ == '__main__':
    globals()['exp_' + sys.argv[1]](int(sys.argv[2]) if len(sys.argv) > 2 else 5)
