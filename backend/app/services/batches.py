"""在途批次操作：结清 / 改归属。

所有写操作先经 duty.assert_batch_operable 校验值班权，
非值班班次一律拒绝且错误信息写明当前值班班次，拒绝原因同源落库。
"""

from __future__ import annotations

from fastapi import HTTPException

from app.db import connect
from app.services import duty


def settle_batch(batch_id: int, actor_shift_id: int) -> dict:
    with connect() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        # 校验不过会留痕并抛 HTTPException
        duty.assert_batch_operable(conn, batch, actor_shift_id, "结清")
        conn.execute(
            "UPDATE batches SET status = '已结清' WHERE id = ? AND status = '在途'",
            (batch_id,),
        )
        conn.commit()
    return _get(batch_id)


def reassign_batch(batch_id: int, actor_shift_id: int, new_owner_shift_id: int) -> dict:
    with connect() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        target = conn.execute(
            "SELECT * FROM shifts WHERE id = ?", (new_owner_shift_id,)
        ).fetchone()
        if not target:
            raise HTTPException(404, f"目标班次#{new_owner_shift_id}不存在")

        # 值班权校验优先：非值班班次直接拒绝，不暴露后续规则
        duty.assert_batch_operable(conn, batch, actor_shift_id, "改归属")

        if actor_shift_id == new_owner_shift_id:
            raise HTTPException(400, "新归属班次与当前操作班次相同")

        # 改归属只允许交给该责任区的当前值班班次，避免绕过值班权转移在途批次
        holder = duty.get_duty_holder(conn, batch["zone_id"])
        if holder["shift_id"] != new_owner_shift_id:
            zone = conn.execute(
                "SELECT * FROM zones WHERE id = ?", (batch["zone_id"],)
            ).fetchone()
            reason = (
                f"不能把在途批次改归属给「{target['name']}」：其不是责任区"
                f"「{zone['name']}」的当前值班班次；当前值班班次为"
                f"「{holder['shift_name']}」(#{holder['shift_id']})"
            )
            duty.record_denial(
                "改归属", reason,
                actor_shift_id=actor_shift_id,
                zone_id=batch["zone_id"],
                batch_id=batch_id,
                detail={"new_owner_shift_id": new_owner_shift_id},
            )
            raise HTTPException(403, reason)

        conn.execute(
            "UPDATE batches SET owner_shift_id = ? WHERE id = ? AND status = '在途'",
            (new_owner_shift_id, batch_id),
        )
        conn.commit()
    return _get(batch_id)


def _get(batch_id: int) -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT b.*, s.name AS owner_shift_name,
                   z.name AS zone_name, ds.name AS duty_shift_name
            FROM batches b
            LEFT JOIN shifts s ON s.id = b.owner_shift_id
            LEFT JOIN zones z ON z.id = b.zone_id
            LEFT JOIN duty_holders dh ON dh.zone_id = b.zone_id
            LEFT JOIN shifts ds ON ds.id = dh.shift_id
            WHERE b.id = ?
            """,
            (batch_id,),
        ).fetchone()
        return dict(row)
