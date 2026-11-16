"""责任区 / 值班权 / 在途操作管控。

单值班不变量由 SQLite 约束保证：
- duty_holders.zone_id 主键 => 每个责任区至多一行 => 同一责任区同一时刻至多一个值班班次；
- duty_holders(zone_id, shift_id) 外键经 shift_zones 校验 => 只有绑定该区的班次能就任；
- 交班在同一事务内原地 UPDATE duty_holders => 不存在“先卸任后就任”的空窗。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 留痕

def record_denial(
    operation: str,
    reason: str,
    *,
    actor_shift_id: int | None = None,
    zone_id: int | None = None,
    batch_id: int | None = None,
    handover_id: int | None = None,
    detail: dict | None = None,
) -> None:
    """操作被拒原因落库。独立连接写入，不随业务事务回滚而丢失。"""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO operation_denials(
              operation, actor_shift_id, zone_id, batch_id, handover_id,
              reason, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation,
                actor_shift_id,
                zone_id,
                batch_id,
                handover_id,
                reason,
                json.dumps(detail or {}, ensure_ascii=False),
                now(),
            ),
        )
        conn.commit()


def list_denials(
    operation: str | None = None,
    zone_id: int | None = None,
    actor_shift_id: int | None = None,
) -> list[dict]:
    sql = """
        SELECT d.*,
               z.name AS zone_name,
               s.name AS actor_shift_name,
               b.biz_key AS batch_biz_key
        FROM operation_denials d
        LEFT JOIN zones z ON z.id = d.zone_id
        LEFT JOIN shifts s ON s.id = d.actor_shift_id
        LEFT JOIN batches b ON b.id = d.batch_id
    """
    conds, params = [], []
    if operation:
        conds.append("d.operation = ?")
        params.append(operation)
    if zone_id is not None:
        conds.append("d.zone_id = ?")
        params.append(zone_id)
    if actor_shift_id is not None:
        conds.append("d.actor_shift_id = ?")
        params.append(actor_shift_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY d.id DESC"
    with connect() as conn:
        rows = rows_to_dicts(conn.execute(sql, params).fetchall())
    for r in rows:
        r["detail"] = json.loads(r.pop("detail_json"))
    return rows


# ---------------------------------------------------------------- 查询

def list_zones() -> list[dict]:
    with connect() as conn:
        zones = rows_to_dicts(
            conn.execute(
                """
                SELECT z.*, dh.shift_id AS duty_shift_id, dh.since AS duty_since,
                       s.name AS duty_shift_name, s.keeper AS duty_keeper
                FROM zones z
                LEFT JOIN duty_holders dh ON dh.zone_id = z.id
                LEFT JOIN shifts s ON s.id = dh.shift_id
                ORDER BY z.id
                """
            ).fetchall()
        )
        for z in zones:
            z["qualified_shifts"] = rows_to_dicts(
                conn.execute(
                    """
                    SELECT s.id, s.name, s.keeper
                    FROM shift_zones sz
                    JOIN shifts s ON s.id = sz.shift_id
                    WHERE sz.zone_id = ?
                    ORDER BY s.id
                    """,
                    (z["id"],),
                ).fetchall()
            )
        return zones


def get_duty_holder(conn, zone_id: int):
    return conn.execute(
        """
        SELECT dh.*, s.name AS shift_name, s.keeper
        FROM duty_holders dh
        JOIN shifts s ON s.id = dh.shift_id
        WHERE dh.zone_id = ?
        """,
        (zone_id,),
    ).fetchone()


def shift_qualified(conn, shift_id: int, zone_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM shift_zones WHERE shift_id = ? AND zone_id = ?",
        (shift_id, zone_id),
    ).fetchone() is not None


def qualified_shift_names(conn, zone_id: int) -> str:
    rows = conn.execute(
        """
        SELECT s.id, s.name FROM shift_zones sz
        JOIN shifts s ON s.id = sz.shift_id
        WHERE sz.zone_id = ? ORDER BY s.id
        """,
        (zone_id,),
    ).fetchall()
    return "、".join(f"{r['name']}(#{r['id']})" for r in rows) or "无"


def refresh_shift_status(conn, *shift_ids: int) -> None:
    """班次状态由值班权派生：持有任一责任区 => 值班中，否则空闲。"""
    for sid in {s for s in shift_ids if s is not None}:
        holds = conn.execute(
            "SELECT 1 FROM duty_holders WHERE shift_id = ? LIMIT 1", (sid,)
        ).fetchone()
        conn.execute(
            "UPDATE shifts SET status = ? WHERE id = ?",
            ("值班中" if holds else "空闲", sid),
        )


# ---------------------------------------------------------------- 操作管控

def _deny(
    operation: str,
    reason: str,
    *,
    actor_shift_id: int | None,
    zone_id: int | None,
    batch_id: int | None = None,
    handover_id: int | None = None,
    detail: dict | None = None,
    status_code: int = 403,
) -> None:
    record_denial(
        operation,
        reason,
        actor_shift_id=actor_shift_id,
        zone_id=zone_id,
        batch_id=batch_id,
        handover_id=handover_id,
        detail=detail,
    )
    raise HTTPException(status_code, reason)


def _batch_label(batch) -> str:
    return f"{batch['biz_key']}(#{batch['id']})"


def assert_batch_operable(conn, batch, actor_shift_id: int, operation: str) -> None:
    """非值班班次禁止对责任区在途批次操作；冲突写明当前值班班次。"""
    actor = conn.execute(
        "SELECT * FROM shifts WHERE id = ?", (actor_shift_id,)
    ).fetchone()
    if not actor:
        _deny(
            operation,
            f"操作班次#{actor_shift_id}不存在",
            actor_shift_id=actor_shift_id,
            zone_id=None,
            batch_id=batch["id"] if batch else None,
            status_code=404,
        )
    if batch["status"] != "在途":
        _deny(
            operation,
            f"批次{_batch_label(batch)}不是在途状态（当前：{batch['status']}），禁止{operation}",
            actor_shift_id=actor_shift_id,
            zone_id=batch["zone_id"],
            batch_id=batch["id"],
            status_code=400,
        )
    zone = conn.execute("SELECT * FROM zones WHERE id = ?", (batch["zone_id"],)).fetchone()
    if not zone:
        _deny(
            operation,
            f"批次{_batch_label(batch)}未归属任何责任区，无法校验值班权",
            actor_shift_id=actor_shift_id,
            zone_id=None,
            batch_id=batch["id"],
            status_code=409,
        )
    holder = get_duty_holder(conn, zone["id"])
    if holder is None:
        _deny(
            operation,
            f"责任区「{zone['name']}」当前无值班班次，禁止{operation}",
            actor_shift_id=actor_shift_id,
            zone_id=zone["id"],
            batch_id=batch["id"],
            status_code=409,
        )
    if holder["shift_id"] != actor_shift_id:
        _deny(
            operation,
            (
                f"班次「{actor['name']}」对责任区「{zone['name']}」无值班权，"
                f"禁止{operation}；当前值班班次为「{holder['shift_name']}」"
                f"（#{holder['shift_id']}，值班人 {holder['keeper']}）"
            ),
            actor_shift_id=actor_shift_id,
            zone_id=zone["id"],
            batch_id=batch["id"],
        )
