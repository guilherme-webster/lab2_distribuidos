import importlib.util
from pathlib import Path

import pytest

from dsnode.config import Config

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gen_compose", ROOT / "scripts" / "gen_compose.py")
gen_compose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_compose)


def test_render_has_one_service_per_node():
    text = gen_compose.render(3)
    for i in (1, 2, 3):
        assert f"  node{i}:\n" in text
        assert f'NODE_ID: "{i}"' in text
    assert "node4:" not in text
    assert 'PEERS: "1=node1:9000,2=node2:9000,3=node3:9000"' in text


def test_tunables_default_to_config_defaults():
    text = gen_compose.render(2)
    defaults = Config(node_id=1)
    for var, default in gen_compose.TUNABLES.items():
        assert f"${{{var}:-{default}}}" in text
        field = var.lower()
        assert str(getattr(defaults, field)) == default, var


def test_render_rejects_zero_nodes():
    with pytest.raises(ValueError):
        gen_compose.render(0)


def test_committed_compose_is_up_to_date():
    assert (ROOT / "compose.yaml").read_text() == gen_compose.render(5)
