#!/bin/bash
# Runs Phase 3, 4 and 5 experiments back to back. Usage: sudo experiments/run_phase345.sh
cd "$(dirname "$0")/.."
modprobe msr; modprobe amd_hsmp; modprobe amd-uncore
lsmod | grep -q read_core_energy || insmod energy_kfunc_module/module/read_core_energy.ko
for e in smt pingpong uncore; do python3 -u experiments/phase3.py $e 5; done
python3 -u experiments/phase4.py collect 3
python3 -u experiments/phase5.py run 5
python3 -u experiments/phase3.py efsfix 5
