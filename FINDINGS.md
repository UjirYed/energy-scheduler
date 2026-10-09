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
- **Recommendation for later:** installing the 6.17 or HWE 7.0 kernel (needs a reboot, so I asked first and did not do it) would restore sched_ext and `power_core`. Everything in Phases 2–5 below is done from userspace and a tracing BPF program, so it does not depend on that.
