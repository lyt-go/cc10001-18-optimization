"""模板库接口：维护采集模板，覆盖提交模板验证、发布模板、废弃模板与版本回滚。

静态路径（``/export``）必须注册在动态路径（``/{entry_id}``）之前，否则导出请求会被
当成 entry_id 解析并返回 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.templatelib import TemplatelibService

router = APIRouter(prefix="/api/templatelib", tags=["模板库"])

service = TemplatelibService()

LIST_FIELDS = ["模板编号", "站点分类", "模板名称", "解析规则集", "请求模板", "分页配置", "适用域名", "生效版本"]
STATUSES = ["草稿", "模板验证中", "已发布", "已废弃"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出模板库清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "templatelib", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、模板验证中、已发布、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤模板库列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条采集模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="采集模板已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条采集模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集模板 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/versions", response_model=dict)
def get_versions(entry_id: int) -> dict:
    """模板详情页的版本视图：当前校验指纹、结论是否可复用、旧结论失效原因与版本历史。"""
    detail = service.version_detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"采集模板 {entry_id} 不存在或已归档")
    return detail


@router.get("/{entry_id}/impact", response_model=dict)
def get_impact(entry_id: int) -> dict:
    """模板详情页的受影响视图：引用该模板的采集任务、排期计划及不可启动的原因。"""
    impact = service.impact_detail(entry_id)
    if impact is None:
        raise HTTPException(status_code=404, detail=f"采集模板 {entry_id} 不存在或已归档")
    return impact


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改模板配置：模板编号、适用域名、请求模板、字符编码必须同进同退，变更即产生新版本。"""
    entry, message, errors = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message="；".join(errors) if errors else message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条采集模板执行提交模板验证、发布模板、废弃模板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/rollback", response_model=ActionResult)
def rollback_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """回滚到旧生效版本：整版读取该版本字段，不把新旧模板字段拼接在一起。"""
    version_id = str(payload.values.get("version_id") or "").strip()
    if not version_id:
        return ActionResult(ok=False, message="回滚需要提供 version_id")
    entry, message = service.rollback_entry(entry_id, version_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
