import argparse
import json
import os
import subprocess
import sys
import time

from minisb.probes import PROBES


def setup_network(netem):
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    if netem:
        subprocess.run(["tc", "qdisc", "add", "dev", "lo", "root", "netem", *netem.split()], check=True)


def qdisc_stats():
    return subprocess.run(["tc", "-s", "qdisc", "show", "dev", "lo"], capture_output=True, text=True).stdout


def run_probe(probe, workdir, repeats, window, warmup):
    started = time.perf_counter()
    probe.setup(workdir)
    try:
        ready = time.perf_counter()
        check = bool(probe.check())
        probe.measure(warmup)
        samples = [probe.measure(window) for _ in range(repeats)]
    finally:
        probe.teardown()
    finished = time.perf_counter()
    return {
        "probe": probe.name,
        "subsystem": probe.subsystem,
        "unit": probe.unit,
        "check_passed": check,
        "samples": samples,
        "setup_seconds": ready - started,
        "cost_seconds": finished - started,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probes", default=",".join(PROBES))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--window", type=float, default=0.2)
    parser.add_argument("--warmup", type=float, default=0.05)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--netem", default="")
    args = parser.parse_args()
    os.makedirs(args.workdir, exist_ok=True)
    setup_network(args.netem)
    results = [
        run_probe(PROBES[name](), args.workdir, args.repeats, args.window, args.warmup)
        for name in args.probes.split(",")
    ]
    json.dump(
        {
            "cpus": sorted(os.sched_getaffinity(0)),
            "netem": args.netem,
            "qdisc": qdisc_stats(),
            "results": results,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
