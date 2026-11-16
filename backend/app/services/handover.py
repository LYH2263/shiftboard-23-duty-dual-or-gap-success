"""交班主服务：责任区资格校验 + 单值班约束 + 同事务原子交接。

约束落点：
1. duty_holders.zone_id 主键 —— 同一责任区至多一个值班班次（数据库级保证）；
2. 完成交班时对每个责任区执行条件原地 UPDATE（WHERE shift_id = 交班方），
   一条 UPDATE 同时完成卸任+就任，不存在先删后插的空窗；rowcount 不为 1
   即整事务回滚 —— 杜绝“卸任成功就任失败”的空窗与假成功；
3. 每次切换逐区追加 duty_transitions 持久化证明（区、旧值班、新值班、
   切换时刻、交班单 id），与业务数据同库同源，只读接口可按区/交班单查询；
4. 发起前对清单内【每个】责任区逐一校验双方资格与单值班约束，任一不过整单拒绝。
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import HTTPException

from app.db import connect, rows_to_dicts
from app.services import duty


class _HandoverConflict(Exception):
    """事务内发现值班权已被改写，触发回滚后留痕。"""


def _now() -> str:
    return duty.now()


# ---------------------------------------------------------------- 班次 / 批次查询

def list_shifts() -> list[dict]:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        for s in shifts:
            s["zones"] = rows_to_dicts(
                conn.execute(
                    """
                    SELECT z.id, z.code, z.name,
                           dh.shift_id IS NOT NULL AS on_duty
                    FROM shift_zones sz
                    JOIN zones z ON z.id = sz.zone_id
                    LEFT JOIN duty_holders dh
                      ON dh.zone_id = z.id AND dh.shift_id = sz.shift_id
                    WHERE sz.shift_id = ?
                    ORDER BY z.id
                    """,
                    (s["id"],),
                ).fetchall()
            )
            s["duty_zone_ids"] = [
                r["id"]
                for r in conn.execute(
                    "SELECT zone_id AS id FROM duty_holders WHERE shift_id = ? ORDER BY zone_id",
                    (s["id"],),
                ).fetchall()
            ]
        return shifts


def list_batches(owner_shift_id: int | None = None, zone_id: int | None = None) -> list[dict]:
    sql = """
        SELECT b.*, s.name AS owner_shift_name,
               z.name AS zone_name, z.code AS zone_code,
               dh.shift_id AS duty_shift_id, ds.name AS duty_shift_name
        FROM batches b
        LEFT JOIN shifts s ON s.id = b.owner_shift_id
        LEFT JOIN zones z ON z.id = b.zone_id
        LEFT JOIN duty_holders dh ON dh.zone_id = b.zone_id
        LEFT JOIN shifts ds ON ds.id = dh.shift_id
    """
    conds, params = [], []
    if owner_shift_id is not None:
        conds.append("b.owner_shift_id = ?")
        params.append(owner_shift_id)
    if zone_id is not None:
        conds.append("b.zone_id = ?")
        params.append(zone_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY b.id"
    with connect() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def list_handovers() -> list[dict]:
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT h.*,
                       fs.name AS from_shift_name,
                       ts.name AS to_shift_name
                FROM handovers h
                JOIN shifts fs ON fs.id = h.from_shift_id
                JOIN shifts ts ON ts.id = h.to_shift_id
                ORDER BY h.id DESC
                """
            ).fetchall()
        )
        for h in rows:
            h["zones"] = _handover_zones(conn, h["id"])
            h["transitions"] = duty.transitions_for_handover(conn, h["id"])
        return rows


def _handover_zones(conn, handover_id: int) -> list[dict]:
    return rows_to_dicts(
        conn.execute(
            """
            SELECT z.id, z.code, z.name
            FROM handover_zones hz
            JOIN zones z ON z.id = hz.zone_id
            WHERE hz.handover_id = ?
            ORDER BY z.id
            """,
            (handover_id,),
        ).fetchall()
    )


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.*, z.name AS zone_name
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            LEFT JOIN zones z ON z.id = b.zone_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        out["zones"] = _handover_zones(conn, handover_id)
        out["transitions"] = duty.transitions_for_handover(conn, handover_id)
        return out


def dashboard() -> dict:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = '在途'"
        ).fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = '进行中'"
        ).fetchone()["c"]
        zones = rows_to_dicts(
            conn.execute(
                """
                SELECT z.id, z.name, dh.shift_id, s.name AS shift_name, s.keeper,
                       dh.since, dh.handover_id
                FROM zones z
                LEFT JOIN duty_holders dh ON dh.zone_id = z.id
                LEFT JOIN shifts s ON s.id = dh.shift_id
                ORDER BY z.id
                """
            ).fetchall()
        )
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "班次数": len(shifts),
            "责任区数": len(zones),
            "在途批次数": inflight,
            "进行中交班": active,
            "最近交班": dict(last) if last else None,
            "班次": shifts,
            "责任区值班": zones,
        }


# ---------------------------------------------------------------- 发起交班

def start_handover(
    from_shift_id: int, to_shift_id: int, zone_ids: list[int] | None = None
) -> dict:
    if from_shift_id == to_shift_id:
        raise HTTPException(400, "交班双方不能是同一班次")

    with connect() as conn:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")

        # 清单 = 交班方名下在途批次；按责任区分组，跨区逐区校验
        batches = conn.execute(
            """
            SELECT b.* FROM batches b
            WHERE b.owner_shift_id = ? AND b.status = '在途' AND b.zone_id IS NOT NULL
            ORDER BY b.zone_id, b.id
            """,
            (from_shift_id,),
        ).fetchall()
        if not batches:
            duty.record_denial(
                "发起交班", "交班方没有可交接的在途批次",
                actor_shift_id=from_shift_id,
                detail={"to_shift_id": to_shift_id},
            )
            raise HTTPException(400, "交班方没有可交接的在途批次")

        zones_in_list = sorted({b["zone_id"] for b in batches})
        if zone_ids:
            unknown = sorted(set(zone_ids) - set(zones_in_list))
            if unknown:
                raise HTTPException(400, f"责任区{unknown}不在交班方在途清单内")
            zones_in_list = [z for z in zones_in_list if z in set(zone_ids)]
            if not zones_in_list:
                raise HTTPException(400, "所选责任区没有可交接的在途批次")

        # 已有进行中的交班占用任一相关责任区/班次 => 拒绝
        busy = conn.execute(
            """
            SELECT h.id, hz.zone_id
            FROM handovers h
            JOIN handover_zones hz ON hz.handover_id = h.id
            WHERE h.status = '进行中' AND hz.zone_id IN (%s)
            """ % ",".join("?" * len(zones_in_list)),
            zones_in_list,
        ).fetchall()
        if busy:
            ids = sorted({r["id"] for r in busy})
            reason = f"已有进行中的交班单#{ids[0]}占用相关责任区，禁止并行交班"
            duty.record_denial(
                "发起交班", reason,
                actor_shift_id=from_shift_id,
                handover_id=ids[0],
                detail={"busy_handover_ids": ids, "zone_ids": zones_in_list},
            )
            raise HTTPException(409, reason)

        # 逐责任区校验：双方资格 + 单值班。任一不过 => 整单拒绝（禁止只查主责任区）
        failures: list[str] = []
        for zid in zones_in_list:
            zone = conn.execute("SELECT * FROM zones WHERE id = ?", (zid,)).fetchone()
            holder = duty.get_duty_holder(conn, zid)
            if holder is None:
                failures.append(
                    f"责任区「{zone['name']}」当前无值班班次，无法交班"
                )
            elif holder["shift_id"] != from_shift_id:
                failures.append(
                    f"责任区「{zone['name']}」当前值班班次为「{holder['shift_name']}」"
                    f"(#{holder['shift_id']})，交班方「{fs['name']}」无该区值班权"
                )
            if not duty.shift_qualified(conn, to_shift_id, zid):
                failures.append(
                    f"接班方「{ts['name']}」未绑定责任区「{zone['name']}」，"
                    f"无值班资格（可值班班次：{duty.qualified_shift_names(conn, zid)}）"
                )
        if failures:
            reason = "交班单整单拒绝：" + "；".join(failures)
            for zid in zones_in_list:
                duty.record_denial(
                    "发起交班", reason,
                    actor_shift_id=from_shift_id,
                    zone_id=zid,
                    detail={"to_shift_id": to_shift_id, "failures": failures},
                )
            raise HTTPException(409, reason)

        target_batch_ids = [b["id"] for b in batches if b["zone_id"] in zones_in_list]
        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (?, ?, '进行中', ?, ?)
            """,
            (from_shift_id, to_shift_id, len(target_batch_ids), _now()),
        )
        hid = cur.lastrowid
        for bid in target_batch_ids:
            conn.execute(
                "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
                (hid, bid),
            )
        for zid in zones_in_list:
            conn.execute(
                "INSERT INTO handover_zones(handover_id, zone_id) VALUES (?, ?)",
                (hid, zid),
            )
        conn.execute("UPDATE shifts SET status = '交班中' WHERE id = ?", (from_shift_id,))
        conn.execute("UPDATE shifts SET status = '接班中' WHERE id = ?", (to_shift_id,))
        conn.commit()
    return get_handover(hid)


# ---------------------------------------------------------------- 完成交班

def complete_handover(handover_id: int) -> dict:
    try:
        with connect() as conn:
            h = conn.execute(
                "SELECT * FROM handovers WHERE id = ?", (handover_id,)
            ).fetchone()
            if not h:
                raise HTTPException(404, "交班单不存在")
            if h["status"] != "进行中":
                raise HTTPException(400, "只能完成进行中的交班单")

            zones = [
                r["zone_id"]
                for r in conn.execute(
                    "SELECT zone_id FROM handover_zones WHERE handover_id = ? ORDER BY zone_id",
                    (handover_id,),
                ).fetchall()
            ]
            items = conn.execute(
                "SELECT batch_id FROM handover_items WHERE handover_id = ?",
                (handover_id,),
            ).fetchall()
            before = len(items)

            # 生效前复核：每个责任区仍由交班方持权、接班方仍有资格
            pre_failures: list[str] = []
            for zid in zones:
                holder = duty.get_duty_holder(conn, zid)
                zone = conn.execute("SELECT * FROM zones WHERE id = ?", (zid,)).fetchone()
                if holder is None:
                    pre_failures.append(f"责任区「{zone['name']}」无值班班次")
                elif holder["shift_id"] != h["from_shift_id"]:
                    pre_failures.append(
                        f"责任区「{zone['name']}」值班权已属于"
                        f"「{holder['shift_name']}」(#{holder['shift_id']})"
                    )
                if not duty.shift_qualified(conn, h["to_shift_id"], zid):
                    pre_failures.append(
                        f"接班方对责任区「{zone['name']}」无值班资格"
                    )
            if pre_failures:
                reason = "交班生效失败，已整单回滚：" + "；".join(pre_failures)
                for zid in zones:
                    duty.record_denial(
                        "完成交班", reason,
                        actor_shift_id=h["from_shift_id"],
                        zone_id=zid,
                        handover_id=handover_id,
                    )
                raise HTTPException(409, reason)

            now = _now()
            # 同事务逐区原子切换：一条原地条件 UPDATE 同时完成
            # “交班方卸任 + 接班方就任”，没有先删后插的空窗；
            # 持权者已被并发改写时 WHERE 不命中 => rowcount=0，整单回滚。
            for zid in zones:
                cur = conn.execute(
                    """
                    UPDATE duty_holders
                       SET shift_id = ?, since = ?, handover_id = ?
                     WHERE zone_id = ? AND shift_id = ?
                    """,
                    (h["to_shift_id"], now, handover_id, zid, h["from_shift_id"]),
                )
                if cur.rowcount != 1:
                    raise _HandoverConflict(
                        f"责任区#{zid}值班权已被改写，条件切换未生效"
                    )
                # 持久化值班切换证明：区、旧值班、新值班、切换时刻、交班单 id
                conn.execute(
                    """
                    INSERT INTO duty_transitions(
                      zone_id, from_shift_id, to_shift_id, handover_id, switched_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (zid, h["from_shift_id"], h["to_shift_id"], handover_id, now),
                )

            # 清单批次整批过户（仅在途），与值班权切换同一事务
            transferred = 0
            for it in items:
                cur = conn.execute(
                    """
                    UPDATE batches SET owner_shift_id = ?
                    WHERE id = ? AND status = '在途' AND owner_shift_id = ?
                    """,
                    (h["to_shift_id"], it["batch_id"], h["from_shift_id"]),
                )
                transferred += cur.rowcount

            # 一致性只认清单本身：清单内每个批次都必须过户到接班方。
            # 交班方在【其他责任区】仍可能持有在途批次，不参与本单判定。
            not_transferred = [
                it["batch_id"]
                for it in items
                if conn.execute(
                    "SELECT 1 FROM batches WHERE id = ? AND owner_shift_id = ?",
                    (it["batch_id"], h["to_shift_id"]),
                ).fetchone()
                is None
            ]
            after_from = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["from_shift_id"],),
            ).fetchone()["c"]
            after_to = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["to_shift_id"],),
            ).fetchone()["c"]
            consistent = 1 if transferred == before and not not_transferred else 0
            detail = {
                "交班前在途数": before,
                "实际过户数": transferred,
                "未过户清单批次": not_transferred,
                "交班后交班方在途(全量)": after_from,
                "交班后接班方在途(全量)": after_to,
                "切换责任区": zones,
            }

            conn.execute(
                "UPDATE handovers SET status = ?, finished_at = ?, batch_count = ?, reason = '' WHERE id = ?",
                ("已完成" if consistent else "失败", now, before, handover_id),
            )
            if not consistent:
                conn.execute(
                    "UPDATE handovers SET reason = ? WHERE id = ?",
                    ("交班后批次归属与清单不一致，事务回滚", handover_id),
                )
                raise _HandoverConflict("批次过户数量与清单不一致")

            # 班次状态由值班权重新派生
            duty.refresh_shift_status(conn, h["from_shift_id"], h["to_shift_id"])
            conn.execute(
                """
                INSERT INTO reconcile_reports(
                  handover_id, before_count, after_from_count, after_to_count,
                  consistent, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handover_id, before, after_from, after_to,
                    consistent, json.dumps(detail, ensure_ascii=False), now,
                ),
            )
            conn.commit()
    except (
        _HandoverConflict,
        sqlite3.IntegrityError,
    ) as exc:
        # with 块退出即回滚；留痕后报错，绝不允许标注交班成功。
        # IntegrityError：触发器拦截无资格就任（如并发移除接班方资格）。
        duty.record_denial(
            "完成交班",
            f"交班单#{handover_id}原子切换失败并已回滚：{exc}",
            handover_id=handover_id,
            detail={"conflict": str(exc)},
        )
        raise HTTPException(409, f"交班失败并已回滚：{exc}")
    return get_handover(handover_id)


def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconcile_reports ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out
