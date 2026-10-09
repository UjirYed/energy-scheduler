// Per-context-switch energy + PMU sampler (no sched_ext needed).
// On every sched_switch, emits the calling CPU's raw RAPL core/pkg counters (via the
// read_energy_raw kfunc from energy_kfunc_module) and its instructions/cycles/DRAM-fill
// counts. Userspace (taskacct.c) writes them to CSV; accounting variants are computed offline.
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

extern u64 read_energy_raw(void) __ksym;

struct { __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY); __uint(key_size, 4); __uint(value_size, 4); } ev_instr SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY); __uint(key_size, 4); __uint(value_size, 4); } ev_cycles SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY); __uint(key_size, 4); __uint(value_size, 4); } ev_dram SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_RINGBUF); __uint(max_entries, 256 << 20); } rb SEC(".maps");

struct event {
	u64 ts;
	u32 cpu, prev_pid, next_pid, prev_state;
	u32 core_raw, pkg_raw;
	u64 instr, cycles, dram;
	char prev_comm[16];
};

__u64 dropped = 0;

static __always_inline u64 rd(void *map)
{
	struct bpf_perf_event_value v = {};
	if (bpf_perf_event_read_value(map, BPF_F_CURRENT_CPU, &v, sizeof(v)))
		return 0;
	return v.counter;
}

SEC("tp_btf/sched_switch")
int BPF_PROG(on_switch, bool preempt, struct task_struct *prev, struct task_struct *next)
{
	struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e) { __sync_fetch_and_add(&dropped, 1); return 0; }
	u64 raw = read_energy_raw();
	e->ts = bpf_ktime_get_ns();
	e->cpu = bpf_get_smp_processor_id();
	e->prev_pid = prev->pid;
	e->next_pid = next->pid;
	e->prev_state = prev->__state;
	e->core_raw = (u32)raw;
	e->pkg_raw = (u32)(raw >> 32);
	e->instr = rd(&ev_instr);
	e->cycles = rd(&ev_cycles);
	e->dram = rd(&ev_dram);
	__builtin_memcpy(e->prev_comm, prev->comm, 16);
	bpf_ringbuf_submit(e, BPF_RB_NO_WAKEUP); // consumer polls on a timeout; waking it per event caused a self-sustaining switch storm
	return 0;
}

char LICENSE[] SEC("license") = "GPL";
