import io
import json

import pytest

from dsnode.log import EventLog, MemoryLog, stream_sink
from dsnode.messages import MsgType


def test_emit_writes_one_json_line_per_event():
    out = io.StringIO()
    log = EventLog(3, clock=lambda: 12.5, sink=stream_sink(out))
    log.emit("suspect", peer=5, last_seen_ms=640)
    log.emit("node_start")
    lines = out.getvalue().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"ts": 12.5, "node": 3, "event": "suspect", "peer": 5, "last_seen_ms": 640},
        {"ts": 12.5, "node": 3, "event": "node_start"},
    ]


def test_non_json_values_are_stringified():
    out = io.StringIO()
    log = EventLog(1, clock=lambda: 0.0, sink=stream_sink(out))
    log.emit("msg_send", type=MsgType.ELECTION, addr=("node2", 9000), obj=object)
    record = json.loads(out.getvalue())
    assert record["type"] == "ELECTION"
    assert record["addr"] == ["node2", 9000]
    assert isinstance(record["obj"], str)


@pytest.mark.parametrize("field", ["ts", "node", "event"])
def test_rejects_reserved_fields(field):
    log = MemoryLog(1, clock=lambda: 0.0)
    with pytest.raises(ValueError):
        log.emit("x", **{field: 1})
    assert log.records == []


def test_memory_log_uses_injected_clock_and_filters_events():
    now = [1.0]
    log = MemoryLog(2, clock=lambda: now[0])
    log.emit("suspect", peer=1)
    now[0] = 2.0
    log.emit("restore", peer=1)
    log.emit("suspect", peer=3)
    assert [r["ts"] for r in log.records] == [1.0, 2.0, 2.0]
    assert [r["peer"] for r in log.events("suspect")] == [1, 3]
