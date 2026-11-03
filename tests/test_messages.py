import json

import pytest

from dsnode.messages import Message, MessageError, MessageFactory, MsgType


def test_round_trip():
    msg = Message(MsgType.ELECTION, 2, 5, "2.0.7", {"election_id": "2-1"})
    back = Message.from_bytes(msg.to_bytes())
    assert back == msg
    assert back.type == MsgType.ELECTION


def test_round_trip_custom_type_and_empty_payload():
    msg = Message("PING", 1, 2, "1.0.1")
    assert Message.from_bytes(msg.to_bytes()) == msg


def test_missing_payload_defaults_to_empty():
    data = json.dumps({"type": "HEARTBEAT", "src": 1, "dst": 2, "msg_id": "x"}).encode()
    assert Message.from_bytes(data).payload == {}


@pytest.mark.parametrize("data", [
    b"",
    b"\xff\xfe",
    b"not json",
    b"[1, 2]",
    b'{"type": "HEARTBEAT", "src": 1, "dst": 2}',
    b'{"type": "", "src": 1, "dst": 2, "msg_id": "x"}',
    b'{"type": "HEARTBEAT", "src": "1", "dst": 2, "msg_id": "x"}',
    b'{"type": "HEARTBEAT", "src": true, "dst": 2, "msg_id": "x"}',
    b'{"type": "HEARTBEAT", "src": 1, "dst": 2, "msg_id": "x", "payload": []}',
])
def test_rejects_invalid(data):
    with pytest.raises(MessageError):
        Message.from_bytes(data)


def test_rejects_oversized():
    msg = Message("HEARTBEAT", 1, 2, "x", {"blob": "a" * 70_000})
    with pytest.raises(MessageError):
        msg.to_bytes()


def test_factory_fills_src_and_unique_ids():
    f = MessageFactory(node_id=3, incarnation=9)
    a = f.new(MsgType.HEARTBEAT, 1, {"seq": 1})
    b = f.new(MsgType.HEARTBEAT, 2)
    assert (a.src, a.dst, a.payload) == (3, 1, {"seq": 1})
    assert (b.dst, b.payload) == (2, {})
    assert a.msg_id == "3.9.1" and b.msg_id == "3.9.2"


def test_factory_ids_differ_across_incarnations():
    before = MessageFactory(3, incarnation=1).new("PING", 1)
    after = MessageFactory(3, incarnation=2).new("PING", 1)
    assert before.msg_id != after.msg_id
