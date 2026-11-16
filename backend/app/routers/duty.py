from fastapi import APIRouter, Query

from app.services import duty

router = APIRouter(prefix="/duty-transitions", tags=["值班切换证明"])


@router.get("")
def get_transitions(
    zone_id: int | None = Query(default=None, description="按责任区过滤"),
    handover_id: int | None = Query(default=None, description="按交班单 id 过滤"),
):
    """值班切换证明只读接口：区、旧值班、新值班、切换时刻、交班单 id。"""
    return duty.list_transitions(zone_id=zone_id, handover_id=handover_id)
