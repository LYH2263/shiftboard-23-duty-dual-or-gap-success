"""数据库级保证：单值班唯一约束、原子切换回滚无空窗、老库迁移。"""

from pathlib import Path
import sys

import pytest
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FIXTURES_DIR", str(ROOT / "fixtures"))
    from app import seed
    seed.init_db()
    from app import db as db_mod
    yield db_mod


def test_duty_holders_unique_per_zone(fresh_db):
    """同一责任区插入第二个值班班次 -> 约束拒绝，数据库层面不可能双值班。"""
    conn = fresh_db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO duty_holders(zone_id, shift_id, since) VALUES (1, 2, 'x')"
        )
    conn.rollback()
    # 原值班权未受影响
    row = conn.execute(
        "SELECT shift_id FROM duty_holders WHERE zone_id = 1"
    ).fetchone()
    assert row["shift_id"] == 1
    conn.close()


def test_unqualified_shift_cannot_hold_duty(fresh_db):
    """未绑定责任区的班次不能就任值班权（外键经 shift_zones 校验）。"""
    conn = fresh_db.connect()
    # 晚班#2 未绑定 Z2，直接插 Z2->#2 必须失败
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO duty_holders(zone_id, shift_id, since) VALUES (2, 2, 'x')"
        )
    conn.rollback()
    conn.close()


def test_atomic_switch_rollback_leaves_no_gap(fresh_db):
    """模拟切换中途失败：条件 UPDATE 未命中时，旧值班权原样保留、无空窗。"""
    conn = fresh_db.connect()
    before = dict(
        conn.execute("SELECT * FROM duty_holders WHERE zone_id = 1").fetchone()
    )
    # WHERE 条件不成立（持权者不是#3）=> rowcount=0，调用方必须回滚
    cur = conn.execute(
        """
        UPDATE duty_holders SET shift_id = 2, since = 'y'
        WHERE zone_id = 1 AND shift_id = 3
        """
    )
    assert cur.rowcount == 0
    conn.rollback()
    after = dict(
        conn.execute("SELECT * FROM duty_holders WHERE zone_id = 1").fetchone()
    )
    # 回滚后持权行仍在（无空窗），持权班次不变
    assert after == before
    holders = conn.execute("SELECT COUNT(*) c FROM duty_holders").fetchone()["c"]
    assert holders == 2  # 两个责任区都有人值班
    conn.close()


def test_service_rollback_on_conflict(fresh_db):
    """完整服务路径：生效瞬间值班权已被他人改走 -> 409 回滚，绝不标成功。"""
    from app.services import handover as svc
    # 发起一单 Z1：#1 -> #2
    h = svc.start_handover(1, 2, [1])
    hid = h["id"]
    # 模拟并发：另一事务把 Z1 值班权先交给#2（#2 本就绑定 Z1，可直接改）
    conn = fresh_db.connect()
    conn.execute(
        "UPDATE duty_holders SET shift_id = 2 WHERE zone_id = 1 AND shift_id = 1"
    )
    conn.commit()
    conn.close()
    # 此时完成交班：预复核发现 Z1 已不属于交班方#1 -> 拒绝
    with pytest.raises(Exception) as ei:
        svc.complete_handover(hid)
    assert ei.value.status_code == 409
    # 交班单未标成功
    conn = fresh_db.connect()
    status = conn.execute(
        "SELECT status FROM handovers WHERE id = ?", (hid,)
    ).fetchone()["status"]
    assert status == "进行中"
    # 失败原因同源留痕
    denial = conn.execute(
        "SELECT reason FROM operation_denials WHERE handover_id = ? ORDER BY id DESC LIMIT 1",
        (hid,),
    ).fetchone()
    assert denial is not None and "回滚" in denial["reason"]
    conn.close()


def test_legacy_schema_migration(tmp_path, monkeypatch):
    """07 之前的老库（无 zones/zone_id）启动后自动补表补列，老数据不丢。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "legacy"))
    monkeypatch.setenv("FIXTURES_DIR", str(ROOT / "fixtures"))
    from app import db as db_mod, seed

    # 手工建一个只有老结构的库并写数据
    conn = db_mod.connect()
    conn.executescript(
        """
        CREATE TABLE shifts (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
          keeper TEXT NOT NULL, status TEXT NOT NULL DEFAULT '空闲'
        );
        CREATE TABLE batches (
          id INTEGER PRIMARY KEY, biz_key TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL, owner_shift_id INTEGER,
          status TEXT NOT NULL DEFAULT '在途', note TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO shifts VALUES (1, '老早班', '阿陈', '值班中');
        INSERT INTO batches(id, biz_key, title, owner_shift_id, status)
        VALUES (1, 'OLD-1', '老批次', 1, '在途');
        """
    )
    conn.commit()
    conn.close()

    seed.init_db()

    conn = db_mod.connect()
    # 新列存在且老批次 zone_id 留空（不臆造归属）
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(batches)")]
    assert "zone_id" in cols
    row = conn.execute("SELECT * FROM batches WHERE id = 1").fetchone()
    assert row["biz_key"] == "OLD-1"
    assert row["zone_id"] is None
    # 新表就位
    for t in ("zones", "shift_zones", "duty_holders", "operation_denials"):
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (t,)
        ).fetchone()
    conn.close()
