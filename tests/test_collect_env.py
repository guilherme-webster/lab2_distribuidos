import json

from tests.scripts import load_script

collect_env = load_script("collect_env")


def test_collect_has_required_fields_and_is_json():
    env = collect_env.collect()
    assert set(env) == {"collected_at", "hardware", "os", "versions", "git"}
    assert env["hardware"]["cores"] >= 1
    assert env["os"]["kernel"]
    assert env["versions"]["python_host"]
    assert len(env["git"]["commit"]) == 40
    json.dumps(env)


def test_cli_writes_file(tmp_path):
    out = tmp_path / "env.json"
    collect_env.main(["-o", str(out)])
    assert json.loads(out.read_text())["os"]["name"]


def test_experiment_outputs_do_not_mark_tree_dirty(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    monkeypatch.setattr(collect_env, "ROOT", tmp_path)
    for d in collect_env.OUTPUT_DIRS:
        (tmp_path / d / "exp").mkdir(parents=True)
        (tmp_path / d / "exp" / "logs.jsonl.gz").write_bytes(b"")
    assert collect_env.git_status() == ""
    (tmp_path / "novo.py").write_text("")
    assert "novo.py" in collect_env.git_status()
