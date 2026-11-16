"""值班切换证明 duty_transitions：持久化字段、只读查询、与交班同源。"""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FIXTURES_DIR", str(ROOT / "fixtures"))
    from app import seed
    seed.init_db()
    from app.db import connect
    yield connect


def _start_complete(svc, connect):
    h = svc.start_handover(1, 2, [1])
    hid = h["id"]
    done = svc.complete_handover(hid)
    assert done["status"] == "已完成"
    return hid


def test_transition_row_persisted_with_all_fields(fresh_db):
    """完成交班逐区落证明：区、旧值班、新值班、切换时刻、交班单 id 齐全。"""
    from app.services import handover as svc
    connect = fresh_db
    hid = _start_complete(svc, connect)

    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM duty_transitions WHERE handover_id = ?", (hid,)
        ).fetchall()
        assert len(rows) == 1
        t = dict(rows[0])
        assert t["zone_id"] == 1
        assert t["from_shift_id"] == 1
        assert t["to_shift_id"] == 2
        assert t["handover_id"] == hid
        assert t["switched_at"]  # 切换时刻非空


def test_transition_query_by_zone_and_handover(fresh_db):
    """只读接口可按责任区或交班单 id 查询，且只追加不改写。"""
    from app.services import duty
    from app.services import handover as svc
    connect = fresh_db
    hid1 = _start_complete(svc, connect)
    # 第二单：Z2 #1 -> #3
    h2 = svc.start_handover(1, 3, [2])
    hid2 = h2["id"]
    svc.complete_handover(hid2)

    by_zone = duty.list_transitions(zone_id=1)
    assert len(by_zone) == 1
    assert by_zone[0]["handover_id"] == hid1
    assert by_zone[0]["zone_name"] == "华北责任区"
    assert by_zone[0]["from_shift_name"] == "早班"
    assert by_zone[0]["to_shift_name"] == "晚班"

    by_handover = duty.list_transitions(handover_id=hid2)
    assert len(by_handover) == 1
    assert by_handover[0]["zone_id"] == 2

    all_rows = duty.list_transitions()
    assert {r["handover_id"] for r in all_rows} == {hid1, hid2}


def test_handover_detail_carries_same_source_transitions(fresh_db):
    """交班详情/列表内嵌的证明与独立查询同源同数。"""
    from app.services import duty
    from app.services import handover as svc
    connect = fresh_db
    hid = _start_complete(svc, connect)

    detail = svc.get_handover(hid)
    assert len(detail["transitions"]) == 1
    assert detail["transitions"][0]["to_shift_id"] == 2

    listed = next(h for h in svc.list_handovers() if h["id"] == hid)
    assert [t["id"] for t in listed["transitions"]] == [
        t["id"] for t in detail["transitions"]
    ]
    # 独立只读接口与详情同源：同一条证明、同一切换时刻
    standalone = duty.list_transitions(handover_id=hid)
    assert len(standalone) == 1
    assert standalone[0]["id"] == detail["transitions"][0]["id"]
    assert standalone[0]["switched_at"] == detail["transitions"][0]["switched_at"]
    assert standalone[0]["to_shift_name"] == "晚班"


def test_no_transition_when_handover_rejected(fresh_db):
    """交班被拒/回滚时不得写切换证明，也不得改动值班权。"""
    from app.services import duty
    from app.services import handover as svc
    connect = fresh_db
    # #2 未绑定 Z2 => 整单拒绝
    with pytest.raises(Exception):
        svc.start_handover(1, 2)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM duty_transitions").fetchone()["c"] == 0
        # 值班权原样
        assert conn.execute(
            "SELECT shift_id FROM duty_holders WHERE zone_id=1"
        ).fetchone()["shift_id"] == 1


def test_failed_complete_writes_no_transition_and_keeps_holder(fresh_db):
    """生效瞬间持权被改走 => 回滚：无证明、无空窗、交班单不标成功。"""
    from app.services import handover as svc
    connect = fresh_db
    h = svc.start_handover(1, 2, [1])
    hid = h["id"]
    # 并发改写 Z1 值班权（#2 有资格，直接条件 UPDATE）
    with connect() as conn:
        cur = conn.execute(
            "UPDATE duty_holders SET shift_id=2 WHERE zone_id=1 AND shift_id=1"
        )
        assert cur.rowcount == 1
        conn.commit()

    with pytest.raises(Exception) as ei:
        svc.complete_handover(hid)
    assert ei.value.status_code == 409

    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM duty_transitions WHERE handover_id=?", (hid,)
        ).fetchone()["c"] == 0
        # 仍有持权行（无空窗），持权者为并发后的 #2
        holder = conn.execute(
            "SELECT shift_id FROM duty_holders WHERE zone_id=1"
        ).fetchone()
        assert holder is not None and holder["shift_id"] == 2
        status = conn.execute(
            "SELECT status FROM handovers WHERE id=?", (hid,)
        ).fetchone()["status"]
        assert status == "进行中"


def test_trigger_abort_during_complete_returns_clean_409(fresh_db, monkeypatch):
    """预复核后接班方资格被移除 => 触发器在 UPDATE 时拦截 => 409 回滚、无证明。"""
    from app.services import duty
    from app.services import handover as svc
    connect = fresh_db
    h = svc.start_handover(1, 2, [1])
    hid = h["id"]
    # 并发移除 #2 对 Z1 的资格
    with connect() as conn:
        conn.execute("DELETE FROM shift_zones WHERE shift_id=2 AND zone_id=1")
        conn.commit()
    # 模拟资格在预复核之后才失效：放行预复核，让触发器在生效瞬间拦截
    monkeypatch.setattr(duty, "shift_qualified", lambda conn, sid, zid: True)

    with pytest.raises(Exception) as ei:
        svc.complete_handover(hid)
    assert ei.value.status_code == 409

    with connect() as conn:
        # 交班单未标成功、无切换证明、旧值班权原样保留（无空窗）
        assert conn.execute(
            "SELECT status FROM handovers WHERE id=?", (hid,)
        ).fetchone()["status"] == "进行中"
        assert conn.execute(
            "SELECT COUNT(*) c FROM duty_transitions WHERE handover_id=?", (hid,)
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT shift_id FROM duty_holders WHERE zone_id=1"
        ).fetchone()["shift_id"] == 1
        denial = conn.execute(
            "SELECT reason FROM operation_denials WHERE handover_id=? "
            "ORDER BY id DESC LIMIT 1",
            (hid,),
        ).fetchone()
        assert denial is not None and "回滚" in denial["reason"]
