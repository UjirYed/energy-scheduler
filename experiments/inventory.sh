#!/bin/bash
# Phase 1 inventory. Usage: sudo experiments/inventory.sh > data/inventory.txt 2>&1
sec(){ echo; echo "===== $* ====="; }
sec date; date -u
sec kernel; uname -a; cat /etc/os-release | head -4
sec sched_ext; ls /sys/kernel/sched_ext 2>&1; grep -E 'CONFIG_SCHED_CLASS_EXT|CONFIG_BPF_JIT=|CONFIG_DEBUG_INFO_BTF=' /boot/config-$(uname -r)
sec cmdline; cat /proc/cmdline
sec cpu; lscpu
sec smt; cat /sys/devices/system/cpu/smt/control /sys/devices/system/cpu/smt/active
sec cpufreq; for f in scaling_driver scaling_governor scaling_available_governors energy_performance_preference energy_performance_available_preferences scaling_min_freq scaling_max_freq cpuinfo_min_freq cpuinfo_max_freq amd_pstate_highest_perf amd_pstate_max_freq amd_pstate_lowest_nonlinear_freq amd_pstate_prefcore_ranking scaling_available_frequencies; do printf "%s: " $f; cat /sys/devices/system/cpu/cpu0/cpufreq/$f 2>&1; done
cat /sys/devices/system/cpu/amd_pstate/status 2>&1; cat /sys/devices/system/cpu/cpufreq/boost 2>&1
sec cpuidle; cat /sys/devices/system/cpu/cpuidle/current_driver /sys/devices/system/cpu/cpuidle/current_governor* 2>&1
for s in /sys/devices/system/cpu/cpu0/cpuidle/state*; do echo "$s: $(cat $s/name) desc=$(cat $s/desc) latency=$(cat $s/latency)us residency=$(cat $s/residency)us disable=$(cat $s/disable)"; done
sec topology; lscpu -e=CPU,CORE,SOCKET,NODE,CACHE
for c in 0 1; do for i in 0 1 2 3; do d=/sys/devices/system/cpu/cpu$c/cache/index$i; echo "cpu$c idx$i L$(cat $d/level) $(cat $d/type) $(cat $d/size) shared=$(cat $d/shared_cpu_list)"; done; done
echo "L3 groups:"; cat /sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list | sort -u
echo "siblings:"; cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | tr '\n' ' '; echo
numactl -H 2>&1
sec resctrl; grep -o -w -E 'cqm|cqm_llc|cqm_occup_llc|cqm_mbm_total|cqm_mbm_local|rdt_a|mba|smba|bmec|cat_l3|cdp_l3' /proc/cpuinfo | sort | uniq -c
grep -i resctrl /proc/filesystems; mount | grep resctrl
grep -E 'CONFIG_X86_CPU_RESCTRL|CONFIG_PERF_EVENTS_AMD_UNCORE|CONFIG_PERF_EVENTS_INTEL_RAPL|CONFIG_AMD_HSMP|CONFIG_SENSORS_AMD_ENERGY' /boot/config-$(uname -r)
sec perf_pmus; ls /sys/bus/event_source/devices/; for p in power power_core; do echo "-- $p"; ls /sys/bus/event_source/devices/$p/events 2>&1; done
sec hsmp; ls /dev/hsmp* 2>&1; modinfo amd_hsmp 2>&1 | head -3; modinfo hsmp_acpi 2>&1 | head -3
sec hwmon; for h in /sys/class/hwmon/hwmon*; do echo "$h $(cat $h/name)"; done
sec msr; modprobe msr; rdmsr -p0 0xC0010299 2>&1; rdmsr -p0 0xC001029A 2>&1; rdmsr -p0 0xC001029B 2>&1
sec bmc; which ipmitool; modprobe ipmi_devintf ipmi_si 2>&1; ls /dev/ipmi* 2>&1; timeout 20 ipmitool dcmi power reading 2>&1
sec tools; for t in turbostat perf bpftool clang gcc make cpupower stress-ng numactl msr-tools; do printf "%s: " $t; which $t 2>&1 || echo missing; done
ls /lib/modules/$(uname -r)/build >/dev/null 2>&1 && echo "kernel headers: present" || echo "kernel headers: missing"
ls -la /sys/kernel/btf/vmlinux
