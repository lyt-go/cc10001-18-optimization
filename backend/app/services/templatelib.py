"""模板库业务规则：状态流转、版本判定与筛选口径都收在这里。

版本判定、校验结论缓存与失效传播全部委托给 ``app/services/versioning.py``，
模板验证、任务排期和回滚动作共用同一套指纹写法。
"""
from __future__ import annotations

from typing import Any

from app.fingerprint import TEMPLATE_CONFIG_FIELDS, normalize_ruleset, normalize_text
from app.services.versioning import (
    DEPRECATED_STATUS,
    RESULT_INVALID,
    RESULT_PENDING,
    RESULT_VALID,
    action_intent,
    version_service,
)
from app.store import store

MODULE = "templatelib"
REQUIRED_FIELDS = ["模板编号", "站点分类", "模板名称"]
STATUS_ORDER = ["草稿", "模板验证中", "已发布", "已废弃"]
ACTION_RULES = {"提交模板验证": "模板验证中", "发布模板": "已发布", "废弃模板": "已废弃"}
NEGATIVE_ACTIONS = []


def _template_code(row: dict[str, Any]) -> str:
    return str(row.get("模板编号") or "").strip()


def find_by_code(code: str) -> dict[str, Any] | None:
    for row in store.rows(MODULE):
        if _template_code(row) == str(code).strip():
            return row
    return None


def _task_template_code(task_row: dict[str, Any]) -> str:
    return str(task_row.get("template_code") or task_row.get("模板编号") or "").strip()


def _bound(task_row: dict[str, Any], entry: dict[str, Any]) -> bool:
    code = _task_template_code(task_row)
    if code:
        return code == _template_code(entry)
    # 没有显式绑定关系时按解析规则集交集回退，兼容早期只登记规则编号的任务。
    task_rules = set(normalize_ruleset(task_row.get("规则编号")))
    return bool(task_rules & set(normalize_ruleset(entry.get("解析规则集"))))


def _bound_schedule(schedule_row: dict[str, Any], entry: dict[str, Any]) -> bool:
    code = str(schedule_row.get("template_code") or "").strip()
    if code:
        return code == _template_code(entry)
    task_code = str(schedule_row.get("关联任务") or "").strip()
    for task_row in store.rows("taskcrawl"):
        if str(task_row.get("任务编号") or "").strip() == task_code:
            return _bound(task_row, entry)
    return False


class TemplatelibService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        version_service.record_version(entry)
        return entry, []

    def update_entry(
        self, entry_id: int, changes: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, list[str]]:
        """修改模板配置；原子字段组整组提交，配置变化立即产生新版本并让旧结论失效。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, "", [f"采集模板 {entry_id} 不存在或已归档"]
        candidate, errors = version_service.prepare_update(entry, changes)
        if candidate is None:
            return None, "", errors
        previous = version_service.snapshot(entry)
        entry.update({
            field: candidate.get(field)
            for field in changes
            if field in TEMPLATE_CONFIG_FIELDS
        })
        updated = version_service.snapshot(entry)
        version_service.record_version(entry)
        if updated.combined_fingerprint == previous.combined_fingerprint:
            return entry, "模板配置未变化，沿用原校验结论", []
        # 生效版本保留为上一次真正发布过的版本，便于对照“已发布版本 vs 当前配置”；
        # 任务是否可启动只看当前指纹，当前版本没有新结论就会在排期视图里被拦下。
        stale = version_service.stale(updated)
        note = (
            f"模板配置已变更，生成新版本 {updated.version_id}；旧版本 {previous.version_id} 的校验结论已失效"
            if stale is not None
            else f"模板配置已变更，生成新版本 {updated.version_id}"
        )
        return entry, note, []

    def version_detail(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        snapshot = version_service.snapshot(entry)
        cached = version_service.stale(snapshot)
        stored = version_service.registry.get_result(entry_id)
        effective_version = str(entry.get("生效版本") or "")
        return {
            "模板编号": snapshot.code,
            "模板状态": snapshot.status,
            "版本标识": snapshot.version_id,
            "生效版本": effective_version or None,
            "配置与生效版本一致": bool(effective_version) and effective_version == snapshot.version_id,
            "校验指纹": snapshot.combined_fingerprint,
            "模板指纹": snapshot.template_fingerprint,
            "规则集指纹": snapshot.rules_fingerprint,
            "完整配置": snapshot.as_dict(),
            "校验结论": cached.as_dict() if cached else None,
            "结论可复用": bool(cached and cached.reusable),
            "结论来源": cached.source if cached else None,
            "验证时间": stored.checked_at if stored else None,
            "有效期至": stored.expires_at if stored else None,
            "提交次数": stored.run_count if stored else 0,
            "版本历史": version_service.describe_versions(entry_id),
        }

    def rollback_entry(
        self, entry_id: int, version_id: str,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集模板 {entry_id} 不存在或已归档"
        snapshot, message = version_service.rollback(entry, version_id)
        if snapshot is None:
            return None, message
        entry["生效版本"] = snapshot.version_id
        return entry, message

    def impact_detail(self, entry_id: int) -> dict[str, Any] | None:
        """模板详情页的受影响任务：显示每项任务在当前版本下的可启动判断与原因。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        snapshot = version_service.snapshot(entry)
        task_rows = [row for row in store.rows("taskcrawl") if _bound(row, entry)]
        schedule_rows = [
            row for row in store.rows("scheduler")
            if _bound_schedule(row, entry)
        ]
        tasks = [version_service.judge_task(row, entry) for row in task_rows]
        return {
            "模板编号": snapshot.code,
            "模板名称": snapshot.config.get("模板名称"),
            "模板状态": snapshot.status,
            "版本标识": snapshot.version_id,
            "校验指纹": snapshot.combined_fingerprint,
            "校验结论": version_service.stale(snapshot).result if version_service.stale(snapshot) else None,
            "任务": tasks,
            "排期计划": [
                {
                    "计划编号": str(row.get("计划编号") or ""),
                    "关联任务": str(row.get("关联任务") or ""),
                    "状态": str(row.get("status") or ""),
                }
                for row in schedule_rows
            ],
            "受影响任务数": sum(1 for item in tasks if not item["可启动"]),
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于模板库可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        intent = action_intent(action)
        if intent == "validate":
            conclusion, snapshot = version_service.run_validation(entry)
            entry["status"] = target
            entry["pending"] = True
            entry["abnormal"] = conclusion.result == RESULT_INVALID
            if conclusion.result == RESULT_VALID:
                return entry, f"模板验证通过（版本 {snapshot.version_id}）：{conclusion.reason}"
            if conclusion.result == RESULT_PENDING:
                return entry, f"模板验证未完成：{conclusion.reason}"
            return entry, f"模板验证未通过（版本 {snapshot.version_id}）：{conclusion.reason}"

        if intent == "publish":
            conclusion, snapshot = version_service.run_validation(entry)
            if conclusion.result != RESULT_VALID:
                entry["abnormal"] = True
                return None, (
                    f"模板当前版本 {snapshot.version_id} 校验未通过，不能发布：{conclusion.reason}"
                )
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            entry["生效版本"] = snapshot.version_id
            version_service.registry.mark_effective(entry_id, snapshot.version_id)
            return entry, f"采集模板已发布，生效版本 {snapshot.version_id}"

        if intent == "deprecate":
            snapshot = version_service.snapshot(entry)
            entry["status"] = DEPRECATED_STATUS
            entry["pending"] = False
            entry["abnormal"] = True
            if entry.get("生效版本"):
                version_service.registry.mark_effective(entry_id, str(entry["生效版本"]))
            return entry, f"采集模板已废弃，版本 {snapshot.version_id} 不再允许任务启动"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"采集模板已{action}"


__all__ = [
    "MODULE",
    "TemplatelibService",
    "find_by_code",
]
