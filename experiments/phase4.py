#!/usr/bin/env python3
"""Phase 4 data collection: per-second system-wide signals across diverse workloads.
Signals: BMC wall power, RAPL pkg & sum-of-core (MSR), HSMP socket power + DDR BW, perf core PMCs
(instructions, cycles, DRAM fills), UMC (memory controller) CAS rd/wr + ACT for all 12 channels,
resctrl MBM total bytes (per workload group).
Usage: sudo python3 experiments/phase4.py collect [reps]   -> data/phase4_seconds.csv
       python3 experiments/phase4.py fit                   -> data/phase4_fit.csv + printed table"""
import sys, os, time, subprocess, random, threading, glob
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
RES = '/sys/fs/resctrl'
UMC_EV = ','.join(f'amd_umc_{i}/umc_cas_cmd.rd/,amd_umc_{i}/umc_cas_cmd.wr/,amd_umc_{i}/umc_act_cmd.all/' for i in range(12))
CORE_EV = 'instructions,cycles,ls_any_fills_from_sys.dram_io_all'

def P(n): return list(range(min(n, 32))) + list(range(32, 32 + max(0, n - 32)))
def ccx_fill(n):  # n cores packed onto CCXs in order
    return list(range(n))
SN = 'stress-ng --quiet --timeout {t}s --taskset {cpus} '
CONFIGS = {
    'idle': [],
    'burn8': [('burn', P(8))], 'burn32': [('burn', P(32))], 'burn64': [('burn', P(64))],
    'memrand4': [('memrand', P(4))], 'memrand16': [('memrand', P(16))], 'memrand32': [('memrand', P(32))], 'memrand64': [('memrand', P(64))],
    'memseq8': [('memseq', P(8))], 'memseq32': [('memseq', P(32))],
    'meml3_32': [('meml3', P(32))],
    'matrix32': [('sn_matrix', P(32))], 'stream32': [('sn_stream', P(32))],
    'mix_burn32_mem32': [('burn', list(range(32))), ('memrand', list(range(32, 64)))],
}

def start(kind, cpus, secs):
    cl = ','.join(map(str, cpus))
    if kind == 'burn': return launch('burn', cpus, secs)
    if kind == 'memrand': return launch('mem', cpus, secs, ['1024', '1024', '0', '1', '64', '50'])
    if kind == 'memseq': return launch('mem', cpus, secs, ['1024', '1024', '0', '0', '64', '50'])
    if kind == 'meml3': return launch('mem', cpus, secs, ['4', '4', '0', '1', '64', '50'])
    if kind == 'sn_matrix': return [subprocess.Popen(SN.format(t=secs, cpus=cl) + f'--matrix {len(cpus)}', shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)]
    if kind == 'sn_stream': return [subprocess.Popen(SN.format(t=secs, cpus=cl) + f'--stream {len(cpus)}', shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)]
    raise ValueError(kind)

def mbm_total(group=''):
    return sum(int(open(f).read()) for f in glob.glob(f'{RES}/{group}mon_data/mon_L3_*/mbm_total_bytes'))

def collect(reps, secs=16, warm=4):
    if not os.path.exists(f'{RES}/info'): sh(f'mount -t resctrl resctrl {RES}')
    rows = []
    for rep in range(reps):
        order = list(CONFIGS); random.seed(100 + rep); random.shuffle(order)
        if os.environ.get('ONLY'): order = os.environ['ONLY'].split(',')
        for name in order:
            time.sleep(3)
            procs = []
            for kind, cpus in CONFIGS[name]: procs += start(kind, cpus, secs + warm + 2)
            # per-workload resctrl mon groups (one per component)
            groups = []
            for j, (kind, cpus) in enumerate(CONFIGS[name]):
                g = f'mon_groups/w{j}_{kind}/'; os.makedirs(f'{RES}/{g}', exist_ok=True); groups.append(g)
            time.sleep(0.3)
            for g, (kind, cpus) in zip(groups, CONFIGS[name]):
                for p in procs:
                    pass
            # assign pids (incl. stress-ng children) to groups by cpu list membership
            pid_groups = {}
            for (kind, cpus), g in zip(CONFIGS[name], groups):
                for c in cpus: pid_groups[c] = g
            time.sleep(warm - 0.3)
            for line in sh('ps -eo pid,psr,comm --no-headers').splitlines():
                pid, psr, comm = line.split(None, 2)
                if comm in ('burn', 'mem_miss') or comm.startswith('stress-ng'):
                    g = pid_groups.get(int(psr))
                    if g:
                        try: open(f'{RES}/{g}tasks', 'w').write(pid)
                        except OSError: pass
            perf = subprocess.Popen(f'perf stat -a -x, -I 1000 -e {CORE_EV},{UMC_EV} sleep {secs}', shell=True, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
            rs = subprocess.Popen(f'{BIN}/rapl series {secs} 1000', shell=True, stdout=subprocess.PIPE, text=True)
            bmc = Poller(bmc_watts, 0.25); hs = Poller(hsmp_socket_w, 0.25); bw = Poller(lambda: hsmp_ddr_bw()[1], 0.25)
            mbm = Poller(lambda: [mbm_total()] + [mbm_total(g) for g in groups], 1.0)
            t0 = time.monotonic()
            for p in (bmc, hs, bw, mbm): p.start()
            perr = perf.communicate()[1]; rso = rs.communicate()[0]
            for p in (bmc, hs, bw, mbm): p.stop()
            wait_all(procs)
            for g in groups:
                try: os.rmdir(f'{RES}/{g}')
                except OSError: pass
            # parse perf -I output: time,count,unit,event,...
            pm = {}
            for l in perr.splitlines():
                f = l.split(',')
                if len(f) < 4 or not f[0].strip()[0].isdigit(): continue
                sec = int(round(float(f[0]))); ev = f[3]
                try: v = float(f[1])
                except ValueError: continue
                key = 'umc_' + ev.split('/')[1].split('.')[-1] if ev.startswith('amd_umc') else ev
                pm.setdefault(sec, {}); pm[sec][key] = pm[sec].get(key, 0) + v
            rsd = pd.read_csv(__import__('io').StringIO(rso))
            for i, r in rsd.iterrows():
                sec = int(round(r.t_s)); a, b = t0 + sec - 1, t0 + sec
                m = [(t, v) for t, v in mbm.samples if a - 0.5 <= t <= b + 0.5]
                mb = (np.array(m[-1][1]) - np.array(m[0][1])) / max(1e-9, m[-1][0] - m[0][0]) if len(m) >= 2 else [np.nan] * (1 + len(groups))
                row = dict(rep=rep, config=name, sec=sec, pkg_W=r.pkg_J, sumcore_W=sum(r[f'core{c}_J'] for c in range(32)),
                           bmc_W=bmc.mean(a, b), hsmp_W=hs.mean(a, b), hsmp_ddr_GBs=bw.mean(a, b), mbm_GBs=mb[0] / 1e9,
                           mbm_groups_GBs=';'.join(f'{x / 1e9:.3f}' for x in mb[1:]), **pm.get(sec, {}))
                rows.append(row)
            print(rep, name, 'pkg %.1f core %.1f bmc %.1f mbm %.1f GB/s cas %.3g' % (
                np.mean([x['pkg_W'] for x in rows[-len(rsd):]]), np.mean([x['sumcore_W'] for x in rows[-len(rsd):]]),
                np.nanmean([x['bmc_W'] for x in rows[-len(rsd):]]), np.nanmean([x['mbm_GBs'] for x in rows[-len(rsd):]]),
                np.nanmean([x.get('umc_rd', 0) + x.get('umc_wr', 0) for x in rows[-len(rsd):]])), flush=True)
            pd.DataFrame(rows).to_csv(os.path.join(DATA, 'phase4_seconds.csv'), index=False)

if __name__ == '__main__':
    if sys.argv[1] == 'collect': collect(int(sys.argv[2]) if len(sys.argv) > 2 else 3)

def fit():
    d = pd.read_csv(os.path.join(DATA, 'phase4_seconds.csv'))
    d = d[d.sec >= 3].copy()  # drop first 2 s of each window (BMC lag <= ~1.1 s)
    d['cas_GBs'] = (d.umc_rd + d.umc_wr) * 64 / 1e9
    d['rd_GBs'] = d.umc_rd * 64 / 1e9; d['wr_GBs'] = d.umc_wr * 64 / 1e9
    d['act_M'] = d.umc_all / 1e6 if 'umc_all' in d else np.nan
    d['ginstr'] = d.instructions / 1e9; d['gcyc'] = d.cycles / 1e9; d['fill_GBs'] = d['ls_any_fills_from_sys.dram_io_all'] * 64 / 1e9
    d['uncore_W'] = d.pkg_W - d.sumcore_W; d['nonpkg_W'] = d.bmc_W - d.pkg_W
    agg = d.groupby(['rep', 'config']).mean(numeric_only=True).reset_index()
    models = {
        'pkg': ['pkg_W'],
        'sumcore': ['sumcore_W'],
        'hsmp': ['hsmp_W'],
        'core+uncore': ['sumcore_W', 'uncore_W'],
        'core+uncore+umc': ['sumcore_W', 'uncore_W', 'rd_GBs', 'wr_GBs', 'act_M'],
        'core+uncore+umc+pmc': ['sumcore_W', 'uncore_W', 'rd_GBs', 'wr_GBs', 'act_M', 'ginstr', 'gcyc'],
        'pkg+mbm': ['pkg_W', 'mbm_GBs'],
        'pkg+cas': ['pkg_W', 'cas_GBs'],
        'pmc only': ['ginstr', 'gcyc', 'fill_GBs'],
    }
    out = []
    for level, X in (('per-second', d), ('per-run mean', agg)):
        for target in ('bmc_W', 'nonpkg_W'):
            Xd = X.dropna(subset=[target])
            ss = ((Xd[target] - Xd[target].mean()) ** 2).sum()
            for name, cols in models.items():
                if target == 'nonpkg_W' and 'pkg_W' in cols: continue
                Z = Xd.dropna(subset=cols)
                A = np.c_[np.ones(len(Z)), Z[cols].values]; y = Z[target].values
                coef, *_ = np.linalg.lstsq(A, y, rcond=None); r2 = 1 - ((y - A @ coef) ** 2).sum() / ((y - y.mean()) ** 2).sum()
                # leave-one-config-out CV
                pred = np.full(len(Z), np.nan)
                for cfg in Z.config.unique():
                    tr = (Z.config != cfg).values
                    c2, *_ = np.linalg.lstsq(A[tr], y[tr], rcond=None); pred[~tr] = A[~tr] @ c2
                cv_r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum(); rmse = np.sqrt(np.mean((y - pred) ** 2))
                out.append(dict(level=level, target=target, model=name, n=len(Z), r2=r2, cv_r2=cv_r2, cv_rmse_W=rmse,
                                coef=' '.join(f'{c}={v:.3g}' for c, v in zip(['b0'] + cols, coef))))
    o = pd.DataFrame(out); o.to_csv(os.path.join(DATA, 'phase4_fit.csv'), index=False)
    print(o.drop(columns='coef').to_string(index=False, float_format=lambda v: f'{v:.3f}'))
    print(o[['level', 'target', 'model', 'coef']].to_string(index=False))
    cfgs = agg.groupby('config')[['bmc_W', 'pkg_W', 'sumcore_W', 'uncore_W', 'nonpkg_W', 'cas_GBs', 'mbm_GBs', 'hsmp_ddr_GBs', 'act_M', 'ginstr']].agg(['mean', 'std'])
    print(cfgs.round(2).to_string())
    cfgs.round(3).to_csv(os.path.join(DATA, 'phase4_configs.csv'))

if __name__ == '__main__' and sys.argv[1] == 'fit': fit()
