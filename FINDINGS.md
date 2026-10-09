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

## Phase 3 — Accounting problems the data shows

Setup: there is no sched_ext on this kernel, so the EFS hooks cannot run. Instead `experiments/taskacct/` is a `tp_btf/sched_switch` BPF program that, on every context switch, calls a new kfunc `read_energy_raw()` (added to `energy_kfunc_module` and registered for TRACING as well as STRUCT_OPS) and reads per-CPU instructions, cycles and DRAM fills (`ls_any_fills_from_sys.dram_io_all`, raw 0x4844). The resulting trace (`data/traces/*.csv.gz`) is replayed offline in `experiments/phase3.py`, both with the exact arithmetic of `efs.bpf.c` (`sched_running`/`sched_stopping`) and with fixed variants. `python3 experiments/analyze.py phase3` regenerates the tables. There are 5 reps each.

### 3.1 SMT siblings double-charge the shared core counter
- **Command:** `sudo python3 experiments/phase3.py smt 5`. `burn` runs on cpu 2 and `mem_miss` on its sibling cpu 34 for 8 s; references are `burn` alone on core 3 and `mem_miss` alone on core 4.
- **Result:** core-2 counter 21.45 ± 0.93 J. EFS-style per-interval charging gives burn 21.45 J + mem 21.57 J = **2.01 ± 0.04 × the energy the core actually used**. The SMT-aware replay (energy between counter reads on either sibling split by run time across both siblings, ≥10 ms windows) sums to 1.00 × the counter (burn 10.90, mem 10.55 J). Alone, the two tasks would have used 18.71 + 13.73 J = 1.51 × the shared figure.
- **Conclusion [measured]:** this is a real bug: with SMT on and both siblings busy, every task's energy is doubled. The fix (split per *physical* core across the busy siblings) removes it exactly. How to split the shared energy "fairly" between siblings is a modeling choice. By run time, burn and mem get ~50/50. By standalone power, they would get 58/42. Nothing on this machine can measure which split is right. **[inferred]**

### 3.2 Sub-millisecond run intervals: quantization and blur
- **Command:** `sudo python3 experiments/phase3.py pingpong 5`. `experiments/workloads/pingpong.c` has two processes pinned to cpu 6 hand a token back and forth: `pp_cpu` (sqrt loop) and `pp_mem` (random 1 GB accesses) each work `work_us` per turn. The reference power per task comes from the 20 ms-turn run: pp_cpu 2.741 W, pp_mem 2.380 W.

| work_us | mean interval µs | intervals with dE=0 | naive pp_cpu W | naive pp_mem W | pp_cpu error | pp_mem error | equal-power split error (cpu) |
|---|---|---|---|---|---|---|---|
| 20000 | 6542 | 32% | 2.74 | 2.38 | 0.0% (ref) | 0.0% (ref) | −6.6% |
| 1000 | 1006 | 0% | 2.53 | 2.60 | −7.6 ± 0.1% | +9.3 ± 0.1% | −6.4% |
| 200 | 208 | 79% | 2.59 | 2.60 | −5.7 ± 1.3% | +9.4 ± 1.4% | −5.3% |
| 50 | 57 | 94% | 2.67 | 2.68 | −2.6 ± 0.6% | +12.6 ± 0.6% | −2.4% |

- **Conclusion [measured]:** once intervals are ≤ 1 ms, the EFS per-interval delta can no longer tell the tasks apart. Both converge to the time-weighted mean (~2.6 W), and the result is no better than ignoring energy and splitting by run time. At 200/50 µs, 79–94% of intervals see no counter change at all, so `efs.bpf.c`'s `if (*prev_energy == cur_energy) return;` skips them and their per-interval "power" (the EMA input) is 0 or a full 1 ms of energy, i.e. noise. Because energy is conserved on average, totals stay roughly right; what disappears is the per-task discrimination. A minimum accumulation window (≥ 10 ms) removes the noise but cannot recover the discrimination; that needs a per-task activity model (Phase 4). Note how small the signal is: a compute-bound and a memory-bound task differ by only ~15% in core power (2.74 vs 2.38 W).

### 3.3 The "uncore" accumulator in efs.bpf.c over-counts by the number of running tasks
- **Command:** `sudo python3 experiments/phase3.py uncore 5`. n `burn` tasks for 6 s, with the `sched_stopping` uncore arithmetic replayed: for every non-idle interval, add `max(0, Δpkg − Δcore)`. The reference is pkg − Σcore from Phase 2 windows at the same n.

| n tasks | EFS-style "uncore" W | reference pkg−Σcore W | over-count |
|---|---|---|---|
| 0 | 58.5 ± 2.5 | 87.5 | 0.67× |
| 1 | 157.8 ± 1.8 | 87.7 | 1.8× |
| 8 | 706.9 ± 205 | 86.4 | 8.2× |
| 32 | 4427 ± 107 | 82.7 | 53.5× |
| 64 | 9404 ± 228 | 78.1 | 120× |

- **Conclusion [measured]:** this is a real bug. Δpkg is socket-wide, so every concurrently running task's interval adds the *whole* package delta again. The accumulator therefore grows at ≈ (number of running tasks) × (package power), which is dominated by the constant ~87 W SoC floor. With a fixed workload its rate is constant whatever the memory activity, which is exactly the "uncore rises at a constant rate regardless of workload" symptom from the first attempt. The fix is to compute pkg − Σcore once per socket on a timer (or in userspace from one counter), never per task interval. Even the correct value is flat (Phase 2.6), so the bug masked a signal that is not there anyway.
- Caveat: the trace-based truth columns in `data/phase3_uncore.csv` (`true_uncore_W`, `pkg_W`, `ratio`) are **wrong**. They summed diffs over a stream merged from many CPUs, where near-simultaneous reads arrive out of order. The script is now fixed (last − first), and the table uses the Phase 2 reference instead.
- Caveat: the first tracer version woke its userspace reader on every event, which created a self-sustaining ~230k switches/s storm (1.4 M events in 6 s at "idle"). That inflates the n = 0 row. The tracer now uses `BPF_RB_NO_WAKEUP` with a 20 ms timed consume. Phase 3.1/3.2 results are per-CPU and unaffected.

### 3.4 Other defects found by reading the code (not separately measured)
- `read_core_energy()` called `pr_info` twice per invocation, i.e. twice per context switch in EFS, which floods the kernel log and adds overhead to every switch. **Removed.**
- There is no 32-bit wrap handling: µJ values derived from a 32-bit counter wrap at 65.5 kJ (the package counter wraps every 3.6–11 min at 100–300 W), producing a huge `u64` delta on wrap. The new `read_energy_raw()` returns raw 32-bit values so callers can use `(u32)(cur − prev)`.
- The EMA (α = ½) is applied to per-interval power whose inputs are quantized to 1 ms of energy (3.2). It cannot settle for tasks with short intervals, which plausibly explains TODO #1 in the README ("rolling pid_to_power keeps increasing") **[inferred]**.
- Kernel-thread intervals are skipped (`PF_KTHREAD`), so their energy goes to no one; idle intervals are likewise dropped. That is fine for ordering decisions, but per-task sums will not add up to the counter.

### Fix status
Fixes are implemented and tested in the offline replay (`phase3.py: smt_aware`), not in `efs.bpf.c`, which cannot be loaded here. They are: per-physical-core attribution split across busy siblings; ≥ 10 ms accumulation window; raw counters with wrap handling; socket-level uncore computed once rather than per task; printk removed from the kfunc. Porting them into `efs.bpf.c` is straightforward, but needs a ≥ 6.12 kernel to test.
