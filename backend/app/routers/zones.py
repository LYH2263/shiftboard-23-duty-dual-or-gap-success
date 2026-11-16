from fastapi import APIRouter, Query

from app.services import duty

router = APIRouter(tags=["责任区与留痕"])


@router.get("/zones")
def get_zones():
    """责任区清单：当前值班班次、可值班班次集合，同源可查。"""
    return duty.list_zones()


@router.get("/denials")
def get_denials(
    operation: str | None = Query(default=None, description="操作类型过滤"),
    zone_id: int | None = Query(default=None),
    actor_shift_id: int | None = Query(default=None),
):
    """责任区/值班态/交班单/在途操作的失败原因，统一留痕查询。"""
    return duty.list_denials(operation, zone_id, actor_shift_id)
