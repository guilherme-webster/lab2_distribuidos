#!/usr/bin/env python3
"""Registra o ambiente de execução dos experimentos (hardware, SO, versões).

    python scripts/collect_env.py               # imprime JSON
    python scripts/collect_env.py -o env.json

O enunciado pede hardware, sistema operacional, versões das ferramentas e
configuração de cada ensaio; run_experiment.py grava este JSON junto dos
dados brutos. Campos que não puderem ser obtidos ficam null.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "mc714-dsnode:dev"


def run(*cmd: str) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def cpu_model() -> str | None:
    if platform.system() == "Darwin":
        return run("sysctl", "-n", "machdep.cpu.brand_string")
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def memory_bytes() -> int | None:
    if platform.system() == "Darwin":
        out = run("sysctl", "-n", "hw.memsize")
        return int(out) if out else None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def collect() -> dict:
    status = run("git", "status", "--porcelain")
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "hardware": {
            "cpu": cpu_model(),
            "cores": os.cpu_count(),
            "memory_bytes": memory_bytes(),
            "machine": platform.machine(),
        },
        "os": {
            "name": os_name(),
            "kernel": platform.release(),
            "hostname": platform.node(),
        },
        "versions": {
            "python_host": platform.python_version(),
            "docker": run("docker", "version", "--format", "{{.Server.Version}}"),
            "compose": run("docker", "compose", "version", "--short"),
            "image_id": run("docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"),
            "python_image": run("docker", "run", "--rm", IMAGE, "python", "--version"),
        },
        "git": {
            "commit": run("git", "rev-parse", "HEAD"),
            "dirty": bool(status) if status is not None else None,
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out", help="arquivo de saída (padrão: stdout)")
    args = parser.parse_args(argv)
    text = json.dumps(collect(), indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
