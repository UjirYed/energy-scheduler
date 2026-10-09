#!/usr/bin/env python3
"""Phase 5: which actuators change total energy for a fixed amount of work?
Actuators: socket frequency cap (HSMP boost limit, msg 0x09; there is no cpufreq driver on this node),
placement (default CFS vs pinned spread/pack/SMT/CCX). Workloads (fixed work): burn (CPU), mem (DRAM
random), redis (redis-benchmark fixed request count).
Energy: RAPL pkg (MSR, exact) and BMC wall (1 s samples integrated). Also reports energy above idle.
Usage: sudo python3 experiments/phase5.py run [reps] [workloads,comma,separated]  -> data/phase5.csv"""
import sys, os, time, subprocess, random, re
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

FMAX = 3800
N = 8
PLACE = {
    'default':    None,                               # CFS chooses
    'spread_ccx': [0, 4, 8, 12, 16, 20, 24, 28],      # one task per CCX (8 CCX)
    'pack_ccx':   list(range(8)),                     # 8 cores on 2 CCX
    'pack_smt':   [0, 1, 2, 3, 32, 33, 34, 35],       # 4 physical cores, both siblings, 1 CCX
}
WORK = {'burn': 3000, 'mem': 200_000_000}            # per task; ~8-10 s at default settings

def set_freq(mhz):
    hsmp(0x09, [mhz], 0)

def run_once(wl, place, mhz):
    cpus = PLACE[place]
    t0 = time.monotonic()
    bmc = Poller(bmc_watts, 0.25); bmc.start()
    procs = []
    if wl == 'idle':
        time.sleep(10); outs, perf = [], None
    elif wl in ('burn', 'mem'):
        for i in range(N):
            cmd = [f'{BIN}/burn', '0', str(WORK['burn'])] if wl == 'burn' else \
                  [f'{BIN}/mem_miss', '1024', '1024', str(WORK['mem']), '1', '64', '50', '0']
            if cpus: cmd = ['taskset', '-c', str(cpus[i])] + cmd
            procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True))
        outs = wait_all(procs); perf = None
    elif wl == 'redis':
        # server on first cpu of the placement (or unpinned), N-1 benchmark clients threads on the rest
        srv_cpu = cpus[0] if cpus else None; cli_cpus = ','.join(map(str, cpus[1:])) if cpus else None
        pre = f'taskset -c {srv_cpu} ' if cpus else ''
        srv = subprocess.Popen(f'{pre}redis-server --port 7777 --save "" --appendonly no --io-threads 1', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        pre = f'taskset -c {cli_cpus} ' if cpus else ''
        o = sh(f'{pre}redis-benchmark -p 7777 -n 4000000 -c 64 -P 16 --threads {N - 1} -t set,get -q')
        srv.terminate(); srv.wait()
        outs = [o]; perf = sum(float(m) for m in re.findall(r'([\d.]+) requests per second', o))
    t1 = time.monotonic()
    bmc.stop()
    return t0, t1, outs, perf, bmc

def measure(wl, place, mhz):
    """Run once and return energy over exactly the run span using a pkg-energy series."""
    # pkg energy: sample counter before/after with rapl series in background at 100 ms
    set_freq(mhz); time.sleep(1.0)
    rs = subprocess.Popen([f'{BIN}/rapl', 'series', '600', '100', '0'], stdout=subprocess.PIPE, text=True)
    time.sleep(0.3)
    t0, t1, outs, perf, bmc = run_once(wl, place, mhz)
    time.sleep(0.15)
    rs.terminate(); out = rs.communicate()[0]
    df = pd.read_csv(__import__('io').StringIO(out))
    # 100 ms pkg-energy samples stamped with CLOCK_MONOTONIC (same clock as time.monotonic)
    el = t1 - t0
    pkgJ = df[(df.t_abs > t0 + 0.05) & (df.t_abs <= t1 + 0.05)].pkg_J.sum()
    bw = [w for t, w in bmc.samples if t0 <= t <= t1]
    return dict(elapsed_s=el, pkg_J=pkgJ, bmc_J=np.mean(bw) * el if bw else np.nan, bmc_meanW=np.mean(bw) if bw else np.nan,
                perf=perf if perf is not None else np.nan)

def idle_baseline(secs=10):
    set_freq(FMAX); time.sleep(2)
    bmc = Poller(bmc_watts, 0.25); bmc.start(); r = rapl_window(secs); bmc.stop()
    return r['pkg_J'] / r['secs'], np.mean([w for t, w in bmc.samples])

def run(reps, wls):
    rows = []
    configs = []
    for wl in wls:
        for mhz in (3800, 3000, 2200, 1500): configs.append((wl, 'default', mhz))
        for place in ('spread_ccx', 'pack_ccx', 'pack_smt'): configs.append((wl, place, FMAX))
    for mhz in (3800, 3000, 2200, 1500): configs.append(('idle', 'default', mhz))  # idle power under each cap
    try:
        for rep in range(reps):
            ip, iw = idle_baseline()
            rows.append(dict(rep=rep, workload='idle', placement='-', mhz=FMAX, elapsed_s=10, pkg_J=ip * 10, bmc_J=iw * 10, bmc_meanW=iw, perf=np.nan, idle_pkg_W=ip, idle_bmc_W=iw))
            random.seed(500 + rep); order = configs[:]; random.shuffle(order)
            for wl, place, mhz in order:
                time.sleep(3)
                r = measure(wl, place, mhz)
                r.update(rep=rep, workload=wl, placement=place, mhz=mhz, idle_pkg_W=ip, idle_bmc_W=iw)
                r['pkg_above_idle_J'] = r['pkg_J'] - ip * r['elapsed_s']; r['bmc_above_idle_J'] = r['bmc_J'] - iw * r['elapsed_s']
                rows.append(r)
                print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
                pd.DataFrame(rows).to_csv(os.path.join(DATA, 'phase5.csv'), index=False)
    finally:
        set_freq(FMAX)

if __name__ == '__main__':
    if sys.argv[1] == 'run':
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 5, (sys.argv[3] if len(sys.argv) > 3 else 'burn,mem,redis').split(','))
