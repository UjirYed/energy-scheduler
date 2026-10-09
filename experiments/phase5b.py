#!/usr/bin/env python3
"""Phase 5b: data-fabric (DF) P-state as an actuator for the ~87 W SoC floor.
HSMP 0x0D SET_DF_PSTATE(p) forces DF P-state p (0 = highest FCLK); 0x0E SET_AUTO_DF_PSTATE re-enables the
SMU's automatic DF P-state selection. Initial state on this node: FCLK/MCLK 1800/2400 at idle (=DF P0, fixed by
BIOS). Restored to DF P0 at the end (and verified via 0x0F GET_FCLK_MCLK).
Usage: sudo python3 experiments/phase5b.py [reps]  -> data/phase5b_df.csv"""
import sys, os, time, random
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import phase5 as p5

def set_df(mode):
    if mode == 'auto': hsmp(0x0E, [], 0)
    else: hsmp(0x0D, [int(mode)], 0)
    time.sleep(1.5)

def fclk(): f, m = hsmp(0x0F, [], 2); return f, m

def main(reps):
    f0 = fclk(); print('initial FCLK/MCLK', f0, flush=True)
    rows = []
    modes = ['0', '1', '2', 'auto']
    try:
        for rep in range(reps):
            cfgs = [(m, wl) for m in modes for wl in ('idle', 'mem', 'burn')]
            random.seed(900 + rep); random.shuffle(cfgs)
            for mode, wl in cfgs:
                set_df(mode); time.sleep(2)
                fc = Poller(lambda: fclk()[0], 0.5); fc.start()
                r = p5.measure(wl, 'default', 3800)
                fc.stop()
                r.update(rep=rep, df_mode=mode, workload=wl, fclk_mean=np.mean([v for t, v in fc.samples]) if fc.samples else np.nan)
                rows.append(r); print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
                pd.DataFrame(rows).to_csv(os.path.join(DATA, 'phase5b_df.csv'), index=False)
    finally:
        set_df('0'); p5.set_freq(3800)
        print('restored FCLK/MCLK', fclk(), 'boost limit', hsmp(0x0A, [0]), flush=True)

if __name__ == '__main__': main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
