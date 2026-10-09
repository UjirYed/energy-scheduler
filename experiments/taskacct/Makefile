# Requires: clang, bpftool, libbpf-dev; and energy_kfunc_module loaded (provides read_energy_raw).
all: ../bin/taskacct
taskacct.bpf.o: taskacct.bpf.c vmlinux.h
	clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -c $< -o $@
taskacct.skel.h: taskacct.bpf.o
	bpftool gen skeleton $< > $@
../bin/taskacct: taskacct.c taskacct.skel.h
	gcc -O2 -Wall -o $@ taskacct.c -lbpf -lelf -lz
clean:
	rm -f taskacct.bpf.o taskacct.skel.h ../bin/taskacct
