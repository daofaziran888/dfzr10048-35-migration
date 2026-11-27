"""字幕包历史签收迁移与复验接口。

- 列表/详情：旧签收、迁移状态、清单、原件下载、待确认复验与审计轨迹
- 迁移动作：启动 / 确认 / 回滚 / 重新启用
- 更正写入：只落同位置工作区，不覆盖签收原件
- 原件下载：只返回字节与签收指纹一致的 V1 原件
- 复验：接收方提交原文件与指纹，可在迁移确认前到达
- 共用结论：字幕包与交付回执读取同一份服务端核验结果
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.services.signoff import signoff_registry

router = APIRouter(prefix="/api/signoff", tags=["历史签收迁移"])


class ActorPayload(BaseModel):
    actor: str = Field(default="数据工程组")


class CorrectionPayload(BaseModel):
    filename: str
    content: str
    actor: str = Field(default="更正执行人")


class SubmittedFile(BaseModel):
    filename: str
    content: str


class VerifyPayload(BaseModel):
    receiver: str
    files: list[SubmittedFile] = Field(default_factory=list)
    actor: str = Field(default="接收方")


def _require(pack_no: str):
    detail = signoff_registry.detail(pack_no)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"原包号 {pack_no} 没有历史签收记录")
    return detail


@router.get("/packs")
def list_signoff_packs() -> dict[str, object]:
    items = signoff_registry.list_packs()
    return {"items": items, "total": len(items)}


@router.get("/packs/{pack_no}")
def get_signoff_pack(pack_no: str) -> dict[str, object]:
    return _require(pack_no)


@router.get("/packs/{pack_no}/legacy")
def read_legacy(pack_no: str) -> dict[str, object]:
    """旧签收读取依赖：部署切换后仍保留，回滚后仍然可读。"""
    legacy = signoff_registry.read_legacy_signoff(pack_no)
    if legacy is None:
        raise HTTPException(status_code=404, detail=f"原包号 {pack_no} 没有历史签收记录")
    return legacy


@router.post("/packs/{pack_no}/migration/start")
def start_migration(pack_no: str, payload: ActorPayload) -> dict[str, object]:
    _require(pack_no)
    ok, message = signoff_registry.start_migration(pack_no, payload.actor)
    return {"ok": ok, "message": message, "detail": signoff_registry.detail(pack_no)}


@router.post("/packs/{pack_no}/migration/confirm")
def confirm_migration(pack_no: str, payload: ActorPayload) -> dict[str, object]:
    _require(pack_no)
    result = signoff_registry.confirm_migration(pack_no, payload.actor)
    result["detail"] = signoff_registry.detail(pack_no)
    return result


@router.post("/packs/{pack_no}/migration/rollback")
def rollback_migration(pack_no: str, payload: ActorPayload) -> dict[str, object]:
    _require(pack_no)
    ok, message = signoff_registry.rollback_migration(pack_no, payload.actor)
    return {"ok": ok, "message": message, "detail": signoff_registry.detail(pack_no)}


@router.post("/packs/{pack_no}/migration/reactivate")
def reactivate_migration(pack_no: str, payload: ActorPayload) -> dict[str, object]:
    _require(pack_no)
    ok, message = signoff_registry.reactivate_migration(pack_no, payload.actor)
    return {"ok": ok, "message": message, "detail": signoff_registry.detail(pack_no)}


@router.post("/packs/{pack_no}/corrections")
def write_correction(pack_no: str, payload: CorrectionPayload) -> dict[str, object]:
    _require(pack_no)
    ok, message = signoff_registry.write_correction(
        pack_no, payload.filename, payload.content, payload.actor,
    )
    return {"ok": ok, "message": message, "detail": signoff_registry.detail(pack_no)}


@router.get("/packs/{pack_no}/originals/{filename}")
def download_original(pack_no: str, filename: str) -> PlainTextResponse:
    _require(pack_no)
    data, source, reason = signoff_registry.get_original(pack_no, filename)
    if data is None:
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "message": f"无法复验：{reason}", "missing_link": reason},
        )
    return PlainTextResponse(
        content=data.decode("utf-8", "replace"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Original-Source": quote(source or ""),
            "X-Original-Sha256": signoff_registry.expected_fingerprint(pack_no, filename) or "",
        },
    )


@router.post("/packs/{pack_no}/verify")
def verify_pack(pack_no: str, payload: VerifyPayload) -> dict[str, object]:
    _require(pack_no)
    files = {item.filename: item.content.encode("utf-8") for item in payload.files}
    return signoff_registry.verify_pack(
        pack_no, payload.receiver, files, actor=payload.actor,
    )


@router.get("/packs/{pack_no}/shared-result")
def shared_result(pack_no: str) -> dict[str, object]:
    _require(pack_no)
    result = signoff_registry.shared_result(pack_no)
    if result is None:
        return {"ok": False, "message": "尚无服务端核验结论", "shared": True}
    return result
