"""责任区单值班 + 原子交班 + 在途操作管控 全量验收。

数据基线（fixtures/seed.json）：
- 责任区 Z1 华北：早班#1 持权，可值班 {早班#1, 晚班#2}
- 责任区 Z2 华东：早班#1 持权，可值班 {早班#1, 夜班#3}
- 早班#1 名下在途：批次#1/#2 属 Z1，#3/#4 属 Z2
"""

from pathlib import Path
import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATA_DIR"] = str(ROOT / "backend" / "data_test")
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")

db_path = Path(os.environ["DATA_DIR"]) / "app.db"
if db_path.exists():
    db_path.unlink()

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["项目"] == "交班台"


def test_zones_and_duty_baseline(client):
    zones = {z["id"]: z for z in client.get("/api/zones").json()}
    # 每个责任区恰好一个值班班次，且都是早班#1
    for zid in (1, 2):
        assert zones[zid]["duty_shift_id"] == 1
    # 班次绑定的可值班责任区集合
    shifts = {s["id"]: s for s in client.get("/api/shifts").json()}
    assert {z["id"] for z in shifts[2]["zones"]} == {1}
    assert {z["id"] for z in shifts[3]["zones"]} == {2}
    assert shifts[1]["duty_zone_ids"] == [1, 2]


def test_non_duty_shift_cannot_settle(client):
    """非值班班次结清在途批次 -> 403，错误写明当前值班班次，且原因落库。"""
    r = client.post("/api/batches/1/settle", json={"actor_shift_id": 2})
    assert r.status_code == 403
    assert "无值班权" in r.text
    assert "早班" in r.text  # 写明当前值班班次
    denials = client.get("/api/denials", params={"operation": "结清"}).json()
    assert denials and denials[0]["batch_id"] == 1
    assert denials[0]["zone_id"] == 1


def test_non_duty_shift_cannot_reassign(client):
    r = client.post(
        "/api/batches/1/reassign",
        json={"actor_shift_id": 2, "new_owner_shift_id": 2},
    )
    assert r.status_code == 403
    assert "当前值班班次为" in r.text
    assert client.get("/api/denials", params={"operation": "改归属"}).json()


def test_duty_holder_can_settle(client):
    r = client.post("/api/batches/1/settle", json={"actor_shift_id": 1})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "已结清"


def test_reassign_only_to_current_duty_holder(client):
    """持权班次也不能把在途批次改给非持权班次（防绕过值班权转移）。"""
    ok = client.post(
        "/api/batches/2/reassign",
        json={"actor_shift_id": 1, "new_owner_shift_id": 2},
    )
    assert ok.status_code == 403
    assert "不是责任区" in ok.text
    # 改给自己/当前持权者无意义 -> 400
    same = client.post(
        "/api/batches/2/reassign",
        json={"actor_shift_id": 1, "new_owner_shift_id": 1},
    )
    assert same.status_code == 400


def test_handover_rejected_when_to_shift_not_qualified_for_all_zones(client):
    """跨两个责任区的清单：晚班#2 只绑定 Z1，不满足 Z2 资格 -> 整单拒绝。"""
    r = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 2},
    )
    assert r.status_code == 409
    assert "整单拒绝" in r.text
    assert "华东责任区" in r.text  # 每个责任区都检查了，不只主责任区
    # 留痕按责任区逐条记录
    denials = client.get(
        "/api/denials",
        params={"operation": "发起交班", "zone_id": 2},
    ).json()
    assert denials
    # 整单拒绝：未产生进行中交班单
    assert client.get("/api/handovers").json() == [] or all(
        h["status"] != "进行中" for h in client.get("/api/handovers").json()
    )


def test_handover_rejected_when_from_shift_not_duty_holder(client):
    """非值班班次发起交班 -> 拒绝并写明当前值班班次。"""
    r = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 2, "to_shift_id": 1},
    )
    assert r.status_code in (400, 409)
    assert client.get("/api/denials", params={"operation": "发起交班"}).json()


def test_zone_scoped_handover_atomic_switch(client):
    """只交接 Z1：晚班#2 对 Z1 有资格 -> 成功；Z2 值班权不受影响。"""
    started = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 2, "zone_ids": [1]},
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    assert {z["id"] for z in started.json()["zones"]} == {1}
    # 清单只含 Z1 的在途批次（#1 已结清，剩 #2）
    assert {b["id"] for b in started.json()["items"]} == {2}

    # 完成：同事务卸任#1 + 就任#2
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "已完成"

    zones = {z["id"]: z for z in client.get("/api/zones").json()}
    assert zones[1]["duty_shift_id"] == 2  # Z1 已切换
    assert zones[2]["duty_shift_id"] == 1  # Z2 仍是早班
    # 不存在同一责任区两个值班班次（duty_holders 每区仅一行）
    with connect() as conn:
        dup = conn.execute(
            "SELECT zone_id, COUNT(*) c FROM duty_holders GROUP BY zone_id HAVING c > 1"
        ).fetchall()
        assert dup == []
        # 无空窗：两个责任区都仍有持权行
        assert conn.execute("SELECT COUNT(*) c FROM duty_holders").fetchone()["c"] == 2
    # 批次过户
    b2 = next(b for b in client.get("/api/batches").json() if b["id"] == 2)
    assert b2["owner_shift_id"] == 2
    # 班次状态由值班权派生：#2 值班中，#1 仍持 Z2 故仍值班中
    shifts = {s["id"]: s for s in client.get("/api/shifts").json()}
    assert shifts[2]["status"] == "值班中"
    assert shifts[1]["status"] == "值班中"


def test_duty_permission_follows_handover(client):
    """交班后原持权班次#1 对 Z1 在途批次失去操作权，新持权#2 可操作。"""
    denied = client.post("/api/batches/2/settle", json={"actor_shift_id": 1})
    assert denied.status_code == 403
    assert "当前值班班次为「晚班」" in denied.text
    allowed = client.post("/api/batches/2/settle", json={"actor_shift_id": 2})
    assert allowed.status_code == 200


def test_second_zone_handover_to_qualified_shift(client):
    """Z2 交给夜班#3（绑定 Z2），完成后早班#1 无任何责任区 -> 空闲。"""
    started = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 3, "zone_ids": [2]},
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    zones = {z["id"]: z for z in client.get("/api/zones").json()}
    assert zones[1]["duty_shift_id"] == 2
    assert zones[2]["duty_shift_id"] == 3
    shifts = {s["id"]: s for s in client.get("/api/shifts").json()}
    assert shifts[1]["status"] == "空闲"
    assert shifts[2]["status"] == "值班中"
    assert shifts[3]["status"] == "值班中"
    # 对账报告同源可查
    reports = client.get("/api/reconcile").json()
    assert len(reports) == 2
    assert all(r["一致"] is True for r in reports)


def test_parallel_handover_on_same_zone_rejected(client):
    """同一责任区已有进行中交班 -> 并行发起被拒。"""
    first = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 3, "to_shift_id": 1, "zone_ids": [2]},
    )
    assert first.status_code == 200, first.text
    again = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 3, "to_shift_id": 1, "zone_ids": [2]},
    )
    assert again.status_code == 409
    # 清理：完成第一单，恢复#3持权
    client.post(f"/api/handovers/{first.json()['id']}/complete")


def test_duty_transition_proof_api_and_same_source(client):
    """切换证明只读接口可按责任区/交班单查，交班详情与责任区界面同源带证明。"""
    # 此时点 Z2 持权者为早班#1，夜班#3 绑定 Z2，交回给#3
    started = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 3, "zone_ids": [2]},
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text

    # 只读接口：按交班单 id
    by_h = client.get("/api/duty-transitions", params={"handover_id": hid}).json()
    assert len(by_h) == 1
    proof = by_h[0]
    assert proof["zone_id"] == 2
    assert proof["from_shift_id"] == 1
    assert proof["to_shift_id"] == 3
    assert proof["handover_id"] == hid
    assert proof["switched_at"]
    assert proof["zone_name"] == "华东责任区"
    assert proof["from_shift_name"] == "早班"
    assert proof["to_shift_name"] == "夜班"

    # 只读接口：按责任区
    by_z = client.get("/api/duty-transitions", params={"zone_id": 2}).json()
    assert any(p["handover_id"] == hid for p in by_z)
    assert all(p["zone_id"] == 2 for p in by_z)

    # 交班详情内嵌证明，与独立接口同源
    detail = client.get(f"/api/handovers/{hid}").json()
    assert len(detail["transitions"]) == 1
    assert detail["transitions"][0]["id"] == proof["id"]
    listed = next(h for h in client.get("/api/handovers").json() if h["id"] == hid)
    assert listed["transitions"][0]["id"] == proof["id"]

    # 责任区界面带当前值班与最近切换证明字段
    z2 = next(z for z in client.get("/api/zones").json() if z["id"] == 2)
    assert z2["duty_shift_id"] == 3
    last = z2["last_transition"]
    assert last is not None
    assert last["handover_id"] == hid
    assert last["from_shift_id"] == 1 and last["to_shift_id"] == 3


def test_no_dual_duty_and_no_gap_after_every_completion(client):
    """任意时刻不变量：每区恰好一行持权（无双值班、无空窗）。"""
    with connect() as conn:
        # 全流程跑完后逐区核对
        dup = conn.execute(
            "SELECT zone_id, COUNT(*) c FROM duty_holders GROUP BY zone_id HAVING c <> 1"
        ).fetchall()
        assert dup == []
        zone_total = conn.execute("SELECT COUNT(*) c FROM zones").fetchone()["c"]
        holder_total = conn.execute("SELECT COUNT(*) c FROM duty_holders").fetchone()["c"]
        assert holder_total == zone_total
        # 证明条数 = 已完成交班覆盖的责任区次数
        proof_count = conn.execute(
            "SELECT COUNT(*) c FROM duty_transitions"
        ).fetchone()["c"]
        covered = conn.execute(
            "SELECT COUNT(*) c FROM handover_zones hz "
            "JOIN handovers h ON h.id = hz.handover_id WHERE h.status = '已完成'"
        ).fetchone()["c"]
        assert proof_count == covered


def test_denials_query_filters(client):
    """失败原因可按操作类型/责任区/班次过滤，且与业务库同源。"""
    all_denials = client.get("/api/denials").json()
    assert len(all_denials) >= 5
    settle_only = client.get("/api/denials", params={"operation": "结清"}).json()
    assert all(d["operation"] == "结清" for d in settle_only)
    z1_only = client.get("/api/denials", params={"zone_id": 1}).json()
    assert all(d["zone_id"] == 1 for d in z1_only)
