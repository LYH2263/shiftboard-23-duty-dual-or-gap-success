from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services import batches as batch_svc
from app.services import handover as svc

router = APIRouter(prefix="/batches", tags=["在途批次"])


class SettleBody(BaseModel):
    actor_shift_id: int = Field(..., description="执行操作的班次")


class ReassignBody(BaseModel):
    actor_shift_id: int = Field(..., description="执行操作的班次")
    new_owner_shift_id: int = Field(..., description="新归属班次")


@router.get("")
def get_batches(
    owner_shift_id: int | None = Query(default=None),
    zone_id: int | None = Query(default=None),
):
    return svc.list_batches(owner_shift_id, zone_id)


@router.post("/{batch_id}/settle")
def settle(batch_id: int, body: SettleBody):
    return batch_svc.settle_batch(batch_id, body.actor_shift_id)


@router.post("/{batch_id}/reassign")
def reassign(batch_id: int, body: ReassignBody):
    return batch_svc.reassign_batch(
        batch_id, body.actor_shift_id, body.new_owner_shift_id
    )
