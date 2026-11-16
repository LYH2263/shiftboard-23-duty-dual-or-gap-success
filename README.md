# 07-single-duty-zone（交班台 · 单值班责任区）

交班台 — 责任区单值班约束 + 班次值班权原子交接 + 在途批次操作管控：
责任区登记 → 班次绑定可值班责任区 → 值班权唯一持有 → 跨区交班逐区校验、同事务原子切换 → 操作失败同源留痕。

**不是** 多源 Feed 归并，**不是** 作业依赖编排，**不是** CDC 变更流，**不是** 质检签发，**不是** 数仓贴源/明细/汇总分层。
**也不是** OA 考勤排班后台、电商订单、报表 CSV 看板。

## 启动

本地开发：

```bash
# 后端
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=$PWD/data FIXTURES_DIR=$PWD/../fixtures \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 10600

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5600 |
| 接口 | http://127.0.0.1:10600 |

## 领域模型（本地 SQLite）

| 表 | 职责 |
| --- | --- |
| `zones` | 责任区 |
| `shift_zones` | 班次 ↔ 可值班责任区集合（多对多） |
| `duty_holders` | **独立值班权表**：责任区 → 当前值班班次。`zone_id` 主键保证同一责任区至多一个值班班次；`(zone_id, shift_id)` 经 `shift_zones` 外键保证只有具备资格的班次能就任 |
| `batches` | 在途批次，`zone_id` 归属某责任区、`owner_shift_id` 归属当前持权班次 |
| `handovers` / `handover_zones` / `handover_items` | 交班单头、覆盖责任区、批次清单 |
| `operation_denials` | 责任区资格/单值班/交班/在途操作的失败原因，与业务数据同库同源 |

## 不变量与落点

1. **同一责任区同一时刻至多一个值班班次**：`duty_holders.zone_id` 主键（数据库级），应用层无法插入第二个持权行。
2. **交班原子生效，无空窗、不假成功**：完成交班在**同一事务**内对清单内每个责任区执行条件
   `UPDATE duty_holders SET shift_id=接班方 WHERE zone_id=? AND shift_id=交班方`，
   一条原地 UPDATE 同时完成「交班方卸任 + 接班方就任」；`rowcount≠1` 或批次过户数与清单不一致则整事务回滚，交班单绝不标成功。
3. **跨区整单校验**：发起交班时对清单内**每个**责任区逐一校验双方资格（交班方当前持权、接班方已绑定该区）与单值班约束，任一不过整单拒绝，禁止只查主责任区。
4. **非值班班次禁止在途操作**：结清 / 改归属 / 发起交班先过值班权校验，冲突信息写明当前值班班次（名称、编号、值班人），拒绝原因落 `operation_denials`。改归属只能交给该责任区当前值班班次，防止绕过值班权转移批次。

## 主要接口

| 方法/路径 | 说明 |
| --- | --- |
| `GET /api/zones` | 责任区、当前值班班次、可值班班次集合 |
| `GET /api/shifts` | 班次及其绑定/持有的责任区 |
| `GET /api/batches?zone_id=&owner_shift_id=` | 在途批次（含责任区与区当前值班班次） |
| `POST /api/batches/{id}/settle` | 结清（body: `actor_shift_id`） |
| `POST /api/batches/{id}/reassign` | 改归属（body: `actor_shift_id`, `new_owner_shift_id`） |
| `POST /api/handovers/start` | 发起交班（可选 `zone_ids` 分区交接；不传=交班方全部持权责任区） |
| `POST /api/handovers/{id}/complete` | 同事务原子切换值班权 + 清单整批过户 |
| `GET /api/denials?operation=&zone_id=&actor_shift_id=` | 失败原因同源查询 |
| `GET /api/reconcile` | 交班对账（一致性只认清单内批次全部过户） |

## 主链

登记责任区与班次资格 → 值班权唯一持有 → 发起交班逐区校验（跨区不过整单拒绝）→ 完成交班同事务切换值班权并整批过户 → 对账与操作留痕同源可查。

## 界面约定

页面文案全部中文；入口：总览 / 班次 / 责任区 / 在途批次 / 交班办理 / 交班记录 / 交班对账 / 操作留痕。

## 测试

```bash
cd backend
.venv/bin/python -m pytest tests/ -v
```

- `tests/test_handover_smoke.py`：端到端业务规则（13 项）
- `tests/test_duty_guarantees.py`：唯一约束、资格外键、原子回滚无空窗、并发冲突回滚、老库迁移（5 项）

## 与已有仓差异

| 仓 | 骨架 |
| --- | --- |
| Feedstitch | 多源解析→冲突隔离→水位 |
| Batchgate | 作业依赖→重试→水位 |
| Changeflow | 事件序→缺口→Sink 游标 |
| Checklane | 入检→规则→隔离签发 |
| Tiercraft | 贴源→明细→汇总→层间对账 |
| **本仓** | **责任区单值班→值班权原子交接→在途操作管控→失败同源留痕** |
