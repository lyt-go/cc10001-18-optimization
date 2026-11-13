"""模板版本判定与校验缓存：模板验证、任务排期、回滚动作共用的一份实现。

原来模板缓存与规则缓存各自判断版本，模板改动后旧结论仍然有效，引用它的任务也
看不到分页配置/字段映射的变化。这里把判定收成统一写法：

* ``resolve``       —— 解析模板当前版本的完整配置（含其引用的那组解析规则）；
* ``run_validation``—— 提交模板验证，同一指纹在有效期内只保留一次结果；
* ``judge_task``    —— 任务只关心当前版本指纹，按当前版本重新判断能否启动；
* ``rollback``      —— 回滚到旧生效版本时整版切换，不拼接新旧字段。

任何一个动作都先经过同一套指纹；指纹不变即可复用结论，指纹变化旧结论立即失效。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.config import settings
from app.fingerprint import (
    ATOMIC_TEMPLATE_FIELDS,
    TEMPLATE_CONFIG_FIELDS,
    atomic_group_errors,
    combine,
    normalize_field_mapping,
    normalize_ruleset,
    normalize_text,
    rule_fingerprint,
    ruleset_fingerprint,
    split_codes,
    template_config,
    template_fingerprint,
)

MODULE = "templatelib"
RULE_MODULE = "parserule"
INACTIVE_RULE_STATUSES = {"已废弃"}
PUBLISHED_STATUS = "已发布"
DEPRECATED_STATUS = "已废弃"
VALIDATING_STATUS = "模板验证中"

RESULT_VALID = "通过"
RESULT_INVALID = "不通过"
RESULT_PENDING = "验证未完成"

# 校验结论的来由：可启动判断会把它带到任务排期页面上。
SOURCE_FRESH = "fresh"
SOURCE_CACHE = "cache"
SOURCE_STALE = "stale"
SOURCE_DEPRECATED = "deprecated"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _code(row: Mapping[str, Any], field: str) -> str:
    """取业务编号；兼容“规则编号”存在“编号”列里的登记数据。"""
    value = row.get(field)
    return str(value).strip() if value not in (None, "") else ""


@dataclass(frozen=True)
class RuleSnapshot:
    row_id: int | None
    code: str
    status: str
    fingerprint: str
    source: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True)
class TemplateSnapshot:
    entry_id: int
    code: str
    status: str
    template_fingerprint: str
    rules_fingerprint: str
    combined_fingerprint: str
    version_id: str
    config: dict[str, Any] = field(repr=False, compare=False)
    rules: tuple[RuleSnapshot, ...] = field(repr=False, compare=False)
    missing_rules: tuple[str, ...] = ()
    inactive_rules: tuple[str, ...] = ()
    version_exists: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "模板指纹": self.template_fingerprint,
            "规则集指纹": self.rules_fingerprint,
            "校验指纹": self.combined_fingerprint,
            "版本标识": self.version_id,
            "配置": self.config,
            "解析规则": [
                {
                    "规则编号": rule.code,
                    "状态": rule.status,
                    "规则指纹": rule.fingerprint,
                }
                for rule in self.rules
            ],
            "缺失规则": list(self.missing_rules),
            "已废弃规则": list(self.inactive_rules),
        }


@dataclass(frozen=True)
class StoredResult:
    result: str
    signature: str
    reason: str
    checked_at: str
    expires_at: str
    run_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "结果": self.result,
            "校验指纹": self.signature,
            "原因": self.reason,
            "验证时间": self.checked_at,
            "有效期至": self.expires_at,
            "提交次数": self.run_count,
        }


@dataclass(frozen=True)
class CachedConclusion:
    result: str
    signature: str
    reason: str
    checked_at: str
    source: str
    reusable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "结果": self.result,
            "校验指纹": self.signature,
            "原因": self.reason,
            "验证时间": self.checked_at,
            "来源": self.source,
            "可复用": self.reusable,
        }


class VersionRegistry:
    """内存版的模板版本与校验结论仓库；换成数据库时只需替换这一层。"""

    def __init__(self) -> None:
        self._results: dict[int, dict[str, StoredResult]] = {}
        self._latest_signature: dict[int, str] = {}
        self._snapshots: dict[int, list[TemplateSnapshot]] = {}
        self._historical_tasks: dict[int, dict[str, Any]] = {}
        self._effective: dict[int, list[str]] = {}

    # ---- 生效版本历史 -----------------------------------------------------
    def mark_effective(self, entry_id: int, version_id: str) -> None:
        history = self._effective.setdefault(entry_id, [])
        if version_id not in history:
            history.append(version_id)

    def was_effective(self, entry_id: int, version_id: str) -> bool:
        return version_id in self._effective.get(entry_id, [])

    def effective_versions(self, entry_id: int) -> list[str]:
        return list(self._effective.get(entry_id, []))

    # ---- 校验结论 ---------------------------------------------------------
    def get_result(self, entry_id: int, signature: str | None = None) -> StoredResult | None:
        """按指纹取结论；不传指纹时返回该模板最近一次提交的结论。"""
        by_signature = self._results.get(entry_id, {})
        if signature is not None:
            return by_signature.get(signature)
        latest = self._latest_signature.get(entry_id)
        return by_signature.get(latest) if latest else None

    def put_result(self, entry_id: int, result: StoredResult) -> None:
        self._results.setdefault(entry_id, {})[result.signature] = result
        self._latest_signature[entry_id] = result.signature

    # ---- 版本快照 ---------------------------------------------------------
    def get_snapshots(self, entry_id: int) -> list[TemplateSnapshot]:
        return self._snapshots.get(entry_id, [])

    def put_snapshot(self, entry_id: int, snapshot: TemplateSnapshot) -> None:
        items = self._snapshots.setdefault(entry_id, [])
        for index, item in enumerate(items):
            if item.version_id == snapshot.version_id:
                items[index] = snapshot
                break
        else:
            items.append(snapshot)

    def find_snapshot(self, entry_id: int, version_id: str) -> TemplateSnapshot | None:
        for item in self.get_snapshots(entry_id):
            if item.version_id == version_id:
                return item
        return None

    # ---- 任务运行配置 -----------------------------------------------------
    def get_task_run_config(self, task_id: int) -> dict[str, Any] | None:
        return self._historical_tasks.get(task_id)

    def put_task_run_config(self, task_id: int, config: dict[str, Any]) -> None:
        self._historical_tasks[task_id] = config


def _resolve_rules(
    rule_codes: Iterable[str],
    rule_rows: list[dict[str, Any]],
) -> tuple[list[RuleSnapshot], list[str], list[str]]:
    by_code: dict[str, dict[str, Any]] = {}
    for row in rule_rows:
        code = _code(row, "规则编号") or _code(row, "编号")
        if code:
            by_code[code] = row
    rules: list[RuleSnapshot] = []
    missing: list[str] = []
    inactive: list[str] = []
    for code in rule_codes:
        row = by_code.get(code)
        if row is None:
            missing.append(code)
            continue
        status = str(row.get("status") or "")
        if status in INACTIVE_RULE_STATUSES:
            inactive.append(code)
        rules.append(
            RuleSnapshot(
                row_id=row.get("id"),
                code=code,
                status=status,
                fingerprint=rule_fingerprint(row),
                source=dict(row),
            )
        )
    return rules, missing, inactive


def resolve(template_row: Mapping[str, Any], rule_rows: list[dict[str, Any]]) -> TemplateSnapshot:
    """判定模板当前版本的整套配置，模板指纹和规则集指纹一次算清。"""
    config = template_config(template_row)
    rules, missing, inactive = _resolve_rules(config["解析规则集"], rule_rows)
    rules_fp = ruleset_fingerprint(rule.source for rule in rules) if rules else ruleset_fingerprint([])
    if missing:
        # 缺失的规则也要参与指纹：补登记之后不会误命中旧结论。
        rules_fp = combine(rules_fp, str(sorted(missing)))
    template_fp = template_fingerprint(template_row)
    combined = combine(template_fp, rules_fp)
    return TemplateSnapshot(
        entry_id=int(template_row.get("id") or 0),
        code=_code(template_row, "模板编号"),
        status=str(template_row.get("status") or ""),
        template_fingerprint=template_fp,
        rules_fingerprint=rules_fp,
        combined_fingerprint=combined,
        version_id=combined[:10],
        config=config,
        rules=tuple(rules),
        missing_rules=tuple(missing),
        inactive_rules=tuple(inactive),
    )


def _reason_summary(snapshot: TemplateSnapshot) -> str:
    fields = normalize_field_mapping(snapshot.config.get("字段映射"))
    return (
        f"站点分类「{snapshot.config.get('站点分类') or '未登记'}」，"
        f"分页配置「{snapshot.config.get('分页配置') or '未登记'}」，"
        f"解析规则 {len(snapshot.rules)} 条，输出字段 {len(fields)} 个"
    )


def template_for_task(
    task_row: Mapping[str, Any], template_rows: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """任务与模板的绑定：优先 templates_code，缺失时按解析规则集交集回退。"""
    code = normalize_text(task_row.get("template_code")) or normalize_text(task_row.get("模板编号"))
    templates = list(template_rows)
    if code:
        for row in templates:
            if normalize_text(row.get("模板编号")) == code:
                return row
        return None
    task_rules = set(split_codes(task_row.get("规则编号")))
    if not task_rules:
        return None
    for row in templates:
        if task_rules & set(normalize_ruleset(row.get("解析规则集"))):
            return row
    return None


_VALIDATE_KEYWORDS = ("验证",)
_PUBLISH_KEYWORDS = ("发布", "启用")
_DEPRECATE_KEYWORDS = ("废弃", "停用")


def action_intent(action: str) -> str:
    """把各模块的动作名称归一到“验证 / 发布 / 废弃”三种意图。"""
    if any(keyword in action for keyword in _DEPRECATE_KEYWORDS):
        return "deprecate"
    if any(keyword in action for keyword in _PUBLISH_KEYWORDS):
        return "publish"
    if any(keyword in action for keyword in _VALIDATE_KEYWORDS):
        return "validate"
    return "other"


class TemplateVersionService:
    """模板版本判定 + 校验缓存的唯一入口，供模板验证、任务排期与回滚共用。"""

    def __init__(self, registry: VersionRegistry | None = None, ttl_seconds: int | None = None) -> None:
        self.registry = registry or VersionRegistry()
        self.ttl_seconds = settings.validation_ttl_seconds if ttl_seconds is None else ttl_seconds

    # ---- 读取 -------------------------------------------------------------
    def _rule_rows(self) -> list[dict[str, Any]]:
        from app.store import store

        return store.rows(RULE_MODULE)

    def snapshot(self, template_row: Mapping[str, Any]) -> TemplateSnapshot:
        return resolve(template_row, self._rule_rows())

    def latest_snapshot(
        self, entry_id: int, template_row: Mapping[str, Any] | None = None,
    ) -> TemplateSnapshot | None:
        known = self.registry.get_snapshots(entry_id)
        if template_row is None:
            return known[-1] if known else None
        return self.snapshot(template_row)

    def cached(self, snapshot: TemplateSnapshot, at: datetime | None = None) -> CachedConclusion | None:
        """按当前指纹取校验结论；指纹不同或已过期的一律视为没有结论。"""
        stored = self.registry.get_result(snapshot.entry_id, snapshot.combined_fingerprint)
        if stored is None:
            return None
        moment = at or now()
        expired = _parse_iso(stored.expires_at)
        reusable = expired is not None and moment < expired
        return CachedConclusion(
            result=stored.result,
            signature=stored.signature,
            reason=stored.reason,
            checked_at=stored.checked_at,
            source=SOURCE_CACHE if reusable else SOURCE_STALE,
            reusable=reusable,
        )

    def stale(self, snapshot: TemplateSnapshot) -> CachedConclusion | None:
        """取旧结论用于跨页提示：取最近一次提交的结论，指纹不匹配时只作为“受影响原因”。"""
        stored = self.registry.get_result(snapshot.entry_id)
        if stored is None:
            return None
        reusable = stored.signature == snapshot.combined_fingerprint
        return CachedConclusion(
            result=stored.result,
            signature=stored.signature,
            reason=stored.reason,
            checked_at=stored.checked_at,
            source=SOURCE_CACHE if reusable else SOURCE_STALE,
            reusable=reusable,
        )

    # ---- 模板验证 ---------------------------------------------------------
    def run_validation(
        self, template_row: Mapping[str, Any], reporter: Any = None,
    ) -> tuple[CachedConclusion, TemplateSnapshot]:
        """提交模板验证：同一指纹只保留一次结果，验证过程中的并发修改以最终配置为准。

        流程是“判定 → 求值 → 重新判定”：
        1. 指纹已在有效期内命中结论时直接复用；
        2. 否则对当前配置求值，并在落盘前重新读取配置；
        3. 若期间模板被并发修改，指纹已经变化，则丢弃中间结论、按最终配置重新判定。
        最终只保留一份结论，归属于最终配置的指纹。
        """
        snapshot = self.snapshot(template_row)
        cached = self.cached(snapshot)
        if cached is not None and cached.reusable:
            return cached, snapshot

        final_snapshot = snapshot
        if reporter is not None:
            updated_row = reporter(snapshot)
            if updated_row is not None:
                final_snapshot = self.snapshot(updated_row)

        # 乐观复核：template_row 通常是仓库里的活记录，评估期间可能被其他请求改写。
        resolved = self.snapshot(template_row)
        if resolved.combined_fingerprint != final_snapshot.combined_fingerprint:
            final_snapshot = resolved

        result_value, reason = self._evaluate(final_snapshot)
        self._store_result(final_snapshot, result_value, reason)
        self.registry.put_snapshot(final_snapshot.entry_id, final_snapshot)
        return self._conclusion(final_snapshot, SOURCE_FRESH, True), final_snapshot

    def _conclusion(self, snapshot: TemplateSnapshot, source: str, reusable: bool) -> CachedConclusion:
        stored = self.registry.get_result(snapshot.entry_id, snapshot.combined_fingerprint)
        return CachedConclusion(
            result=stored.result if stored else RESULT_PENDING,
            signature=stored.signature if stored else snapshot.combined_fingerprint,
            reason=stored.reason if stored else "尚未提交模板验证",
            checked_at=stored.checked_at if stored else _iso(now()),
            source=source,
            reusable=reusable,
        )

    def _store_result(
        self, snapshot: TemplateSnapshot, result_value: str, reason: str,
    ) -> StoredResult:
        checked_at = now()
        # 每个指纹只保留一份结果；重复提交命中缓存时不覆盖已有结论。
        stored = StoredResult(
            result=result_value,
            signature=snapshot.combined_fingerprint,
            reason=reason,
            checked_at=_iso(checked_at),
            expires_at=_iso(checked_at + timedelta(seconds=self.ttl_seconds)),
            run_count=1,
        )
        self.registry.put_result(snapshot.entry_id, stored)
        return stored

    def _evaluate(self, snapshot: TemplateSnapshot) -> tuple[str, str]:
        problems: list[str] = []
        fields = normalize_field_mapping(snapshot.config.get("字段映射"))
        if not snapshot.config.get("请求模板"):
            problems.append("请求模板未登记")
        if not snapshot.config.get("分页配置"):
            problems.append("分页配置未登记")
        if not fields:
            problems.append("字段映射为空")
        elif len(fields) != len(set(fields)):
            problems.append("字段映射存在重复字段")
        if not snapshot.config.get("站点分类"):
            problems.append("站点分类未登记")
        if snapshot.missing_rules:
            problems.append(f"解析规则未登记：{'、'.join(snapshot.missing_rules)}")
        if snapshot.inactive_rules:
            problems.append(f"解析规则已废弃：{'、'.join(snapshot.inactive_rules)}")
        summaries = _reason_summary(snapshot)
        if problems:
            return RESULT_INVALID, f"{'；'.join(problems)}（{summaries}）"
        return RESULT_VALID, f"{summaries}，配置完整"

    # ---- 任务判断 ---------------------------------------------------------
    def judge_task(
        self, task_row: Mapping[str, Any], template_row: Mapping[str, Any],
    ) -> dict[str, Any]:
        """任务只认当前版本指纹：按当前模板版本重新判断能否启动。

        已发布且当前指纹存在有效校验结论才可启动；配置变化后旧结论不再复用，
        任务会被拦下并说明是哪一版、哪一项受影响。已废弃一律不可启动。
        函数只读不写，不会改写已登记任务的历史运行配置。
        """
        snapshot = self.snapshot(template_row)
        status = snapshot.status
        cached = self.cached(snapshot)
        if cached is not None and cached.reusable:
            conclusion = cached
        else:
            stale = self.stale(snapshot)
            if stale is not None and stale.signature != snapshot.combined_fingerprint:
                reason = (
                    f"当前版本 {snapshot.version_id} 尚未重新验证；"
                    f"原校验指纹 {stale.signature} 对应的旧结论已失效（{stale.reason}）"
                )
                source = SOURCE_STALE
            else:
                reason = f"当前版本 {snapshot.version_id} 尚未提交模板验证"
                source = SOURCE_FRESH
            conclusion = CachedConclusion(
                result=RESULT_PENDING,
                signature=snapshot.combined_fingerprint,
                reason=reason,
                checked_at=_iso(now()),
                source=source,
                reusable=False,
            )

        blockers: list[str] = []
        if status == DEPRECATED_STATUS:
            blockers.append("模板已废弃")
        elif status == PUBLISHED_STATUS:
            if not conclusion.reusable:
                blockers.append(conclusion.reason)
            elif conclusion.result != RESULT_VALID:
                blockers.append(f"当前版本校验{conclusion.result}：{conclusion.reason}")
        elif status == VALIDATING_STATUS:
            blockers.append(f"模板仍在验证中，未发布（{conclusion.reason}）")
        else:
            blockers.append(f"模板状态为「{status or '未登记'}」，未发布")

        historical = self.registry.get_task_run_config(int(task_row.get("id") or 0))
        stored = self.registry.get_result(snapshot.entry_id, snapshot.combined_fingerprint)
        return {
            "任务编号": _code(task_row, "任务编号") or _code(task_row, "编号"),
            "模板编号": snapshot.code,
            "模板状态": status,
            "版本标识": snapshot.version_id,
            "校验指纹": snapshot.combined_fingerprint,
            "模板指纹": snapshot.template_fingerprint,
            "规则集指纹": snapshot.rules_fingerprint,
            "校验结论": conclusion.result,
            "结论来源": conclusion.source,
            "结论可复用": conclusion.reusable,
            "校验有效期至": stored.expires_at if stored else None,
            "可启动": not blockers,
            "受影响原因": "；".join(blockers) if blockers else conclusion.reason,
            "完整配置": snapshot.as_dict(),
            "历史运行配置": historical,
        }

    # ---- 初始化 -----------------------------------------------------------
    def bootstrap(self) -> None:
        """服务启动时把种子数据纳入同一套版本判定。

        已发布模板视为已在发布时留下过校验结论，避免演示数据一启动就全部不可用；
        之后的任何配置变化仍走同一套失效传播。
        """
        from app.store import store

        for row in store.rows(MODULE):
            entry_id = int(row.get("id") or 0)
            snapshot = self.snapshot(row)
            self.registry.put_snapshot(entry_id, snapshot)
            if snapshot.status != PUBLISHED_STATUS:
                continue
            if self.registry.get_result(entry_id, snapshot.combined_fingerprint) is None:
                result_value, reason = self._evaluate(snapshot)
                self._store_result(snapshot, result_value, reason)
            row["生效版本"] = snapshot.version_id
            self.registry.mark_effective(entry_id, snapshot.version_id)

    # ---- 回滚 -------------------------------------------------------------
    def rollback(self, template_row: dict[str, Any], version_id: str) -> tuple[TemplateSnapshot | None, str]:
        """回滚到旧生效版本：整版读取旧快照的字段，不和新版本拼接。

        回滚后旧版本的校验结论仍然有效（指纹回到旧值），但旧版本若当时已废弃则不能
        直接用于启动；是否可启动由调用方按回滚后的状态重新判断。
        """
        snapshot = self.registry.find_snapshot(int(template_row.get("id") or 0), version_id)
        if snapshot is None:
            return None, f"模板版本 {version_id} 不存在或未参与过验证"
        if not self.registry.was_effective(snapshot.entry_id, version_id):
            return None, f"模板版本 {version_id} 从未生效，不能作为回滚目标"
        applied = dict(template_row)
        for field_name in TEMPLATE_CONFIG_FIELDS:
            applied[field_name] = snapshot.config.get(field_name)
        applied["生效版本"] = snapshot.version_id
        template_row.clear()
        template_row.update(applied)
        restored = self.snapshot(template_row)
        self.registry.put_snapshot(restored.entry_id, restored)
        cached = self.cached(restored)
        status = str(template_row.get("status") or "")
        if cached is not None and cached.reusable and cached.result != RESULT_VALID:
            return restored, (
                f"模板已回滚到版本 {snapshot.version_id}，但该版本校验结论为「{cached.result}」："
                f"{cached.reason}"
            )
        return restored, f"模板已回滚到版本 {snapshot.version_id}，整套字段按旧版本读取"

    # ---- 版本变更 ---------------------------------------------------------
    def prepare_update(
        self,
        current: Mapping[str, Any],
        changes: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """生成新版本前的字段校验：原子字段组整组提交，配置变化即产生新版本。"""
        candidate = dict(current)
        for field_name in TEMPLATE_CONFIG_FIELDS:
            if field_name in changes:
                candidate[field_name] = changes[field_name]
        errors = atomic_group_errors(changes)
        if errors:
            return None, errors
        ruleset = normalize_ruleset(candidate.get("解析规则集"))
        if not ruleset:
            errors.append("解析规则集不能为空")
        fields = normalize_field_mapping(candidate.get("字段映射"))
        if not fields:
            errors.append("字段映射不能为空")
        if errors:
            return None, errors
        candidate["模板编号"] = normalize_text(candidate.get("模板编号"))
        candidate["适用域名"] = normalize_text(candidate.get("适用域名"))
        candidate["请求模板"] = normalize_text(candidate.get("请求模板"))
        candidate["字符编码"] = normalize_text(candidate.get("字符编码"))
        candidate["解析规则集"] = ruleset
        candidate["字段映射"] = fields
        return candidate, []

    def record_version(self, template_row: Mapping[str, Any]) -> TemplateSnapshot:
        """把当前配置登记为一个版本快照，并记录其校验结论的时间线。"""
        snapshot = self.snapshot(template_row)
        self.registry.put_snapshot(snapshot.entry_id, snapshot)
        return snapshot

    def describe_versions(self, entry_id: int) -> list[dict[str, Any]]:
        from app.store import store

        current = None
        row = store.find(MODULE, entry_id)
        if row is not None:
            current = self.snapshot(row).version_id
        descriptions: list[dict[str, Any]] = []
        for item in self.registry.get_snapshots(entry_id):
            stored = self.registry.get_result(entry_id, item.combined_fingerprint)
            valid_until = stored.expires_at if stored else None
            descriptions.append(
                {
                    "版本标识": item.version_id,
                    "模板指纹": item.template_fingerprint,
                    "规则集指纹": item.rules_fingerprint,
                    "校验指纹": item.combined_fingerprint,
                    "站点分类": item.config.get("站点分类"),
                    "分页配置": item.config.get("分页配置"),
                    "字段映射": list(normalize_field_mapping(item.config.get("字段映射"))),
                    "解析规则": [rule.code for rule in item.rules],
                    "当前版本": item.version_id == current,
                    "曾生效": self.registry.was_effective(entry_id, item.version_id),
                    "校验有效期至": valid_until,
                    "说明": _reason_summary(item),
                }
            )
        return descriptions


version_service = TemplateVersionService()

__all__ = [
    "CachedConclusion",
    "PUBLISHED_STATUS",
    "RESULT_INVALID",
    "RESULT_PENDING",
    "RESULT_VALID",
    "RuleSnapshot",
    "StoredResult",
    "TemplateSnapshot",
    "TemplateVersionService",
    "VersionRegistry",
    "action_intent",
    "resolve",
    "version_service",
]
