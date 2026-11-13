"""调度计划业务规则：状态流转、模板版本判断与筛选口径都收在这里。

调度计划是任务排期视图的载体：计划绑定的模板版本一旦变化，计划会显示受影响原因，
但已固化到任务上的历史运行配置不会被改写。
"""
from __future__ import annotations

from typing import Any

from app.services.taskcrawl import bound_template
from app.services.versioning import version_service
from app.store import store

MODULE = "scheduler"
REQUIRED_FIELDS = ["计划编号", "关联任务", "cron表达式"]
STATUS_ORDER = ["草稿", "计划审批中", "已排期", "已暂停"]
ACTION_RULES = {"提交审批": "计划审批中", "确认排期": "已排期", "暂停计划": "已暂停"}
NEGATIVE_ACTIONS = []
BLOCKED_STATUSES = {"草稿", "计划审批中"}


def _task_by_code(code: str) -> dict[str, Any] | None:
    for row in store.rows("taskcrawl"):
        if str(row.get("任务编号") or "").strip() == str(code).strip():
            return row
    return None


class SchedulerService:
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
            rows = [row for row in rows if keyword in str(row.get("计划编号", ""))]
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
        if values.get("template_code"):
            entry["template_code"] = str(values["template_code"]).strip()
        rows.append(entry)
        return entry, []

    def plan_view(self, entry_id: int) -> dict[str, Any] | None:
        """单个计划的排期判断：任务可启动 + 模板当前版本，原因与模板详情页统一口径。"""
        plan = store.find(MODULE, entry_id)
        if plan is None:
            return None
        task = _task_by_code(str(plan.get("关联任务") or ""))
        if task is None:
            return {
                "计划编号": plan.get("计划编号"),
                "计划状态": plan.get("status"),
                "模板编号": plan.get("template_code"),
                "可排期": False,
                "受影响原因": "关联任务未登记，无法判断模板版本",
            }
        template = bound_template(task)
        if template is None:
            return {
                "计划编号": plan.get("计划编号"),
                "任务编号": task.get("任务编号"),
                "计划状态": plan.get("status"),
                "模板编号": plan.get("template_code"),
                "可排期": False,
                "受影响原因": "关联任务未绑定采集模板",
            }
        verdict = version_service.judge_task(task, template)
        allowed = verdict["可启动"] and str(plan.get("status") or "") not in BLOCKED_STATUSES
        reason = verdict["受影响原因"]
        if not allowed and verdict["可启动"]:
            reason = f"调度计划状态为「{plan.get('status')}」，需先提交审批并确认排期"
        return {
            "计划编号": plan.get("计划编号"),
            "任务编号": task.get("任务编号"),
            "任务状态": task.get("status"),
            "计划状态": plan.get("status"),
            "模板编号": verdict["模板编号"],
            "模板状态": verdict["模板状态"],
            "版本标识": verdict["版本标识"],
            "校验指纹": verdict["校验指纹"],
            "可排期": allowed,
            "受影响原因": reason,
            "完整配置": verdict["完整配置"],
            "历史运行配置": verdict["历史运行配置"],
        }

    def schedule_view(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            view = self.plan_view(int(row.get("id") or 0))
            if view is not None:
                entries.append(view)
        return {
            "计划": entries,
            "受影响计划数": sum(1 for item in entries if not item["可排期"]),
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"调度计划 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于调度计划可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "确认排期":
            view = self.plan_view(entry_id)
            if view is None:
                return None, f"调度计划 {entry_id} 不存在或已归档"
            if not view["可排期"]:
                return None, f"计划暂不能排期：{view['受影响原因']}"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"调度计划已{action}"


__all__ = ["MODULE", "SchedulerService"]
