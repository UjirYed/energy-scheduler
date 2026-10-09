#!/usr/bin/env python3
"""Phase 2: validate AMD RAPL counters on this EPYC 9354P.
Usage: sudo python3 experiments/phase2.py {interval|smt|sumpkg|neighbors|bmcrate|crosscheck} [reps]
Writes data/phase2_<exp>.csv."""
import sys, os, csv, io, time, random, statistics as st, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

def out(name, rows):
    path = os.path.join(DATA, f'phase2_{name}.csv')
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print('wrote', path, len(rows), 'rows')

def exp_interval(reps):
    """Update interval of core (0xC001029A) and package (0xC001029B) counters. The poller runs pinned on
    a different CPU (cpu 1) and reads cpu 2's MSR, so cpu 2's activity = workload only (+IPI)."""
    rows = []
    for rep in range(reps):
        for msr, name in (('C001029A', 'core'), ('C001029B', 'pkg')):
            for load in ('idle', 'burn'):
                procs = launch('burn', [2], 4) if load == 'burn' else []
                time.sleep(0.5)
                o = sh(f'taskset -c 1 {BIN}/rapl interval 2 {msr} 2')
                wait_all(procs)
                t = [int(r['t_ns']) for r in csv.DictReader(io.StringIO(o))]
                d = [(b - a) / 1e6 for a, b in zip(t, t[1:])]
                rows.append(dict(rep=rep, counter=name, load=load, n_updates=len(t), median_ms=st.median(d),
                                 p5_ms=sorted(d)[len(d) // 20], p95_ms=sorted(d)[len(d) * 19 // 20], max_ms=max(d)))
                print(rows[-1])
    out('interval', rows)

def exp_smt(reps):
    """Is core counter per physical core? Load one hyperthread, read both siblings."""
    rows = []
    for rep in range(reps):
        for loaded in ([2], [34], [2, 34], []):
            procs = launch('burn', loaded, 7) if loaded else []
            time.sleep(1)
            r = rapl_window(5); wait_all(procs)
            rows.append(dict(rep=rep, loaded='+'.join(map(str, loaded)) or 'none',
                             cpu2_W=r['core_J'][2] / r['secs'], cpu34_W=r['core_J'][34] / r['secs'],
                             cpu3_W=r['core_J'][3] / r['secs'], cpu35_W=r['core_J'][35] / r['secs'],
                             cpu2_aperf=r['aperf'][2], cpu34_aperf=r['aperf'][34]))
            print(rows[-1])
    out('smt', rows)

def placement(n):
    """n instances: first fill one thread per physical core (0..31), then siblings."""
    return list(range(min(n, 32))) + list(range(32, 32 + max(0, n - 32)))

def exp_sumpkg(reps, secs=12):
    """Sum-of-cores vs package vs HSMP vs BMC across workloads/intensities."""
    configs = [('idle', 0)] + [('burn', n) for n in (1, 4, 8, 16, 32, 64)] + [('mem', n) for n in (1, 4, 8, 16, 32, 64)]
    rows = []
    for rep in range(reps):
        random.seed(rep); order = configs[:]; random.shuffle(order)
        for kind, n in order:
            time.sleep(3)  # settle
            procs = launch(kind, placement(n), secs + 3) if n else []
            time.sleep(1.5)
            bmc = Poller(bmc_watts, 0.25); hs = Poller(hsmp_socket_w, 0.1); bw = Poller(lambda: hsmp_ddr_bw()[1], 0.1)
            for p in (bmc, hs, bw): p.start()
            t0 = time.monotonic(); r = rapl_window(secs); t1 = time.monotonic()
            for p in (bmc, hs, bw): p.stop()
            res = wait_all(procs)
            S = r['secs']
            sum32 = sum(r['core_J'][c] for c in range(32)); sum64 = sum(r['core_J'][c] for c in range(64))
            mhz = [r['aperf'][c] / max(1, r['mperf'][c]) * 3250 for c in placement(n)] if n else [0]
            thr = sum(float(x.strip().splitlines()[-1]) for x in res if x.strip()) if kind == 'burn' else \
                  sum(float(l.split('accesses/s:')[1].split()[0]) for x in res for l in x.splitlines() if 'accesses/s' in l)
            rows.append(dict(rep=rep, workload=kind, n=n, secs=round(S, 3), pkg_W=r['pkg_J'] / S, sumcore_W=sum32 / S,
                             sum64_W=sum64 / S, uncore_W=(r['pkg_J'] - sum32) / S, hsmp_W=hs.mean(t0, t1), bmc_W=bmc.mean(t0, t1),
                             bmc_n=len([1 for t, _ in bmc.samples if t0 <= t <= t1]), ddr_GBs=bw.mean(t0, t1),
                             busy_mhz=st.mean(mhz), throughput=thr))
            print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in rows[-1].items()})
    out('sumpkg', rows)

def exp_neighbors(reps, secs=6):
    """Target core 0 runs burn; vary number of loaded neighbor cores (same CCX first, then others)."""
    rows = []
    for rep in range(reps):
        for k in (0, 3, 7, 15, 31):
            nbrs = list(range(1, 1 + k))
            time.sleep(2)
            procs = launch('burn', [0] + nbrs, secs + 3)
            time.sleep(1.5)
            r = rapl_window(secs); res = wait_all(procs)
            S = r['secs']
            rows.append(dict(rep=rep, neighbors=k, core0_W=r['core_J'][0] / S, core0_mhz=r['aperf'][0] / r['mperf'][0] * 3250,
                             core0_iters_s=float(res[0].strip()), pkg_W=r['pkg_J'] / S,
                             mean_nbr_W=(st.mean(r['core_J'][c] / S for c in nbrs) if nbrs else float('nan'))))
            print(rows[-1])
    out('neighbors', rows)

def exp_bmcrate(reps, secs=20):
    """How often does the BMC reading change? Poll fast under a 0/64-thread square wave (2 s period)."""
    rows = []
    for rep in range(reps):
        bmc = Poller(bmc_watts, 0.05); bmc.start(); t0 = time.monotonic()
        end = t0 + secs
        while time.monotonic() < end:
            ts = time.monotonic(); procs = launch('burn', range(64), 2); wait_all(procs)
            rows.append(dict(rep=rep, kind='edge', t=ts - t0, watts=float('nan')))
            time.sleep(2)
        bmc.stop()
        rows += [dict(rep=rep, kind='sample', t=t - t0, watts=w) for t, w in bmc.samples]
        ch = [t for (t, w), (_, w0) in zip(bmc.samples[1:], bmc.samples) if w != w0]
        gaps = [b - a for a, b in zip(ch, ch[1:])]
        print(rep, 'samples', len(bmc.samples), 'changes', len(ch), 'median gap', st.median(gaps) if gaps else None)
    out('bmcrate', rows)

def exp_crosscheck(reps, secs=10):
    """Our MSR decoding vs turbostat (CorWatt/PkgWatt) and perf power/energy-pkg over the same window, 8 burners."""
    rows = []
    for rep in range(reps):
        procs = launch('burn', range(8), secs + 6); time.sleep(1.5)
        ts = subprocess.Popen(f'turbostat --quiet --show CPU,CorWatt,PkgWatt --interval {secs} --num_iterations 1',
                              shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        pf = subprocess.Popen(f'perf stat -a -x, -e power/energy-pkg/ sleep {secs}', shell=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        r = rapl_window(secs)
        tso = ts.communicate()[0]; pfo = pf.communicate()[1]; wait_all(procs)
        lines = [l.split() for l in tso.splitlines() if l.strip()]
        hdr = next(l for l in lines if 'CPU' in l)
        summ = lines[lines.index(hdr) + 1]
        tcore = float(summ[hdr.index('CorWatt')]); tpkg = float(summ[hdr.index('PkgWatt')])
        pj = float(pfo.strip().splitlines()[-1].split(',')[0])
        S = r['secs']
        rows.append(dict(rep=rep, ours_sumcore_W=sum(r['core_J'][c] for c in range(32)) / S, ours_pkg_W=r['pkg_J'] / S,
                         turbostat_sumcore_W=tcore, turbostat_pkg_W=tpkg, perf_pkg_W=pj / secs))
        print(rows[-1])
    out('crosscheck', rows)

if __name__ == '__main__':
    exp = sys.argv[1]; reps = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    globals()['exp_' + exp](reps)
