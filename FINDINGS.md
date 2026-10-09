# Findings — energy-aware scheduling on CloudLab r6615 (AMD EPYC 9354P)

Each entry gives the question, the exact command, the result, and the conclusion, tagged
**[measured]** or **[inferred]**. Raw data is in `data/`, scripts are in `experiments/`.
Node: `node0.riju-energy.e6897-pg0.clemson.cloudlab.us`; session began 2026-10-09 ~17:15 UTC.

## Phase 1 — Inventory

Command: `sudo experiments/inventory.sh > data/inventory.txt 2>&1` (plus the HSMP probes below).

| Item | Result | |
|---|---|---|
| Kernel | Ubuntu 24.04.4, `6.8.0-138-generic` | measured |
| sched_ext | **Not available.** No `/sys/kernel/sched_ext`, no `sched_ext_ops` in kernel BTF. sched_ext was merged in 6.12. `bpftool struct_ops register efs.bpf.c.o` → `struct sched_ext_ops is not found in kernel BTF`. | measured |
| Newer kernel | apt offers `linux-generic-hwe-24.04` (7.0.0-38) and `linux-image-6.17.0-42-generic`. Both would bring sched_ext and the `power_core` perf PMU. **Not installed**: that needs a new kernel and a reboot, and the brief requires asking first. | measured |
| SMT | On (`smt/control=on`), 32 cores / 64 threads; sibling of CPU *n* is *n*+32 | measured |
| cpufreq | **No cpufreq driver at all.** dmesg: `amd_pstate: the _CPC object is not present in SBIOS or ACPI disabled`; `modprobe acpi-cpufreq` loads but creates no policies (no `_PSS`). There are no governors to switch. The BIOS is most likely in a "performance"/firmware-controlled power profile. | measured / inferred cause |
| Frequency in practice | turbostat at idle: Bzy_MHz ≈ 3797 on every core (Fmax 3800, base 3250). Boost is enabled (HWCR 0xC0010015 = 0xc9006011, CpbDis bit 25 clear). | measured |
| Hardware P-states (MSR) | 0xC0010064..66 = P0 3250 MHz (FID 0x82, DID 8), P1 2300 MHz (FID 0x5c, DID 8), P2 1500 MHz (FID 0x5a, DID 12). PStateCtl 0xC0010062 = 0. **Frequency can still be actuated** by writing PStateCtl per CPU and/or CpbDis; tested in Phase 5. | measured |
| Idle states | No cpuidle driver (`current_driver=none`); dmesg: `using mwait in idle threads`. There is no `_CST`, so the OS cannot request deeper C-states and only MWAIT-based idle is used. Whether hardware promotes to CC6 behind that is unknown. | measured / inferred |
| Topology | 1 socket, NPS4 (4 NUMA nodes of 8 cores, ~48 GB each). **8 CCDs/CCXs of 4 cores each** (L3 32 MB per CCX): {0-3}, {4-7}, …, {28-31}, plus siblings +32. Node 0 = CCX {0-3},{4-7}. | measured |
| resctrl | CPU flags include `cqm_mbm_total`, `cqm_mbm_local`, `cqm_occup_llc`, `cat_l3`, `mba`; `CONFIG_X86_CPU_RESCTRL=y`; resctrl fs available (mount tested in Phase 4). | measured |
| perf power PMUs | `power` exists with **only `energy-pkg`**. `power_core` **absent** (added in 6.13). | measured |
| amd_hsmp | `modprobe amd_hsmp` → `/dev/hsmp`, protocol v5, SMU fw 0x04478100. Works: socket power (0x04: 96.4 W idle), PPT limit (0x06/07: 300 W), FCLK/MCLK (0x0F: 1800/2400 MHz), DDR bandwidth (0x14: max 461 GB/s, utilization in **1 GB/s** steps), boost limit get/set (0x0A/0x08), Fmax/Fmin (0x1C: 3800/400). **Per-DIMM power (0x17) returns 0 for all 256 DIMM addresses**, DIMM temp range (0x16) returns 1, DIMM thermal (0x18) is 0 or EIO. Socket temp (0x15) is unsupported. | measured |
| BMC power | `ipmitool dcmi power reading` works in-band (`/dev/ipmi0`), ~50 ms per query, reports a 1 s sampling period; idle ≈ 208–223 W. `ipmitool sdr` also gives `Pwr Consumption` in whole watts. | measured |
| RAPL units | MSR 0xC0010299 = 0xa1000 on all 64 CPUs → energy status unit ESU = 16 → **2^-16 J = 15.26 µJ** per count. Counters are 32 bits wide: wrap = 65.5 kJ (≈ 3.6 min at 300 W pkg). | measured |
| Existing module | Did **not** build on 6.8 (`rdmsrq_safe` and `BTF_KFUNCS_START` only exist in ≥6.9/6.16). Added version-guarded compat macros; with `pahole` installed and `vmlinux` copied into the headers dir it builds with BTF, `insmod` succeeds, and BTF id 94 `read_core_energy` registers. Its kfunc is registered for `BPF_PROG_TYPE_STRUCT_OPS` only, so on this kernel no program can call it. | measured |
| Existing scheduler | `efs-sched/build.sh` compiles (it uses its own bundled `vmlinux.h` from a newer kernel) but cannot be loaded (no sched_ext). **The EFS comparison in Phase 5 cannot run on this kernel.** | measured |
| Tools | Installed: msr-tools, ipmitool, clang/llvm, libbpf-dev, stress-ng, sysbench, redis, dwarves, python numpy/pandas/sklearn/matplotlib. turbostat 2023.11.07 and perf already present. | — |

### Phase 1 conclusions
- **Deviations from the brief:** (a) this kernel can't run sched_ext; (b) there are no amd-pstate/acpi-cpufreq governors because the BIOS exposes no `_CPC`/`_PSS`; (c) there is no `power_core` PMU on this kernel. **[measured]**
- **Usable signals:** per-core and package RAPL via MSR, HSMP socket power and DDR bandwidth, BMC wall power at ~1 s, resctrl MBM, and core PMCs. **[measured]**
- **Usable actuators:** per-CPU P-state via MSR 0xC0010062, boost disable via HWCR, HSMP boost limit (per core or socket), affinity/placement, and SMT online/offline. **[measured that they exist; effects in Phase 5]**
- **Recommendation for later:** installing the 6.17 or HWE 7.0 kernel (needs a reboot, which the brief says to ask about first, so it is not done yet; awaiting your decision) would restore sched_ext and `power_core`. Everything in Phases 2–5 below is done from userspace and a tracing BPF program, so it does not depend on that.

## Phase 2 — Validating the energy counters

Tools: `experiments/bin/rapl` (built from `experiments/rapl.c`) reads the MSRs through `/dev/cpu/N/msr`. `experiments/phase2.py` drives the experiments and `experiments/analyze.py phase2` regenerates every table below. Workloads: `experiments/workloads/burn.c` is the `stress_core` inner loop **without** its 5 s on / 5 s off duty cycle (the original `stress_core` sleeps half the time, which matters when reading old graphs); `mem_miss` (repo version) runs random 64 B accesses over a 1 GB working set, 50% writes. Unless stated otherwise every row is 5 repetitions, given as mean ± sd.

### 2.1 Update interval
- **Question:** how often do the core (0xC001029A) and package (0xC001029B) counters actually change?
- **Command:** `sudo python3 experiments/phase2.py interval 5`. This busy-polls cpu 2's MSR from cpu 1 for 2 s, idle and with `burn` on cpu 2, and records the time of each change → `data/phase2_interval.csv`.
- **Result:** both counters change every **1.000 ms** (p5 0.995, p95 1.005, max 1.009 ms), with 2000 updates per 2 s in every rep and condition.
- **Conclusion [measured]:** the counters update at 1 kHz with very little jitter. A run interval shorter than 1 ms sees either 0 or 1 counter updates, so per-interval energy is quantized at 1 ms. Caveat: polling from another CPU sends IPIs to the target, so "idle" here is not deep idle. Whether the counter still ticks every 1 ms on a deeply idle core is unknown (no OS-visible deep C-states exist anyway).

### 2.2 Per core or per thread?
- **Question:** does 0xC001029A read differently on the two SMT siblings?
- **Command:** `sudo python3 experiments/phase2.py smt 5` → `data/phase2_smt.csv` (5 s windows; `burn` on cpu 2, on cpu 34, on both, or none).

| loaded | cpu2 W | cpu34 W | cpu3 W (other core) |
|---|---|---|---|
| none | 0.21 ± 0.00 | 0.21 ± 0.00 | 0.23 ± 0.03 |
| 2 | 2.67 ± 0.01 | 2.67 ± 0.01 | 0.23 ± 0.01 |
| 34 | 2.68 ± 0.03 | 2.68 ± 0.03 | 0.22 ± 0.01 |
| 2+34 | 3.72 ± 0.01 | 3.72 ± 0.01 | 0.22 ± 0.01 |

- **Conclusion [measured]:** the counter is **per physical core**: both siblings return the identical value. Loading the second sibling adds about 39% power for the same core. Any scheme that charges each hardware thread the core-counter delta over its own run interval (as `efs.bpf.c` does) **double-counts whenever both siblings are busy**: here 2 × 3.72 W would be charged against 3.72 W actually consumed. Summing the counter over all 64 CPUs double-counts the core total in the same way.

### 2.3 Unit conversion cross-check
- **Question:** is our decoding (ESU = 16 → 15.26 µJ) right?
- **Command:** `sudo python3 experiments/phase2.py crosscheck 5` → `data/phase2_crosscheck.csv`. For 8 `burn` tasks, our MSR window, `turbostat --show CorWatt,PkgWatt` and `perf stat -e power/energy-pkg/` run over the same 10 s.
- **Result:** sum of cores is ours 29.01 ± 0.56 W vs turbostat 28.99 ± 0.56 W; package is ours 113.16 ± 0.51 W vs turbostat 113.12 ± 0.52 W vs perf 113.18 ± 0.50 W.
- **Conclusion [measured]:** the decoding agrees with both tools to within 0.05%. The kfunc applies the same `>> ESU` conversion (in µJ, truncated), so its values match too **[inferred: no program can call the kfunc on this kernel without sched_ext; Phase 3 calls the new raw-counter kfunc from a tracing program and gets the same numbers]**.

### 2.4 Frequency/boost coupling between neighbors
- **Question:** does one core's reported power change when other cores are loaded?
- **Command:** `sudo python3 experiments/phase2.py neighbors 5` → `data/phase2_neighbors.csv`. `burn` runs on core 0, plus 0/3/7/15/31 neighbor cores also running `burn` (same CCX first).

| neighbors | core0 W | core0 MHz (APERF/MPERF) | core0 iters/s | mean neighbor W | pkg W |
|---|---|---|---|---|---|
| 0 | 2.70 ± 0.02 | 3800 | 315.6 | – | 98.1 ± 0.4 |
| 3 | 2.69 ± 0.02 | 3800 | 315.7 | 2.69 | 104.9 ± 0.4 |
| 7 | 2.68 ± 0.03 | 3800 | 315.1 | 2.66 | 113.8 ± 0.4 |
| 15 | 2.69 ± 0.04 | 3800 | 315.6 | 2.70 | 131.5 ± 0.6 |
| 31 | 2.62 ± 0.02 | 3800 | 315.4 | 2.68 | 167.3 ± 1.0 |

- **Conclusion [measured]:** for this scalar workload there is **no frequency coupling**: every core holds 3.8 GHz (Fmax) with all 32 cores loaded, because the package (167 W) is far below the 300 W limit. Core 0's reported power moves by ≤3%. Note that package power grows by only **2.23 W per added busy core**, while each core's own counter reports **~2.7 W**. Package and core counters therefore do not add up linearly; see 2.5. Wider (AVX-512) workloads that approach the power limit may couple; not tested here.

### 2.5 BMC (DCMI) wall power: rate and lag
- **Command:** `sudo python3 experiments/phase2.py bmcrate 3` → `data/phase2_bmcrate.csv`. Polls `ipmitool dcmi power reading` every 50 ms during a 2 s on / 2 s off square wave of 64 `burn` threads.
- **Result:** the reading changes about once every **1.05 s** (median gap between changes; p90 up to 2.1 s). After a load step it reaches 50% of the step within **0.3–0.7 s median, ≤1.1 s max** (this includes process launch time). Wall power is ~226 W idle and ~330 W with 64 threads.
- **Conclusion [measured]:** the BMC is a 1 Hz, whole-watt signal with ≤1.1 s lag. Use it only over windows of several seconds, never for per-task or sub-second attribution. Idle wall power (~225 W) is more than twice idle package power (~96 W).

### 2.6 Sum of cores vs package vs HSMP vs wall, across intensities (the "flat uncore" question)
- **Command:** `sudo python3 experiments/phase2.py sumpkg 5` → `data/phase2_sumpkg.csv`; summary via `python3 experiments/analyze.py phase2`. Each run is a 12 s window with tasks pinned one per physical core (n ≤ 32), then on siblings (n = 64), in randomized order. Signals: MSR package, sum of the 32 per-core counters (one thread per core), HSMP socket power at 10 Hz, BMC at 4 Hz polling, HSMP DDR bandwidth.

| cfg | pkg W | Σcore W | pkg−Σcore W | BMC W | BMC−pkg W | DDR GB/s |
|---|---|---|---|---|---|---|
| idle | 97.3 ± 0.5 | 9.8 ± 0.2 | 87.5 ± 0.3 | 223.5 ± 0.5 | 126.2 ± 0.3 | 0.4 |
| burn1 | 100.0 ± 0.5 | 12.2 ± 0.2 | 87.7 ± 0.5 | 226.1 ± 0.7 | 126.1 ± 0.7 | 0.5 |
| burn8 | 115.7 ± 0.6 | 29.3 ± 0.3 | 86.4 ± 0.4 | 241.7 ± 0.9 | 126.0 ± 0.9 | 0.4 |
| burn32 | 169.2 ± 0.6 | 86.5 ± 0.1 | 82.7 ± 0.5 | 299.3 ± 0.5 | 130.1 ± 0.8 | 0.5 |
| burn64 | 195.8 ± 0.3 | 117.7 ± 0.3 | 78.1 ± 0.3 | 328.4 ± 0.5 | 132.6 ± 0.5 | 0.3 |
| mem1 | 98.7 ± 0.6 | 11.2 ± 0.1 | 87.5 ± 0.4 | 225.2 ± 0.8 | 126.5 ± 0.4 | 1.5 |
| mem8 | 108.8 ± 0.5 | 22.4 ± 0.4 | 86.4 ± 0.3 | 238.8 ± 0.9 | 130.0 ± 0.9 | 9.5 |
| mem16 | 121.1 ± 1.1 | 34.9 ± 0.2 | 86.2 ± 1.1 | 254.7 ± 0.4 | 133.6 ± 1.2 | 18.8 |
| mem32 | 144.1 ± 0.6 | 58.3 ± 0.1 | 85.8 ± 0.7 | 286.1 ± 0.9 | 142.0 ± 1.1 | 37.9 |
| mem64 | 165.3 ± 0.8 | 67.7 ± 0.2 | 97.6 ± 0.9 | 316.1 ± 3.0 | 150.8 ± 2.4 | 66.5 |

(burn4, burn16, mem4 are in the CSV and follow the same trends.)

**Conclusions**
1. **The flat "uncore" result reproduces with correct accounting; it was not just a bug [measured].** Package minus the sum of cores stays at 86–88 W from idle up to 32 memory-bound tasks pulling 38 GB/s, and only rises at 66 GB/s (+10 W). Under compute load it *falls* (87.5 → 78.1 W at 64 threads). A linear fit over all runs gives pkg−Σcore = 84.9 + **0.15 W per GB/s**: the package domain barely sees memory traffic. In package terms, ~87 W is a constant SoC floor (I/O die, Infinity Fabric, memory controllers/PHY at fixed FCLK 1.8 GHz / MCLK 2.4 GHz, with no OS-visible deep idle) that dwarfs anything workload-dependent.
2. **The falling uncore under compute load is a modeling artifact [inferred].** Each busy core adds ~2.7 W to its own counter but only ~2.2 W to package (2.4 too). RAPL on this part is a model, and the two domains are not mutually consistent. Do not treat pkg−Σcore as a physical "uncore" measurement.
3. **Memory power shows up at the wall, not in package RAPL [measured].** BMC − pkg (non-package platform power: DIMMs, VRs/PSU losses, fans, NIC, disks) is ~126 W at idle and rises to **142 W at 38 GB/s and 151 W at 66 GB/s**. A fit over memory runs gives **+0.38 W per GB/s**, about 2.5× what package sees. Compute load raises it only modestly (+6 W at burn64, consistent with PSU/VR losses on +100 W of package power). This supports the hypothesis that DIMM power lives outside the package and outside AMD RAPL. The BMC can see it, but only at 1 Hz and system-wide.
4. **HSMP socket power is not an independent signal [measured]:** across all 65 runs, HSMP = 0.9756 × MSR package (sd of the ratio 0.0002). It is the same SMU telemetry, rescaled.
5. **Old code double-counted cores [inferred from data + code]:** summing the per-core MSR over all 64 hardware threads (`sum64_W`) gives exactly 2× the true core sum. Any pkg − Σ(64 threads) "uncore" would go negative under load.
6. **Side observations [measured]:** SMT throughput for `burn` is +33% (burn64 13 451 vs burn32 10 094 iters/s) for +26.6 W package / +29.1 W wall. Idle per-core power is ~0.31 W, so with no deep C-states visible, 32 idle cores cost ~10 W against a ~126 W non-package and ~87 W SoC floor.

### 2.7 Per-DIMM power via HSMP
`experiments/hsmp 0x17 <addr>` returns 0 for every address 0–255 at idle. A recheck under 64× `mem_miss` is scheduled in Phase 4's notes. **[measured at idle]** On this platform the SMU is not fed DIMM PMIC telemetry, so per-DIMM power via HSMP is unavailable.
