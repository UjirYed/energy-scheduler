"""Shared measurement helpers: MSR window sampler, BMC (DCMI) poller, HSMP socket-power poller, workload launchers."""
import fcntl, os, struct, subprocess, threading, time, csv, io, random
HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, 'bin')
DATA = os.path.join(os.path.dirname(HERE), 'data')
NCORE, NCPU = 32, 64
CCX = [list(range(i, i + 4)) for i in range(0, 32, 4)]  # cores per L3; siblings are +32

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True, **kw).stdout

class Poller(threading.Thread):
    """Calls fn() every period seconds, stores (t, value) samples."""
    def __init__(self, fn, period):
        super().__init__(daemon=True); self.fn, self.period, self.samples, self.stop_ev = fn, period, [], threading.Event()
    def run(self):
        while not self.stop_ev.is_set():
            t = time.monotonic()
            try: self.samples.append((t, self.fn()))
            except Exception as e: pass
            self.stop_ev.wait(max(0, self.period - (time.monotonic() - t)))
    def stop(self): self.stop_ev.set(); self.join()
    def mean(self, t0=None, t1=None):
        v = [x for t, x in self.samples if (t0 is None or t >= t0) and (t1 is None or t <= t1)]
        return sum(v) / len(v) if v else float('nan')

def bmc_watts():
    out = subprocess.run(['ipmitool', 'dcmi', 'power', 'reading'], capture_output=True, text=True, timeout=5).stdout
    for line in out.splitlines():
        if 'Instantaneous power reading' in line:
            return float(line.split(':')[1].split()[0])
    raise RuntimeError('no reading')

_hsmp_fd = None
HSMP_IOCTL = (3 << 30) | (44 << 16) | (0xF8 << 8) | 0
def hsmp(msg_id, args=(), nresp=1):
    global _hsmp_fd
    if _hsmp_fd is None: _hsmp_fd = os.open('/dev/hsmp', os.O_RDWR)
    a = list(args) + [0] * (8 - len(args))
    buf = bytearray(struct.pack('<IHH8IH2x', msg_id, len(args), nresp, *a, 0))
    fcntl.ioctl(_hsmp_fd, HSMP_IOCTL, buf)
    return struct.unpack('<IHH8IH2x', bytes(buf))[3:3 + nresp]

def hsmp_socket_w(): return hsmp(4)[0] / 1000.0
def hsmp_ddr_bw():  # (max GB/s, used GB/s, pct)
    r = hsmp(0x14)[0]; return (r >> 20, (r >> 8) & 0xfff, r & 0xff)

def rapl_window(secs):
    """Returns dict: core_J[cpu], aperf[cpu], mperf[cpu], pkg_J, secs."""
    out = sh(f'{BIN}/rapl window {secs}')
    r = {'core_J': {}, 'aperf': {}, 'mperf': {}}
    for row in csv.DictReader(io.StringIO(out)):
        if row['cpu'] == 'pkg': r['pkg_J'], r['secs'] = float(row['core_J']), float(row['aperf'])
        else:
            c = int(row['cpu']); r['core_J'][c] = float(row['core_J']); r['aperf'][c] = int(row['aperf']); r['mperf'][c] = int(row['mperf'])
    return r

def launch(kind, cpus, secs, mem_args=None):
    """Start one pinned instance of a workload per cpu. Returns list of Popen."""
    procs = []
    for c in cpus:
        if kind == 'burn': cmd = [f'{BIN}/burn', str(secs)]
        elif kind == 'mem': cmd = [f'{BIN}/mem_miss'] + (mem_args or ['2048', '1024', '0', '1', '64', '50']) + [str(secs)]
        else: raise ValueError(kind)
        procs.append(subprocess.Popen(['taskset', '-c', str(c)] + cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True))
    return procs

def wait_all(procs):
    return [p.communicate()[0] for p in procs]
