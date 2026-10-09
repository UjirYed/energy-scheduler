// RAPL/APERF/MPERF sampler via /dev/cpu/N/msr.
// Build: gcc -O2 -o experiments/bin/rapl experiments/rapl.c -lm
//
//   rapl interval <cpu> <msr_hex> <seconds>
//       Busy-poll one MSR on <cpu>; print one CSV row per observed change:
//       t_ns,delta_counts   (t from CLOCK_MONOTONIC, delta_counts after wrap fix)
//   rapl window <seconds>
//       Snapshot all CPUs at start/end. Prints CSV rows:
//       cpu,core_J,aperf,mperf,tsc_delta  and a final row: pkg,<J>,<seconds>
//       Pkg counter is polled every 100 ms for wrap safety.
//   rapl series <seconds> <period_ms>
//       Every period prints: t_abs (CLOCK_MONOTONIC s),t_s,pkg_J,core0_J,...,coreN_J (one thread per core: cpu 0..ncores-1)
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>

#define MSR_UNIT 0xC0010299
#define MSR_CORE 0xC001029A
#define MSR_PKG  0xC001029B
#define MSR_MPERF 0xE7
#define MSR_APERF 0xE8
#define MAXCPU 512
static int fds[MAXCPU]; static int ncpu; static double unit;

static uint64_t rd(int cpu, uint32_t msr) {
	uint64_t v; if (pread(fds[cpu], &v, 8, msr) != 8) { perror("pread"); exit(1); } return v;
}
static uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec*1000000000ull + t.tv_nsec; }
static uint64_t d32(uint64_t a, uint64_t b) { return (uint32_t)((uint32_t)b - (uint32_t)a); }

int main(int argc, char **argv) {
	char p[64];
	for (ncpu = 0; ncpu < MAXCPU; ncpu++) { snprintf(p, 64, "/dev/cpu/%d/msr", ncpu); fds[ncpu] = open(p, O_RDONLY); if (fds[ncpu] < 0) break; }
	unit = 1.0 / (1 << ((rd(0, MSR_UNIT) >> 8) & 0x1f));
	if (argc >= 5 && !strcmp(argv[1], "interval")) {
		int cpu = atoi(argv[2]); uint32_t msr = strtoul(argv[3], 0, 16); double secs = atof(argv[4]);
		uint64_t t0 = now_ns(), end = t0 + secs*1e9, last = rd(cpu, msr), t;
		printf("t_ns,delta_counts,unit_J\n");
		while ((t = now_ns()) < end) { uint64_t v = rd(cpu, msr); if ((uint32_t)v != (uint32_t)last) { printf("%llu,%llu,%.9g\n", (unsigned long long)(t - t0), (unsigned long long)d32(last, v), unit); last = v; } }
		return 0;
	}
	if (argc >= 3 && !strcmp(argv[1], "window")) {
		double secs = atof(argv[2]);
		uint64_t c0[MAXCPU], a0[MAXCPU], m0[MAXCPU], c1, a1, m1;
		uint64_t t0 = now_ns(), pk = rd(0, MSR_PKG), pkacc = 0;
		for (int i = 0; i < ncpu; i++) { c0[i] = rd(i, MSR_CORE); a0[i] = rd(i, MSR_APERF); m0[i] = rd(i, MSR_MPERF); }
		uint64_t end = t0 + secs*1e9;
		while (now_ns() < end) { usleep(100000); uint64_t v = rd(0, MSR_PKG); pkacc += d32(pk, v); pk = v; }
		uint64_t t1 = now_ns();
		printf("cpu,core_J,aperf,mperf\n");
		for (int i = 0; i < ncpu; i++) { c1 = rd(i, MSR_CORE); a1 = rd(i, MSR_APERF); m1 = rd(i, MSR_MPERF);
			printf("%d,%.6f,%llu,%llu\n", i, d32(c0[i], c1)*unit, (unsigned long long)(a1-a0[i]), (unsigned long long)(m1-m0[i])); }
		printf("pkg,%.6f,%.6f,0\n", pkacc*unit, (t1-t0)/1e9);
		return 0;
	}
	if (argc >= 4 && !strcmp(argv[1], "series")) {
		double secs = atof(argv[2]); int per = atoi(argv[3]); int nc = ncpu / 2; // assumes SMT siblings are n+ncpu/2
		if (argc >= 5) nc = atoi(argv[4]);
		uint64_t pc[MAXCPU], pp = rd(0, MSR_PKG), t0 = now_ns();
		for (int i = 0; i < nc; i++) pc[i] = rd(i, MSR_CORE);
		printf("t_abs,t_s,pkg_J"); for (int i = 0; i < nc; i++) printf(",core%d_J", i); printf("\n");
		uint64_t next = t0;
		while (1) { next += per*1000000ull; uint64_t n = now_ns(); if (next > n) { struct timespec ts = {(next-n)/1000000000ull, (next-n)%1000000000ull}; nanosleep(&ts, 0);} 
			uint64_t t = now_ns(); if (t - t0 > secs*1e9) break;
			uint64_t v = rd(0, MSR_PKG); printf("%.6f,%.6f,%.6f", t/1e9, (t-t0)/1e9, d32(pp, v)*unit); pp = v;
			for (int i = 0; i < nc; i++) { uint64_t c = rd(i, MSR_CORE); printf(",%.6f", d32(pc[i], c)*unit); pc[i] = c; }
			printf("\n"); fflush(stdout); }
		return 0;
	}
	fprintf(stderr, "usage: see source\n"); return 1;
}
