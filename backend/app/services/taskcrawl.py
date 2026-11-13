"""采集任务业务规则：状态流转、模板版本判断与筛选口径都收在这里。

任务只关心模板当前版本的校验指纹：启动任务时按当前版本重新判断，已发布才放行；
历史运行配置记录在 ``VersionRegistry`` 里，模板版本变化不会回写已完成任务的历史配置。
"""
from __future__ import annotations

from typing import Any
from app.services.templatelib import _bound, find_by_code
from app.services.versioning import _iso, now, version_service
from app.store import store

MODULE = "taskcrawl"
REQUIRED_FIELDS = ["任务编号", "采集源名称", "规则编号"]
STATUS_ORDER = ["草稿", "待调度", "执行中", "已完成", "已终止"]
ACTION_RULES = {"提交调度": "待调度", "启动任务": "执行中", "终止任务": "已终止"}
NEGATIVE_ACTIONS = []
# 历史配置已经固化、不再参与新版本判定的任务状态。
HISTORICAL_STATUSES = {"已完成", "已终止"}


def bound_template(task_row: dict[str, Any]) -> dict[str, Any] | None:
    """找到任务引用的模板：显式 template_code 优先，否则按规则集交集回退。"""
    code = str(task_row.get("template_code") or "").strip()
    if code:
        return find_by_code(code)
    templates = store.rows("templatelib")
    for row in templates:
        if _bound(task_row, row):
            return row
    return None


def _timestamp() -> str:
    return _iso(now())


class TaskcrawlService:
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
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
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

    def schedule_view(self) -> dict[str, Any]:
        """任务排期视图：每个任务按模板当前版本给出可启动判断与受影响原因。"""
        tasks: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            template = bound_template(row)
            if template is None:
                tasks.append({
                    "任务编号": str(row.get("任务编号") or ""),
                    "任务状态": str(row.get("status") or ""),
                    "模板编号": row.get("template_code"),
                    "可启动": False,
                    "受影响原因": "任务未绑定采集模板",
                })
                continue
            verdict = version_service.judge_task(row, template)
            historical = version_service.registry.get_task_run_config(int(row.get("id") or 0))
            verdict["任务状态"] = row.get("status")
            verdict["历史运行配置"] = historical
            tasks.append(verdict)
        return {
            "任务": tasks,
            "受影响任务数": sum(1 for item in tasks if not item["可启动"]),
        }

    def task_impact(self, entry_id: int) -> dict[str, Any] | None:
        """任务详情：当前版本判断 + 绑定模板的完整配置 + 自身历史运行配置。"""
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        template = bound_template(row)
        if template is None:
            return {
                "任务编号": row.get("任务编号"),
                "任务状态": row.get("status"),
                "可启动": False,
                "受影响原因": "任务未绑定采集模板",
                "历史运行配置": version_service.registry.get_task_run_config(entry_id),
            }
        verdict = version_service.judge_task(row, template)
        verdict["任务状态"] = row.get("status")
        verdict["历史运行配置"] = version_service.registry.get_task_run_config(entry_id)
        return verdict

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集任务可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "启动任务":
            return self._start(entry, target)
        if action == "终止任务":
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, "采集任务已终止"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"采集任务已{action}"

    def _start(self, entry: dict[str, Any], target: str) -> tuple[dict[str, Any] | None, str]:
        """启动任务只按模板当前版本判断；历史配置只读取、不改写。"""
        if str(entry.get("status") or "") in HISTORICAL_STATUSES:
            return None, f"任务已处于「{entry.get('status')}」，历史运行配置不再变更，不能重新启动"
        template = bound_template(entry)
        if template is None:
            return None, "任务未绑定采集模板，无法判断当前版本能否启动"
        verdict = version_service.judge_task(entry, template)
        if not verdict["可启动"]:
            existing = version_service.registry.get_task_run_config(int(entry.get("id") or 0))
            if existing is not None and not verdict["结论可复用"]:
                # 已启动过的任务在模板变更后会被拦下，但保留原运行配置，不回写。
                return None, (
                    f"模板版本 {verdict['版本标识']} 与任务历史运行版本不一致，"
                    f"需先按新版本重新排期：{verdict['受影响原因']}"
                )
            return None, f"任务当前不可启动：{verdict['受影响原因']}"

        snapshot = verdict["完整配置"]["配置"]
        entry["status"] = target
        entry["pending"] = False
        entry["abnormal"] = False
        # 首次启动才固化运行配置；后续模板版本变化只影响下一次判断，不改写这段历史。
        if version_service.registry.get_task_run_config(int(entry.get("id") or 0)) is None:
            version_service.registry.put_task_run_config(
                int(entry.get("id") or 0),
                {
                    "任务编号": entry.get("任务编号"),
                    "模板编号": verdict["模板编号"],
                    "版本标识": verdict["版本标识"],
                    "校验指纹": verdict["校验指纹"],
                    "字段映射": list(snapshot.get("字段映射") or []),
                    "分页配置": snapshot.get("分页配置"),
                    "请求模板": snapshot.get("请求模板"),
                    "字符编码": snapshot.get("字符编码"),
                    "适用域名": snapshot.get("适用域名"),
                    "固化时间": _timestamp(),
                },
            )
        return entry, (
            f"采集任务已启动，读取模板版本 {verdict['版本标识']} 的整套配置"
            f"（{len(snapshot.get('字段映射') or [])} 个输出字段）"
        )


__all__ = ["MODULE", "TaskcrawlService", "bound_template"]
