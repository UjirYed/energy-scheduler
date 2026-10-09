#!/bin/bash
# Runs all Phase 2 experiments. Usage: sudo experiments/run_phase2.sh
set -e
cd "$(dirname "$0")/.."
modprobe msr; modprobe amd_hsmp
for e in crosscheck smt neighbors; do python3 experiments/phase2.py $e 5; done
python3 experiments/phase2.py bmcrate 3
python3 experiments/phase2.py sumpkg 5
