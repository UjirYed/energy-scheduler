// Minimal HSMP query tool. Build: gcc -O2 -o experiments/hsmp experiments/hsmp.c
// Usage: sudo ./hsmp <msg_id> [arg0 ...]   prints response words in hex and decimal.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <asm/amd_hsmp.h>

int hsmp_call(int fd, int id, int nargs, const unsigned *args, int nresp, unsigned *resp) {
	struct hsmp_message m; memset(&m, 0, sizeof m);
	m.msg_id = id; m.num_args = nargs; m.response_sz = nresp; m.sock_ind = 0;
	for (int i = 0; i < nargs; i++) m.args[i] = args[i];
	if (ioctl(fd, HSMP_IOCTL_CMD, &m) < 0) return -1;
	for (int i = 0; i < nresp; i++) resp[i] = m.args[i];
	return 0;
}
#ifndef HSMP_LIB
int main(int argc, char **argv) {
	if (argc < 2) { fprintf(stderr, "usage: %s msg_id [args]\n", argv[0]); return 1; }
	int fd = open("/dev/hsmp", O_RDWR); if (fd < 0) { perror("/dev/hsmp"); return 1; }
	int id = strtol(argv[1], 0, 0); unsigned args[8] = {0}; int n = argc - 2;
	for (int i = 0; i < n; i++) args[i] = strtoul(argv[i + 2], 0, 0);
	int nresp = hsmp_msg_desc_table[id].response_sz;
	unsigned r[8];
	if (hsmp_call(fd, id, n, args, nresp, r)) { perror("ioctl"); return 2; }
	for (int i = 0; i < nresp; i++) printf("resp[%d]=0x%08x (%u)\n", i, r[i], r[i]);
	return 0;
}
#endif
