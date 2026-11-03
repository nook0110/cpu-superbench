import argparse
import json
import os
import platform
import site
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from minisb.faults import Fault, needs_root, parse, reset
from minisb.probes import PROBES

ROOT = Path(__file__).resolve().parent.parent
ALL_FAULTS = [
    "none",
    "cpu_quota:0.5",
    "cpu_freq:2000",
    "mem_high:400",
    "mem_uncore:800",
    "disk_iops:2000",
    "disk_bps:20",
    "net_delay:1ms",
    "net_loss:2%",
    "net_rate:500mbit",
]


def default_cpu():
    allowed = sorted(os.sched_getaffinity(0))
    return 3 if 3 in allowed else allowed[len(allowed) // 2]


def invoking_user():
    if os.geteuid() == 0 and "SUDO_UID" in os.environ:
        return int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    return None


def give_back(path):
    user = invoking_user()
    if user and Path(path).exists():
        os.chown(path, *user)


def python_path():
    return os.environ.get("MINISB_PYTHONPATH") or os.pathsep.join([str(ROOT), site.getusersitepackages()])


def run_worker(args, fault):
    command = []
    user = invoking_user()
    if user:
        command += ["setpriv", f"--reuid={user[0]}", f"--regid={user[1]}", "--init-groups"]
    command += [
        "unshare", "-rn", "taskset", "-c", str(args.cpu),
        sys.executable, "-m", "minisb.worker",
        "--probes", args.probes,
        "--repeats", str(args.repeats),
        "--window", str(args.window),
        "--workdir", str(args.workdir),
    ]
    if fault.netem:
        command += ["--netem", fault.netem]
    env = dict(os.environ, PYTHONPATH=python_path())
    if user:
        env["HOME"] = os.environ.get("MINISB_HOME", env.get("HOME", "/"))
    procs = fault.cgroup / "cgroup.procs" if fault.cgroup else None

    def enter_cgroup():
        if procs:
            procs.write_text(str(os.getpid()))

    completed = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=1800, preexec_fn=enter_cgroup
    )
    if completed.returncode != 0:
        raise RuntimeError(f"worker failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def episode(args, spec):
    fault = Fault(spec, args.cpu, args.workdir, isolate=os.geteuid() == 0)
    started = datetime.now(timezone.utc).isoformat()
    with fault:
        give_back(args.workdir)
        worker = run_worker(args, fault)
    fault.evidence["active"] = fault.active(fault.evidence, worker["qdisc"])
    if fault.kind != "none":
        time.sleep(args.cooldown)
    return {
        "fault": spec,
        "kind": fault.kind,
        "level": fault.level,
        "subsystem": fault.subsystem,
        "started_at": started,
        "evidence": fault.evidence,
        "worker": worker,
    }


def environment():
    return {
        "platform": platform.platform(),
        "cpu_model": next(
            (line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
             if line.startswith("model name")),
            "",
        ),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "logical_cpus": os.cpu_count(),
    }


def fault_list(text):
    specs = ALL_FAULTS if text == "all" else text.split(",")
    for spec in specs:
        parse(spec)
    return specs if specs[0] == "none" else ["none", *specs]


def print_record(record, reference):
    evidence = {key: value for key, value in record["evidence"].items() if value not in ({}, None, "")}
    print(f"\n== {record['fault']}  ограничение действует: {'да' if record['evidence']['active'] else 'НЕТ'}")
    print(f"   {json.dumps(evidence, ensure_ascii=False)}")
    print(f"{'тест':16} {'самопр.':8} {'медиана':>12} {'единицы':12} {'CV, %':>6} {'время, с':>8} {'к здоровому':>11}")
    for result in record["worker"]["results"]:
        samples = np.asarray(result["samples"])
        cv = 100 * samples.std(ddof=1) / samples.mean() if len(samples) > 1 else 0.0
        ratio = np.median(samples) / reference[result["probe"]] if reference else 1.0
        print(
            f"{result['probe']:16} {'ok' if result['check_passed'] else 'FAIL':8} "
            f"{np.median(samples):12.2f} {result['unit']:12} {cv:6.2f} {result['cost_seconds']:8.2f} {ratio:11.2f}"
        )


def command_check(args):
    env = environment()
    reference = None
    passed = True
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        give_back(args.output.parent)
    for spec in fault_list(args.faults):
        record = episode(args, spec)
        if spec == "none" and reference is None:
            reference = {r["probe"]: float(np.median(r["samples"])) for r in record["worker"]["results"]}
        print_record(record, reference)
        passed &= record["evidence"]["active"] and all(r["check_passed"] for r in record["worker"]["results"])
        if args.output:
            with args.output.open("a") as output:
                output.write(json.dumps({**record, "environment": env}, ensure_ascii=False) + "\n")
            give_back(args.output)
    return 0 if passed else 1


def command_reset(args):
    reset(range(os.cpu_count()))
    return 0


def elevate(argv, specs):
    if os.geteuid() == 0 or not any(needs_root(spec) for spec in specs):
        return
    command = [
        "sudo", "env",
        f"PYTHONPATH={python_path()}",
        f"MINISB_PYTHONPATH={python_path()}",
        f"MINISB_HOME={os.environ.get('HOME', '')}",
        sys.executable, "-m", "minisb", *argv,
    ]
    print("Ограничения CPU, памяти и диска требуют root, перезапуск через sudo", file=sys.stderr)
    os.execvp("sudo", command)


def main():
    parser = argparse.ArgumentParser(prog="python3 -m minisb")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_run_options(command):
        command.add_argument("--probes", default=",".join(PROBES))
        command.add_argument("--repeats", type=int, default=5)
        command.add_argument("--window", type=float, default=0.2)
        command.add_argument("--cpu", type=int, default=default_cpu())
        command.add_argument("--workdir", type=Path, default=ROOT / "work")
        command.add_argument("--cooldown", type=float, default=5.0)

    check = sub.add_parser(
        "check", help="run all probes healthy and under each limit; print self-checks, medians and ratios"
    )
    add_run_options(check)
    check.add_argument("--faults", default="none", help="comma-separated limits, or 'all' for one level of each kind")
    check.add_argument("--output", type=Path)
    check.set_defaults(handler=command_check)

    restore = sub.add_parser("reset", help="restore CPU frequency limits and remove leftover cgroups (root)")
    restore.set_defaults(handler=command_reset)

    args = parser.parse_args()
    if args.command == "check":
        elevate(sys.argv[1:], fault_list(args.faults))
    elif args.command == "reset":
        elevate(sys.argv[1:], ["cpu_freq:0"])
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
