"""版本缓存与失效传播的核心用例：直接用服务层验证语义，不依赖 HTTP。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.versioning import (
    RESULT_INVALID,
    RESULT_VALID,
    TemplateVersionService,
    VersionRegistry,
    now,
)
from app.store import store


def template_row(entry_id: int = 1) -> dict:
    return store.find("templatelib", entry_id)


def test_template_fingerprint_changes_on_paging_or_field_mapping() -> None:
    service = TemplateVersionService()
    before = service.snapshot(template_row())
    row = template_row()
    row["分页配置"] = "单页列表"
    bumped = service.snapshot(row)
    assert before.combined_fingerprint != bumped.combined_fingerprint

    row["分页配置"] = before.config["分页配置"]
    row["字段映射"] = "标题,链接,发布时间,来源,正文,作者,编辑"
    fewer = service.snapshot(row)
    assert fewer.combined_fingerprint != before.combined_fingerprint


def test_ruleset_fingerprint_is_order_insensitive_but_reorder_safe() -> None:
    service = TemplateVersionService()
    row = template_row()
    first = service.snapshot(row)
    row["解析规则集"] = "PARS-0002,PARS-0001"
    second = service.snapshot(row)
    assert first.rules_fingerprint == second.rules_fingerprint
    assert first.combined_fingerprint == second.combined_fingerprint


def test_rule_change_invalidates_template_conclusion() -> None:
    service = TemplateVersionService()
    before = service.snapshot(template_row())
    store.find("parserule", 1)["内容选择器"] = ".news-title-v2"
    after = service.snapshot(template_row())
    assert before.rules_fingerprint != after.rules_fingerprint
    assert before.combined_fingerprint != after.combined_fingerprint


def test_validation_result_is_reused_for_same_fingerprint() -> None:
    service = TemplateVersionService()
    first, _ = service.run_validation(template_row())
    second, _ = service.run_validation(template_row())
    assert first.result == RESULT_VALID
    assert second.source == "cache"
    assert second.signature == first.signature
    # 同一指纹只保留一份结论：重复提交不新增记录，也不会改写已存结论。
    assert len(service.registry.get_result(1, first.signature).signature) > 0
    assert service.registry.get_result(1).run_count == 1


def test_expired_conclusion_is_not_reused() -> None:
    service = TemplateVersionService(ttl_seconds=1)
    first, _ = service.run_validation(template_row())
    assert first.result == RESULT_VALID
    later = now() + timedelta(seconds=5)
    cached = service.cached(service.snapshot(template_row()), at=later)
    assert cached is not None
    assert cached.reusable is False
    assert cached.source == "stale"


def test_concurrency_during_validation_uses_final_config() -> None:
    service = TemplateVersionService()

    def reporter(snapshot):  # 模拟验证过程中被并发修改
        row = template_row()
        row["分页配置"] = "第1页列表 + 第2页正文详情 + 第3页评论（共三页）"
        return row

    conclusion, snapshot = service.run_validation(template_row(), reporter=reporter)
    final = service.snapshot(template_row())
    assert conclusion.signature == final.combined_fingerprint
    assert snapshot.version_id == final.version_id
    # 只保留最终配置的一份结论，中间状态不会留下可复用的记录
    assert list(service.registry._results[1]) == [final.combined_fingerprint]
    assert service.registry.get_result(1).run_count == 1


def test_task_judgement_blocks_when_template_not_published() -> None:
    service = TemplateVersionService()
    task = store.find("taskcrawl", 3)
    template = store.find("templatelib", 2)
    verdict = service.judge_task(task, template)
    assert verdict["可启动"] is False
    assert "验证中" in verdict["受影响原因"]


def test_task_judgement_reads_current_version_and_flags_stale_conclusion() -> None:
    service = TemplateVersionService()
    task = store.find("taskcrawl", 2)
    template = template_row()
    service.run_validation(template)
    verdict = service.judge_task(task, template)
    assert verdict["可启动"] is True
    assert verdict["校验指纹"] == service.snapshot(template).combined_fingerprint

    template["分页配置"] = "单页列表"
    changed = service.judge_task(task, template)
    assert changed["版本标识"] != verdict["版本标识"]
    assert changed["校验指纹"] != verdict["校验指纹"]
    assert "分页配置" in changed["完整配置"]["配置"]


def test_rollback_restores_whole_version_without_splicing() -> None:
    service = TemplateVersionService()
    row = template_row()
    service.run_validation(row)
    before = service.snapshot(row)
    service.registry.mark_effective(1, before.version_id)

    row["分页配置"] = "三页分页"
    row["字段映射"] = "标题,链接"
    service.record_version(row)

    restored, message = service.rollback(row, before.version_id)
    assert restored is not None
    assert restored.version_id == before.version_id
    assert restored.combined_fingerprint == before.combined_fingerprint
    assert restored.config["分页配置"] == before.config["分页配置"]
    assert len(restored.config["字段映射"]) == 8
    assert "回滚" in message


def test_rollback_rejects_version_never_effective() -> None:
    service = TemplateVersionService()
    row = template_row()
    service.run_validation(row)
    snapshot = service.snapshot(row)
    _, message = service.rollback(row, snapshot.version_id)
    assert "从未生效" in message


def test_atomic_fields_must_move_together() -> None:
    service = TemplateVersionService()
    row = template_row()
    _, errors = service.prepare_update(row, {"适用域名": "news2.example.com"})
    assert errors
    assert "同进同退" in errors[0]

    candidate, errors = service.prepare_update(
        row,
        {
            "模板编号": "TEMP-0001",
            "适用域名": "news2.example.com",
            "请求模板": "GET https://news2.example.com/list?page={page}",
            "字符编码": "UTF-8",
        },
    )
    assert candidate is not None
    assert not errors


def test_cache_is_keyed_by_fingerprint_so_old_results_do_not_leak() -> None:
    registry = VersionRegistry()
    service = TemplateVersionService(registry=registry)
    first, _ = service.run_validation(template_row())
    row = template_row()
    row["分页配置"] = "单页列表"
    second, snapshot = service.run_validation(row)
    assert second.signature != first.signature
    assert registry.get_result(1, first.signature) is not None
    assert registry.get_result(1, second.signature) is not None
    assert service.cached(snapshot).reusable is True


def test_validation_failure_marks_abnormal_and_blocks_start() -> None:
    service = TemplateVersionService()
    row = store.find("templatelib", 3)
    conclusion, _ = service.run_validation(row)
    assert conclusion.result == RESULT_INVALID
    assert "请求模板未登记" in conclusion.reason
    verdict = service.judge_task(store.find("taskcrawl", 4), row)
    assert verdict["可启动"] is False
