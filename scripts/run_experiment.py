#!/usr/bin/env python3
"""Executa um experimento: grade de parâmetros × seeds, cada ensaio no Docker.

    python scripts/run_experiment.py experiments/configs/hb-loss.toml
    python scripts/run_experiment.py experiments/configs/hb-loss.toml --seeds 2 --dry-run

A especificação (TOML) define:

    name, description, seeds, warmup_s, duration_s
    [base]    parâmetros fixos (N e variáveis de ambiente dos nós)
    [sweep]   parâmetros variados: listas, combinadas em produto cartesiano
    [[faults]] at_s (após o aquecimento), action (kill/stop/start/pause/unpause)
              e target (id do nó ou "max" = maior id)

Cada ensaio grava em <out>/<name>/<config>/seed-NN/:
    logs.jsonl.gz logs de todos os nós (gzip; check_heartbeat.py lê direto)
    faults.jsonl  falhas injetadas (scripts/faults.sh)
    meta.json     parâmetros, seed, janela de medição, commit; "complete": true
e <out>/<name>/env.json guarda hardware, SO e versões (collect_env.py).

Ensaios completos são pulados (o experimento pode ser retomado após uma
interrupção); --force refaz. Os ensaios são intercalados (para cada seed,
todas as configurações) para que variações lentas da máquina não se
confundam com o efeito de um parâmetro.
"""

import argparse
import gzip
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_env  # noqa: E402
import gen_compose  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Projeto e compose próprios: não interferem num `make up` em andamento
PROJECT = os.environ.get("EXP_PROJECT", "mc714-exp")
COMPOSE_FILE = os.environ.get("EXP_COMPOSE_FILE", ".run/exp-compose.yaml")
FAULT_ACTIONS = {"kill", "stop", "start", "pause", "unpause"}
STARTUP_TIMEOUT = 60.0
# Custo fixo medido por ensaio (subir, esperar os nós, coletar, derrubar),
# só para a previsão de tempo
OVERHEAD_S = 10.0


class SpecError(ValueError):
    """Especificação de experimento inválida."""


@dataclass(frozen=True)
class Fault:
    at_s: float
    action: str
    target: int | str

    def node(self, n: int) -> int:
        return n if self.target == "max" else int(self.target)


@dataclass
class Spec:
    name: str
    description: str
    seeds: int
    warmup_s: float
    duration_s: float
    base: dict
    sweep: dict[str, list]
    faults: list[Fault]
    raw: dict = field(default_factory=dict)

    def configs(self) -> list[dict]:
        """Combinações da grade, cada uma com os parâmetros fixos."""
        keys = list(self.sweep)
        return [{**self.base, **dict(zip(keys, values))} for values in itertools.product(*self.sweep.values())]


def load_spec(path: str | Path) -> Spec:
    raw = tomllib.loads(Path(path).read_text())
    try:
        spec = Spec(
            name=raw["name"],
            description=raw.get("description", ""),
            seeds=int(raw.get("seeds", 10)),
            warmup_s=float(raw.get("warmup_s", 5)),
            duration_s=float(raw["duration_s"]),
            base=dict(raw.get("base", {})),
            sweep=dict(raw.get("sweep", {})),
            faults=[Fault(float(f["at_s"]), f["action"], f["target"]) for f in raw.get("faults", [])],
            raw=raw,
        )
    except KeyError as e:
        raise SpecError(f"campo obrigatório ausente: {e}") from None
    if not spec.name.replace("-", "").replace("_", "").isalnum():
        raise SpecError(f"nome inválido (use letras, números, - e _): {spec.name!r}")
    if spec.seeds < 1 or spec.duration_s <= 0 or spec.warmup_s < 0:
        raise SpecError("seeds >= 1, duration_s > 0 e warmup_s >= 0")
    for key, values in spec.sweep.items():
        if not isinstance(values, list) or not values:
            raise SpecError(f"sweep.{key} deve ser uma lista não vazia")
        if key in spec.base:
            raise SpecError(f"{key} está em base e em sweep")
    for f in spec.faults:
        if f.action not in FAULT_ACTIONS:
            raise SpecError(f"ação de falha inválida: {f.action!r}")
        if not 0 <= f.at_s <= spec.duration_s:
            raise SpecError(f"falha em at_s={f.at_s} fora de [0, duration_s]")
        if f.target != "max" and not isinstance(f.target, int):
            raise SpecError(f"target deve ser inteiro ou \"max\": {f.target!r}")
    for config in spec.configs():
        if not isinstance(config.get("N"), int) or config["N"] < 2:
            raise SpecError("N (inteiro >= 2) deve estar em base ou sweep")
        for f in spec.faults:
            if not 1 <= f.node(config["N"]) <= config["N"]:
                raise SpecError(f"falha em nó inexistente: {f.target} com N={config['N']}")
    return spec


# --- planejamento ----------------------------------------------------------

def env_value(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def label(spec: Spec, config: dict) -> str:
    """Nome do diretório da configuração: só os parâmetros variados."""
    if not spec.sweep:
        return "base"
    return "__".join(f"{k}={env_value(config[k])}" for k in spec.sweep)


@dataclass
class Run:
    spec: Spec
    config: dict
    seed: int
    dir: Path

    @property
    def n(self) -> int:
        return self.config["N"]

    def env(self) -> dict[str, str]:
        params = {k: env_value(v) for k, v in self.config.items() if k != "N"}
        return {**params, "SEED": str(self.seed)}

    def is_complete(self) -> bool:
        meta = self.dir / "meta.json"
        try:
            return json.loads(meta.read_text()).get("complete") is True
        except (OSError, json.JSONDecodeError):
            return False


def plan(spec: Spec, out: Path, seeds: int | None = None) -> list[Run]:
    exp_dir = out / spec.name
    runs = []
    for seed in range(1, (seeds or spec.seeds) + 1):
        for config in spec.configs():
            runs.append(Run(spec, config, seed, exp_dir / label(spec, config) / f"seed-{seed:02d}"))
    return runs


def estimate_s(runs: list[Run]) -> float:
    return sum(r.spec.warmup_s + r.spec.duration_s + OVERHEAD_S for r in runs)


# --- execução --------------------------------------------------------------

def compose(*args: str, env: dict, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["docker", "compose", "--project-directory", ".", "-f", COMPOSE_FILE, *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} falhou:\n{result.stderr}")
    return result


def node_starts(env: dict) -> int:
    out = compose("logs", "--no-log-prefix", env=env, check=False).stdout
    return out.count('"event":"node_start"')


def sleep_until(t: float) -> None:
    delay = t - time.time()
    if delay > 0:
        time.sleep(delay)


def execute(run: Run) -> None:
    if run.dir.exists():
        shutil.rmtree(run.dir)  # resto de um ensaio interrompido
    run.dir.mkdir(parents=True)
    env = {**os.environ, **run.env(), "COMPOSE_PROJECT_NAME": PROJECT}
    (ROOT / COMPOSE_FILE).parent.mkdir(exist_ok=True)
    (ROOT / COMPOSE_FILE).write_text(gen_compose.render(run.n))
    faults_file = run.dir / "faults.jsonl"
    faults_file.touch()
    meta = {
        "experiment": run.spec.name,
        "config": label(run.spec, run.config),
        "params": run.config,
        "seed": run.seed,
        "n": run.n,
        "warmup_s": run.spec.warmup_s,
        "duration_s": run.spec.duration_s,
        "faults_planned": [
            {"at_s": f.at_s, "action": f.action, "target": f.node(run.n)} for f in run.spec.faults
        ],
        "git": collect_env.collect()["git"],
        "started_at": time.time(),
        "complete": False,
    }
    try:
        compose("down", "--remove-orphans", "-t", "2", env=env, check=False)
        compose("up", "-d", "--remove-orphans", env=env)
        deadline = time.time() + STARTUP_TIMEOUT
        while node_starts(env) < run.n:
            if time.time() > deadline:
                raise RuntimeError(f"nem todos os {run.n} nós subiram em {STARTUP_TIMEOUT:.0f} s")
            time.sleep(0.5)
        meta["all_up_ts"] = time.time()
        time.sleep(run.spec.warmup_s)

        start = meta["measure_start_ts"] = time.time()
        fault_env = {**env, "RUN_COMPOSE": COMPOSE_FILE, "FAULTS_FILE": str(faults_file)}
        for f in sorted(run.spec.faults, key=lambda f: f.at_s):
            sleep_until(start + f.at_s)
            result = subprocess.run(
                ["scripts/faults.sh", f.action, str(f.node(run.n))],
                cwd=ROOT, env=fault_env, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(f"faults.sh {f.action} falhou:\n{result.stderr}")
        sleep_until(start + run.spec.duration_s)
        meta["measure_end_ts"] = time.time()

        logs = compose("logs", "--no-log-prefix", env=env).stdout.splitlines()
        # Comprimido: ~12× menor, para os dados brutos caberem no repositório
        (run.dir / "logs.jsonl.gz").write_bytes(gzip.compress(("\n".join(sorted(logs)) + "\n").encode()))
        meta["complete"] = True
    finally:
        compose("down", "--remove-orphans", "-t", "2", env=env, check=False)
        meta["finished_at"] = time.time()
        (run.dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def build_image() -> None:
    result = subprocess.run(
        ["docker", "compose", "--project-directory", ".", "-f", "compose.yaml", "build"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"build falhou:\n{result.stderr}")


def fmt_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes // 60}h{minutes % 60:02d}min" if minutes >= 60 else f"{minutes}min{secs:02d}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", help="arquivo TOML do experimento")
    parser.add_argument("--out", default="experiments/raw", help="raiz dos dados brutos")
    parser.add_argument("--seeds", type=int, help="sobrescreve o número de seeds (ex.: piloto)")
    parser.add_argument("--force", action="store_true", help="refaz ensaios já completos")
    parser.add_argument("--dry-run", action="store_true", help="só lista os ensaios e o tempo previsto")
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
    except SpecError as e:
        print(f"especificação inválida: {e}", file=sys.stderr)
        return 2
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    runs = plan(spec, out, args.seeds)
    pending = [r for r in runs if args.force or not r.is_complete()]
    print(f"{spec.name}: {len(spec.configs())} configurações × {args.seeds or spec.seeds} seeds = "
          f"{len(runs)} ensaios, {len(pending)} a executar (~{fmt_duration(estimate_s(pending))})")
    if args.dry_run:
        for r in pending:
            print(f"  {r.dir.relative_to(out)}")
        return 0
    if not pending:
        return 0

    build_image()
    exp_dir = out / spec.name
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "env.json").write_text(json.dumps(collect_env.collect(), indent=2, ensure_ascii=False) + "\n")
    (exp_dir / "spec.toml").write_text(Path(args.spec).read_text())

    failures = 0
    began = time.time()
    for i, r in enumerate(pending, 1):
        rel = r.dir.relative_to(out)
        print(f"[{i}/{len(pending)}] {rel} ... ", end="", flush=True)
        t = time.time()
        try:
            execute(r)
            print(f"ok ({time.time() - t:.0f} s)")
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            failures += 1
            print(f"FALHOU\n{e}", file=sys.stderr)
        left = estimate_s(pending[i:]) * (time.time() - began) / max(estimate_s(pending[:i]), 1)
        if i < len(pending):
            print(f"    restante ~{fmt_duration(left)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
