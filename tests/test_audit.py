import dataclasses
import json

from conftest import T0

from boost_scheduler import AuditLog


def filled_log() -> AuditLog:
    log = AuditLog()
    log.record(ts=T0, actor_id="m", action="run_created", run_id="run-1", plan="standard")
    log.record(ts=T0, actor_id="system", action="boost_executed", run_id="run-1", cost=10)
    log.record(ts=T0, actor_id="m", action="run_paused", run_id="run-1")
    return log


def test_fresh_and_filled_logs_verify():
    assert AuditLog().verify()
    assert filled_log().verify()


def test_editing_an_event_is_detected():
    log = filled_log()
    log._events[1] = dataclasses.replace(log._events[1], details={"cost": 1})
    assert not log.verify()


def test_deleting_an_event_is_detected():
    log = filled_log()
    del log._events[1]
    assert not log.verify()


def test_reordering_is_detected():
    log = filled_log()
    log._events[0], log._events[1] = log._events[1], log._events[0]
    assert not log.verify()


def test_filtering():
    log = filled_log()
    assert len(log.events(action="run_paused")) == 1
    assert len(log.events(run_id="run-1")) == 3


def test_jsonl_export_round_trips(tmp_path):
    log = filled_log()
    out = tmp_path / "audit.jsonl"
    log.to_jsonl(out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert rows[1]["prev_hash"] == rows[0]["hash"]
