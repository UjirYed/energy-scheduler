// Loader for efsfix.bpf.c. Usage: sudo efsfix <seconds> <out.csv>
// Writes per-task: pid,comm,energy_J,runtime_s,instr,cycles,dram_fills,nswitch ; and per-core totals to stderr.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <linux/perf_event.h>
#include <sys/syscall.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>
#include "efsfix.skel.h"
struct task_stats { unsigned long long energy_nj, runtime_ns, instr, cycles, dram, nsw; };
static int open_ev(int cpu, unsigned type, unsigned long long config) {
	struct perf_event_attr a = { .size = sizeof(a), .type = type, .config = config };
	return syscall(__NR_perf_event_open, &a, -1, cpu, -1, 0);
}
int main(int argc, char **argv) {
	if (argc < 3) { fprintf(stderr, "usage: %s seconds out.csv\n", argv[0]); return 1; }
	struct efsfix_bpf *s = efsfix_bpf__open_and_load();
	if (!s) { fprintf(stderr, "load failed\n"); return 1; }
	int ncpu = libbpf_num_possible_cpus();
	for (int c = 0; c < ncpu; c++) {
		int fi = open_ev(c, PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS), fc = open_ev(c, PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES), fd = open_ev(c, PERF_TYPE_RAW, 0x4844);
		bpf_map_update_elem(bpf_map__fd(s->maps.ev_instr), &c, &fi, 0);
		bpf_map_update_elem(bpf_map__fd(s->maps.ev_cycles), &c, &fc, 0);
		bpf_map_update_elem(bpf_map__fd(s->maps.ev_dram), &c, &fd, 0);
	}
	if (efsfix_bpf__attach(s)) { fprintf(stderr, "attach failed\n"); return 1; }
	usleep((useconds_t)(atof(argv[1]) * 1e6));
	efsfix_bpf__detach(s);
	FILE *o = fopen(argv[2], "w");
	fprintf(o, "pid,comm,energy_J,runtime_s,instr,cycles,dram_fills,nswitch\n");
	unsigned key = 0, next; struct task_stats v; int fd = bpf_map__fd(s->maps.tasks);
	unsigned *kp = NULL;
	while (bpf_map_get_next_key(fd, kp, &next) == 0) {
		if (bpf_map_lookup_elem(fd, &next, &v) == 0) {
			char path[64], comm[32] = "?"; snprintf(path, 64, "/proc/%u/comm", next);
			FILE *f = fopen(path, "r"); if (f) { if (fgets(comm, 32, f)) comm[strcspn(comm, "\n")] = 0; fclose(f); }
			for (char *c = comm; *c; c++) if (*c == ',') *c = '_';
			fprintf(o, "%u,%s,%.6f,%.6f,%llu,%llu,%llu,%llu\n", next, comm, v.energy_nj / 1e9, v.runtime_ns / 1e9, v.instr, v.cycles, v.dram, v.nsw);
		}
		key = next; kp = &key;
	}
	fclose(o);
	return 0;
}
