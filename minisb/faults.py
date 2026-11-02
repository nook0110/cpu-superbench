import os
import threading
import time
from pathlib import Path

MIB = 1024 * 1024
CGROUP_ROOT = Path("/sys/fs/cgroup/minisb")
CPU_PERIOD_US = 10000
UNCORE = Path("/sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00")

FAULTS = {
    "none": ("none", "Без ограничений"),
    "cpu_quota": ("cpu", "cgroup cpu.max: доля одного CPU, доступная тестам, период 10 мс"),
    "cpu_freq": ("cpu", "scaling_max_freq тестового CPU, МГц"),
    "mem_high": ("memory", "cgroup memory.high группы тестов, МиБ"),
    "mem_uncore": ("memory", "потолок частоты uncore (L3 и путь к памяти) для всего процессора, МГц"),
    "disk_iops": ("disk", "cgroup io.max: riops и wiops на устройстве с рабочим каталогом"),
    "disk_bps": ("disk", "cgroup io.max: rbps и wbps на устройстве с рабочим каталогом, МиБ/с"),
    "net_delay": ("network", "netem: задержка на loopback"),
    "net_loss": ("network", "netem: потеря пакетов на loopback"),
    "net_rate": ("network", "netem: ограничение скорости на loopback"),
}
NETEM = {"net_delay": "delay {}", "net_loss": "loss {}", "net_rate": "rate {}"}


def parse(spec):
    kind, _, level = spec.partition(":")
    if kind not in FAULTS:
        raise ValueError(f"unknown fault {kind!r}; known: {', '.join(FAULTS)}")
    if kind != "none" and not level:
        raise ValueError(f"fault {kind!r} needs a level, e.g. {kind}:<value>")
    return kind, level


def needs_root(spec):
    return parse(spec)[0] not in ("none", *NETEM)


def device_of(path):
    number = os.stat(path).st_dev
    return f"{os.major(number)}:{os.minor(number)}"


def read_flat(path):
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(" ")
        if value.strip().lstrip("-").isdigit():
            result[key] = int(value)
    return result


def read_io_stat(path, device):
    for line in path.read_text().splitlines() if path.exists() else []:
        fields = line.split()
        if fields and fields[0] == device:
            return {key: int(value) for key, value in (field.split("=") for field in fields[1:])}
    return {}


def remove_cgroup(path, attempts=50):
    for _ in range(attempts):
        if not path.exists():
            return
        try:
            path.rmdir()
            return
        except OSError:
            for pid in (path / "cgroup.procs").read_text().split():
                try:
                    os.kill(int(pid), 9)
                except ProcessLookupError:
                    pass
            time.sleep(0.1)
    path.rmdir()


def freq_path(cpu, name):
    return Path(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/{name}")


def reset(cpus):
    for cpu in cpus:
        path = freq_path(cpu, "scaling_max_freq")
        if path.exists():
            path.write_text(freq_path(cpu, "cpuinfo_max_freq").read_text())
    if (UNCORE / "max_freq_khz").exists():
        (UNCORE / "max_freq_khz").write_text((UNCORE / "initial_max_freq_khz").read_text())
    if CGROUP_ROOT.exists():
        for child in CGROUP_ROOT.iterdir():
            if child.is_dir():
                remove_cgroup(child)


class FrequencySampler:
    def __init__(self, path, interval=0.05):
        self.path = path
        self.interval = interval
        self.values = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(self.interval):
            self.values.append(int(self.path.read_text()) / 1000)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        return False


def mean(sampler):
    if sampler is None or not sampler.values:
        return None
    return sum(sampler.values) / len(sampler.values)


class Fault:
    def __init__(self, spec, probe_cpu, workdir, isolate):
        self.spec = spec
        self.kind, self.level = parse(spec)
        self.subsystem, self.description = FAULTS[self.kind]
        self.netem = NETEM[self.kind].format(self.level) if self.kind in NETEM else ""
        self.probe_cpu = probe_cpu
        self.workdir = Path(workdir)
        self.isolate = isolate
        self.cgroup = CGROUP_ROOT / f"episode-{os.getpid()}-{time.time_ns()}" if isolate else None
        self.device = None
        self.saved_freq = None
        self.saved_uncore = None
        self.sampler = FrequencySampler(freq_path(probe_cpu, "scaling_cur_freq"))
        uncore_now = UNCORE / "current_freq_khz"
        self.uncore_sampler = FrequencySampler(uncore_now) if os.access(uncore_now, os.R_OK) else None
        self.evidence = {}

    def limits(self):
        if self.kind == "cpu_quota":
            return {"cpu.max": f"{round(float(self.level) * CPU_PERIOD_US)} {CPU_PERIOD_US}"}
        if self.kind == "mem_high":
            return {"memory.high": str(int(float(self.level) * MIB))}
        if self.kind == "disk_iops":
            value = int(float(self.level))
            return {"io.max": f"{self.device} riops={value} wiops={value}"}
        if self.kind == "disk_bps":
            value = int(float(self.level) * MIB)
            return {"io.max": f"{self.device} rbps={value} wbps={value}"}
        return {}

    def __enter__(self):
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.device = device_of(self.workdir)
        if self.isolate:
            CGROUP_ROOT.mkdir(exist_ok=True)
            (CGROUP_ROOT / "cgroup.subtree_control").write_text("+cpu +io +memory")
            self.cgroup.mkdir()
            for name, value in self.limits().items():
                (self.cgroup / name).write_text(value)
        if self.kind == "cpu_freq":
            path = freq_path(self.probe_cpu, "scaling_max_freq")
            self.saved_freq = path.read_text().strip()
            path.write_text(str(int(float(self.level) * 1000)))
        if self.kind == "mem_uncore":
            path = UNCORE / "max_freq_khz"
            self.saved_uncore = path.read_text().strip()
            path.write_text(str(int(float(self.level) * 1000)))
        self.sampler.__enter__()
        if self.uncore_sampler:
            self.uncore_sampler.__enter__()
        return self

    def collect(self):
        evidence = {
            "netem": self.netem,
            "mean_frequency_mhz": mean(self.sampler),
            "mean_uncore_frequency_mhz": mean(self.uncore_sampler),
        }
        if self.cgroup is not None:
            evidence["limits"] = {name: (self.cgroup / name).read_text().strip() for name in self.limits()}
            evidence["cpu_stat"] = read_flat(self.cgroup / "cpu.stat")
            evidence["memory_events"] = read_flat(self.cgroup / "memory.events")
            memory_stat = read_flat(self.cgroup / "memory.stat")
            evidence["memory_stat"] = {key: memory_stat.get(key, 0) for key in ("pgmajfault", "pswpin", "pswpout", "workingset_refault_anon", "workingset_refault_file")}
            evidence["io_stat"] = read_io_stat(self.cgroup / "io.stat", self.device)
        if self.kind == "cpu_freq":
            evidence["max_frequency_mhz"] = int(freq_path(self.probe_cpu, "scaling_max_freq").read_text()) / 1000
        return evidence

    def active(self, evidence, qdisc):
        if self.kind == "none":
            return True
        if self.netem:
            return self.netem.lower() in qdisc.lower()
        if self.kind == "cpu_quota":
            return evidence["cpu_stat"].get("nr_throttled", 0) > 0
        if self.kind == "cpu_freq":
            mean = evidence["mean_frequency_mhz"]
            capped = evidence.get("max_frequency_mhz") == float(self.level)
            return capped and mean is not None and mean <= float(self.level) * 1.1
        if self.kind == "mem_high":
            return evidence["memory_events"].get("high", 0) > 0
        if self.kind == "mem_uncore":
            value = evidence["mean_uncore_frequency_mhz"]
            return value is not None and value <= float(self.level) * 1.1
        if self.kind in ("disk_iops", "disk_bps"):
            device, _, rest = evidence["limits"]["io.max"].partition(" ")
            values = dict(field.split("=") for field in rest.split())
            keys = ("riops", "wiops") if self.kind == "disk_iops" else ("rbps", "wbps")
            done = evidence["io_stat"].get("rios", 0) + evidence["io_stat"].get("wios", 0)
            return device == self.device and all(values.get(key, "max") != "max" for key in keys) and done > 0
        return False

    def __exit__(self, *exc):
        self.sampler.__exit__()
        if self.uncore_sampler:
            self.uncore_sampler.__exit__()
        try:
            self.evidence = self.collect()
        finally:
            if self.saved_freq is not None:
                freq_path(self.probe_cpu, "scaling_max_freq").write_text(self.saved_freq)
            if self.saved_uncore is not None:
                (UNCORE / "max_freq_khz").write_text(self.saved_uncore)
            if self.cgroup is not None:
                remove_cgroup(self.cgroup)
        return False
