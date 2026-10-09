// Fixed per-task energy accounting, runnable from tp_btf/sched_switch (no sched_ext needed).
// Designed to be lifted into efs.bpf.c's running/stopping hooks. Fixes vs efs.bpf.c:
//  1. Energy is accounted per *physical core* (RAPL core counter is shared by SMT siblings) and
//     split across the busy time of both siblings -> no double counting.
//  2. The counter is sampled at most once per >=10 ms window per core; each window yields a rate
//     (energy per busy-ns, after subtracting an idle baseline) and tasks are charged runtime x rate.
//     No 1 ms quantization noise, no zero-delta skips.
//  3. Raw 32-bit counters with wrap-safe deltas.
//  4. Per-task instructions / cycles / DRAM fills (Phase 4 attribution signals).
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

extern u64 read_energy_raw(void) __ksym;
#define NCORE 32
#define WIN_NS 10000000ULL
const volatile __u64 idle_mw_per_thread = 105; /* measured idle core power 0.21 W / 2 threads */

struct efs_core_state {
	struct bpf_spin_lock lock;
	u32 last_raw, have_raw;
	u64 win_start, busy_ns, rate_uw;          /* rate in uW == pJ/ns ... stored as nJ per ms busy */
	u64 acct_from[2], run_start[2]; u32 busy[2]; /* per sibling: busy-accounting start, switch-in time, non-idle */
	u64 energy_nj_total, attributed_nj;       /* checks */
};
struct efs_task_stats { u64 energy_nj, runtime_ns, instr, cycles, dram, nsw; };
struct efs_cpu_pmc { u64 instr, cycles, dram; };

struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, NCORE); __type(key, u32); __type(value, struct efs_core_state); } cores SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_HASH); __uint(max_entries, 65536); __type(key, u32); __type(value, struct efs_task_stats); } tasks SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct efs_cpu_pmc); } last_pmc SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY); __uint(key_size, 4); __uint(value_size, 4); } ev_instr SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY); __uint(key_size, 4); __uint(value_size, 4); } ev_cycles SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY); __uint(key_size, 4); __uint(value_size, 4); } ev_dram SEC(".maps");

static __always_inline u64 rd(void *map)
{
	struct bpf_perf_event_value v = {};
	if (bpf_perf_event_read_value(map, BPF_F_CURRENT_CPU, &v, sizeof(v))) return 0;
	return v.counter;
}

SEC("tp_btf/sched_switch")
int BPF_PROG(on_switch, bool preempt, struct task_struct *prev, struct task_struct *next)
{
	u32 cpu = bpf_get_smp_processor_id(), core = cpu % NCORE, t = cpu / NCORE & 1, z = 0;
	u64 now = bpf_ktime_get_ns();
	struct efs_core_state *cs = bpf_map_lookup_elem(&cores, &core);
	struct efs_cpu_pmc *lp = bpf_map_lookup_elem(&last_pmc, &z);
	if (!cs || !lp) return 0;
	u64 raw = read_energy_raw();   /* read outside the lock; only used if we close a window */
	u64 ins = rd(&ev_instr), cyc = rd(&ev_cycles), dr = rd(&ev_dram);
	u64 run = 0, rate = 0, charge_from;

	bpf_spin_lock(&cs->lock);
	if (!cs->have_raw) { cs->last_raw = (u32)raw; cs->have_raw = 1; cs->win_start = now; cs->acct_from[0] = cs->acct_from[1] = now; cs->run_start[0] = cs->run_start[1] = now; }
	charge_from = cs->run_start[t]; cs->run_start[t] = now;
	if (cs->busy[t] && now > cs->acct_from[t]) { run = now - cs->acct_from[t]; cs->busy_ns += run; }
	cs->acct_from[t] = now;
	if (now - cs->win_start >= WIN_NS) {
		/* include the sibling's in-progress run in this window */
		u32 o = t ^ 1;
		if (cs->busy[o] && now > cs->acct_from[o]) { cs->busy_ns += now - cs->acct_from[o]; cs->acct_from[o] = now; }
		u64 win = now - cs->win_start;
		u64 de = (((u64)((u32)raw - cs->last_raw)) * 1000000000ULL) >> 16;  /* nJ (ESU=16) */
		u64 idle_ns = 2 * win > cs->busy_ns ? 2 * win - cs->busy_ns : 0;
		u64 idle_nj = idle_ns * idle_mw_per_thread / 1000000ULL;            /* ns * mW = pJ ... /1e6 -> nJ */
		u64 busy_nj = de > idle_nj ? de - idle_nj : 0;
		if (cs->busy_ns > 100000) cs->rate_uw = busy_nj * 1000000ULL / cs->busy_ns; /* nJ/ns*1e6 = uW */
		cs->energy_nj_total += de;
		cs->last_raw = (u32)raw; cs->win_start = now; cs->busy_ns = 0;
	}
	rate = cs->rate_uw;
	cs->busy[t] = next->pid != 0;
	bpf_spin_unlock(&cs->lock);

	if (prev->pid != 0) {
		u32 pid = prev->pid;
		struct efs_task_stats *ts = bpf_map_lookup_elem(&tasks, &pid);
		if (!ts) { struct efs_task_stats n = {}; bpf_map_update_elem(&tasks, &pid, &n, BPF_NOEXIST); ts = bpf_map_lookup_elem(&tasks, &pid); }
		if (ts) {
			/* charge the whole run since the task's last switch-in (run may span window closes) */
			u64 full = now - charge_from;
			__sync_fetch_and_add(&ts->energy_nj, full * rate / 1000000ULL);
			__sync_fetch_and_add(&ts->runtime_ns, full);
			__sync_fetch_and_add(&ts->instr, ins - lp->instr);
			__sync_fetch_and_add(&ts->cycles, cyc - lp->cycles);
			__sync_fetch_and_add(&ts->dram, dr - lp->dram);
			__sync_fetch_and_add(&ts->nsw, 1);
		}
	}
	lp->instr = ins; lp->cycles = cyc; lp->dram = dr;
	return 0;
}
char LICENSE[] SEC("license") = "GPL";
