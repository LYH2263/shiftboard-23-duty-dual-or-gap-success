from __future__ import annotations

import json
from pathlib import Path

from app.db import connect, fixtures_dir


# 独立值班权表：责任区 -> 当前值班班次。
# zone_id 主键 => 同一责任区同一时刻至多一个值班班次（数据库级，应用层无法插第二行）；
# 就任资格由触发器经 shift_zones 校验（SQLite 无法对复合键建外键，用触发器等价保证）；
# duty_transitions 为持久化值班切换证明：区、旧值班、新值班、切换时刻、交班单 id。
SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shift_zones (
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  zone_id INTEGER NOT NULL REFERENCES zones(id),
  PRIMARY KEY (shift_id, zone_id)
);
CREATE TABLE IF NOT EXISTS duty_holders (
  zone_id INTEGER PRIMARY KEY REFERENCES zones(id),
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  since TEXT NOT NULL,
  handover_id INTEGER REFERENCES handovers(id)
);
CREATE TRIGGER IF NOT EXISTS trg_duty_holders_qualified_insert
BEFORE INSERT ON duty_holders
BEGIN
  SELECT CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM shift_zones
      WHERE shift_id = NEW.shift_id AND zone_id = NEW.zone_id
    )
    THEN RAISE(ABORT, '该班次未绑定此责任区，无值班资格，禁止就任')
  END;
END;
CREATE TRIGGER IF NOT EXISTS trg_duty_holders_qualified_update
BEFORE UPDATE OF zone_id, shift_id ON duty_holders
BEGIN
  SELECT CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM shift_zones
      WHERE shift_id = NEW.shift_id AND zone_id = NEW.zone_id
    )
    THEN RAISE(ABORT, '该班次未绑定此责任区，无值班资格，禁止就任')
  END;
END;
-- 值班切换证明（只追加，不更新不删除）：每次完成交班逐区落一行
CREATE TABLE IF NOT EXISTS duty_transitions (
  id INTEGER PRIMARY KEY,
  zone_id INTEGER NOT NULL REFERENCES zones(id),
  from_shift_id INTEGER REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  switched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_duty_transitions_zone
  ON duty_transitions(zone_id, switched_at DESC);
CREATE INDEX IF NOT EXISTS idx_duty_transitions_handover
  ON duty_transitions(handover_id);
CREATE TABLE IF NOT EXISTS handover_zones (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  zone_id INTEGER NOT NULL REFERENCES zones(id),
  PRIMARY KEY (handover_id, zone_id)
);
-- 在途操作/交班被拒原因留档，与业务数据同源（同一 SQLite 库）
CREATE TABLE IF NOT EXISTS operation_denials (
  id INTEGER PRIMARY KEY,
  operation TEXT NOT NULL,
  actor_shift_id INTEGER REFERENCES shifts(id),
  zone_id INTEGER REFERENCES zones(id),
  batch_id INTEGER REFERENCES batches(id),
  handover_id INTEGER REFERENCES handovers(id),
  reason TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
"""

# 老库（07 之前的快照）补齐新列：(表, 列, 建列 DDL)
LEGACY_MIGRATIONS = [
    (
        "batches",
        "zone_id",
        "ALTER TABLE batches ADD COLUMN zone_id INTEGER REFERENCES zones(id)",
    ),
]


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _duty_holders_schema_broken(conn) -> bool:
    """植入回归的旧库：duty_holders 用代理 id 主键、zone_id 可重复（双值班）。"""
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(duty_holders)")}
    if "zone_id" not in cols:
        return True
    # zone_id 不是主键 => 同一责任区可写多行，约束失效
    return not bool(cols["zone_id"]["pk"])


def _rebuild_duty_holders(conn) -> None:
    """把被破坏的值班权表重建为 zone_id 主键表，并合并已产生的双值班行。

    同一责任区多行时保留“最近一次切换”的持权行（handover_id 最大，
    其次 since 最新、id 最大），其余删除；重建后由主键与触发器兜底。
    """
    rows = conn.execute(
        """
        SELECT zone_id, shift_id, since, handover_id FROM duty_holders
        ORDER BY zone_id,
                 COALESCE(handover_id, -1),
                 since,
                 rowid DESC
        """
    ).fetchall()
    keep: dict[int, dict] = {}
    for r in rows:  # 排序后同区最后一行即最新持权
        keep[r["zone_id"]] = dict(r)

    conn.execute("DROP TRIGGER IF EXISTS trg_duty_holders_qualified_insert")
    conn.execute("DROP TRIGGER IF EXISTS trg_duty_holders_qualified_update")
    conn.execute("DROP TABLE IF EXISTS duty_holders_old")
    conn.execute("ALTER TABLE duty_holders RENAME TO duty_holders_old")
    conn.execute(
        """
        CREATE TABLE duty_holders (
          zone_id INTEGER PRIMARY KEY REFERENCES zones(id),
          shift_id INTEGER NOT NULL REFERENCES shifts(id),
          since TEXT NOT NULL,
          handover_id INTEGER REFERENCES handovers(id)
        )
        """
    )
    for zid, row in keep.items():
        # 只回填仍具备资格的持权；不臆造无资格值班权（交给人工/后续交班修复）
        qualified = conn.execute(
            "SELECT 1 FROM shift_zones WHERE zone_id = ? AND shift_id = ?",
            (zid, row["shift_id"]),
        ).fetchone()
        if qualified:
            conn.execute(
                """
                INSERT INTO duty_holders(zone_id, shift_id, since, handover_id)
                VALUES (?, ?, ?, ?)
                """,
                (zid, row["shift_id"], row["since"], row["handover_id"]),
            )
    conn.execute("DROP TABLE duty_holders_old")
    conn.executescript(
        """
        CREATE TRIGGER trg_duty_holders_qualified_insert
        BEFORE INSERT ON duty_holders
        BEGIN
          SELECT CASE
            WHEN NOT EXISTS (
              SELECT 1 FROM shift_zones
              WHERE shift_id = NEW.shift_id AND zone_id = NEW.zone_id
            )
            THEN RAISE(ABORT, '该班次未绑定此责任区，无值班资格，禁止就任')
          END;
        END;
        CREATE TRIGGER trg_duty_holders_qualified_update
        BEFORE UPDATE OF zone_id, shift_id ON duty_holders
        BEGIN
          SELECT CASE
            WHEN NOT EXISTS (
              SELECT 1 FROM shift_zones
              WHERE shift_id = NEW.shift_id AND zone_id = NEW.zone_id
            )
            THEN RAISE(ABORT, '该班次未绑定此责任区，无值班资格，禁止就任')
          END;
        END;
        """
    )


def init_db() -> None:
    with connect() as conn:
        # shifts / batches / handovers / handover_items / reconcile_reports
        # 沿用历史建表语句，保证老库与新库结构一致
        conn.executescript(_LEGACY_SCHEMA)

        if _table_exists(conn, "duty_holders") and _duty_holders_schema_broken(conn):
            # 修复植入回归的旧库：先重建值班权表（清双值班），再建新表与触发器
            _rebuild_duty_holders(conn)

        conn.executescript(SCHEMA)

        for table, column, ddl in LEGACY_MIGRATIONS:
            if _table_exists(conn, table) and not _column_exists(conn, table, column):
                conn.execute(ddl)

        n = conn.execute("SELECT COUNT(*) AS c FROM shifts").fetchone()["c"]
        if n:
            # 老快照补数据：没有责任区的在途批次不臆造归属，留空待人工指派；
            # duty_holders 同样不臆造，避免凭空产生值班权。
            conn.commit()
            return

        seed_path = fixtures_dir() / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        now = _now()
        for z in data["zones"]:
            conn.execute(
                "INSERT INTO zones(id, code, name) VALUES (?, ?, ?)",
                (z["id"], z["code"], z["name"]),
            )
        for s in data["shifts"]:
            conn.execute(
                "INSERT INTO shifts(id, name, keeper, status) VALUES (?, ?, ?, ?)",
                (s["id"], s["name"], s["keeper"], s["status"]),
            )
            for zid in s.get("zone_ids", []):
                conn.execute(
                    "INSERT INTO shift_zones(shift_id, zone_id) VALUES (?, ?)",
                    (s["id"], zid),
                )
        for d in data.get("duty_holders", []):
            conn.execute(
                "INSERT INTO duty_holders(zone_id, shift_id, since) VALUES (?, ?, ?)",
                (d["zone_id"], d["shift_id"], now),
            )
        for b in data["batches"]:
            conn.execute(
                """
                INSERT INTO batches(id, biz_key, title, owner_shift_id, zone_id, status, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b["id"],
                    b["biz_key"],
                    b["title"],
                    b["owner_shift_id"],
                    b.get("zone_id"),
                    b["status"],
                    b.get("note", ""),
                ),
            )
        conn.commit()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  keeper TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲'
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  biz_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_shift_id INTEGER REFERENCES shifts(id),
  status TEXT NOT NULL DEFAULT '在途',
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  status TEXT NOT NULL,
  batch_count INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS handover_items (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  PRIMARY KEY (handover_id, batch_id)
);
CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER REFERENCES handovers(id),
  before_count INTEGER NOT NULL,
  after_from_count INTEGER NOT NULL,
  after_to_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""
