// Loader for taskacct.bpf.c. Usage: sudo taskacct <seconds> <out.csv>
// CSV columns: ts_ns,cpu,prev_pid,next_pid,prev_state,core_raw,pkg_raw,instr,cycles,dram_fills,prev_comm
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <linux/perf_event.h>
#include <sys/syscall.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>
#include "taskacct.skel.h"

struct event { unsigned long long ts; unsigned cpu, prev_pid, next_pid, prev_state, core_raw, pkg_raw;
	unsigned long long instr, cycles, dram; char prev_comm[16]; };
static FILE *out; static long n;

static int handle(void *ctx, void *data, size_t sz) {
	struct event *e = data; char comm[17]; memcpy(comm, e->prev_comm, 16); comm[16] = 0;
	for (char *c = comm; *c; c++) if (*c == ',') *c = '_';
	fprintf(out, "%llu,%u,%u,%u,%u,%u,%u,%llu,%llu,%llu,%s\n", e->ts, e->cpu, e->prev_pid, e->next_pid, e->prev_state,
		e->core_raw, e->pkg_raw, e->instr, e->cycles, e->dram, comm);
	n++; return 0;
}

static int open_ev(int cpu, unsigned type, unsigned long long config) {
	struct perf_event_attr a = { .size = sizeof(a), .type = type, .config = config };
	return syscall(__NR_perf_event_open, &a, -1, cpu, -1, 0);
}

int main(int argc, char **argv) {
	if (argc < 3) { fprintf(stderr, "usage: %s seconds out.csv\n", argv[0]); return 1; }
	double secs = atof(argv[1]); out = fopen(argv[2], "w");
	struct taskacct_bpf *skel = taskacct_bpf__open_and_load();
	if (!skel) { fprintf(stderr, "load failed\n"); return 1; }
	int ncpu = libbpf_num_possible_cpus();
	for (int c = 0; c < ncpu; c++) {
		int fi = open_ev(c, PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS);
		int fc = open_ev(c, PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES);
		int fd = open_ev(c, PERF_TYPE_RAW, 0x4844); /* ls_any_fills_from_sys.dram_io_all */
		if (fi < 0 || fc < 0 || fd < 0) { perror("perf_event_open"); return 1; }
		bpf_map_update_elem(bpf_map__fd(skel->maps.ev_instr), &c, &fi, 0);
		bpf_map_update_elem(bpf_map__fd(skel->maps.ev_cycles), &c, &fc, 0);
		bpf_map_update_elem(bpf_map__fd(skel->maps.ev_dram), &c, &fd, 0);
	}
	struct ring_buffer *rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle, NULL, NULL);
	fprintf(out, "ts_ns,cpu,prev_pid,next_pid,prev_state,core_raw,pkg_raw,instr,cycles,dram_fills,prev_comm\n");
	if (taskacct_bpf__attach(skel)) { fprintf(stderr, "attach failed\n"); return 1; }
	struct timespec t0, t; clock_gettime(CLOCK_MONOTONIC, &t0);
	do { ring_buffer__poll(rb, 100); clock_gettime(CLOCK_MONOTONIC, &t); }
	while ((t.tv_sec - t0.tv_sec) + (t.tv_nsec - t0.tv_nsec) / 1e9 < secs);
	taskacct_bpf__detach(skel);
	ring_buffer__consume(rb);
	fprintf(stderr, "events=%ld dropped=%llu\n", n, (unsigned long long)skel->bss->dropped);
	fclose(out); return 0;
}
