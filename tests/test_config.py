import pytest

from dsnode.config import Config, ConfigError, parse_peers

PEERS = "1=node1:9000,2=node2:9000,3=node3:9001"


def test_parse_peers():
    assert parse_peers(PEERS) == {
        1: ("node1", 9000),
        2: ("node2", 9000),
        3: ("node3", 9001),
    }


def test_parse_peers_ignores_blanks():
    assert parse_peers(" 1=a:1 , ,2=b:2,") == {1: ("a", 1), 2: ("b", 2)}
    assert parse_peers("") == {}


@pytest.mark.parametrize("raw", ["1node1:9000", "x=node1:9000", "1=node1", "1=:9000", "1=a:1,1=b:2"])
def test_parse_peers_rejects_invalid(raw):
    with pytest.raises(ConfigError):
        parse_peers(raw)


def test_from_env_removes_self_from_peers():
    cfg = Config.from_env({"NODE_ID": "2", "PEERS": PEERS})
    assert cfg.node_id == 2
    assert cfg.peer_ids == [1, 3]
    assert cfg.peers[3] == ("node3", 9001)


def test_from_env_defaults():
    cfg = Config.from_env({"NODE_ID": "1"})
    assert cfg.peers == {}
    assert (cfg.hb_interval_ms, cfg.hb_timeout_ms) == (200, 600)
    assert cfg.drop_rate == 0.0 and cfg.seed == 0


def test_from_env_overrides():
    cfg = Config.from_env({
        "NODE_ID": "1",
        "PORT": "9100",
        "HB_INTERVAL_MS": "100",
        "HB_TIMEOUT_MS": "500",
        "ELECTION_TIMEOUT_MS": "300",
        "COORD_TIMEOUT_MS": "700",
        "DROP_RATE": "0.1",
        "DELAY_MS": "20",
        "JITTER_MS": "5",
        "SEED": "42",
    })
    assert cfg.port == 9100
    assert (cfg.hb_interval_ms, cfg.hb_timeout_ms) == (100, 500)
    assert (cfg.election_timeout_ms, cfg.coord_timeout_ms) == (300, 700)
    assert (cfg.drop_rate, cfg.delay_ms, cfg.jitter_ms, cfg.seed) == (0.1, 20, 5, 42)


def test_from_env_requires_node_id():
    with pytest.raises(ConfigError):
        Config.from_env({"PEERS": PEERS})


@pytest.mark.parametrize("env", [
    {"NODE_ID": "abc"},
    {"NODE_ID": "1", "PORT": "0"},
    {"NODE_ID": "1", "DROP_RATE": "1.5"},
    {"NODE_ID": "1", "HB_INTERVAL_MS": "500", "HB_TIMEOUT_MS": "500"},
    {"NODE_ID": "1", "ELECTION_TIMEOUT_MS": "0"},
    {"NODE_ID": "1", "DELAY_MS": "-1"},
])
def test_from_env_rejects_invalid(env):
    with pytest.raises(ConfigError):
        Config.from_env(env)


def test_config_is_immutable():
    cfg = Config(node_id=1)
    with pytest.raises(AttributeError):
        cfg.node_id = 2


def test_adaptive_timeout_settings():
    cfg = Config.from_env({"NODE_ID": "1"})
    assert (cfg.hb_adaptive, cfg.hb_timeout_step_ms, cfg.hb_timeout_max_ms) == (False, 200, 0)
    cfg = Config.from_env({
        "NODE_ID": "1", "HB_ADAPTIVE": "true", "HB_TIMEOUT_STEP_MS": "100", "HB_TIMEOUT_MAX_MS": "2000",
    })
    assert (cfg.hb_adaptive, cfg.hb_timeout_step_ms, cfg.hb_timeout_max_ms) == (True, 100, 2000)


@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("No", False), ("off", False), ("", False),
])
def test_parse_bool(raw, expected):
    assert Config.from_env({"NODE_ID": "1", "HB_ADAPTIVE": raw}).hb_adaptive is expected


@pytest.mark.parametrize("env", [
    {"HB_ADAPTIVE": "talvez"},
    {"HB_TIMEOUT_STEP_MS": "0"},
    {"HB_TIMEOUT_MAX_MS": "500"},  # menor que HB_TIMEOUT_MS (600)
])
def test_adaptive_settings_rejected(env):
    with pytest.raises(ConfigError):
        Config.from_env({"NODE_ID": "1", **env})
