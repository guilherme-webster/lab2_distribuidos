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
