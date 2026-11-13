"""接口层用例：验证跨页面联动、并发修改与回滚在 HTTP 语义上成立。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as instance:
        yield instance


def publish(client: TestClient, entry_id: int = 1) -> dict:
    response = client.post(f"/api/templatelib/{entry_id}/actions", json={"values": {"action": "发布模板"}})
    assert response.status_code == 200
    return response.json()


def test_static_routes_are_not_shadowed_by_entry_id(client: TestClient) -> None:
    assert client.get("/api/templatelib/export").status_code == 200
    assert client.get("/api/taskcrawl/schedule").status_code == 200
    assert client.get("/api/scheduler/schedule").status_code == 200


def test_repeated_validation_keeps_single_result(client: TestClient) -> None:
    for _ in range(3):
        response = client.post(
            "/api/templatelib/1/actions", json={"values": {"action": "提交模板验证"}},
        )
        assert response.json()["ok"] is True
    detail = client.get("/api/templatelib/1/versions").json()
    assert detail["提交次数"] == 1
    assert detail["校验结论"]["结果"] == "通过"


def test_template_change_invalidates_old_conclusion_and_flags_tasks(client: TestClient) -> None:
    publish(client)
    before = client.get("/api/taskcrawl/2/impact").json()
    assert before["可启动"] is True

    patch = client.patch("/api/templatelib/1", json={"values": {"分页配置": "三页分页"}})
    assert patch.json()["ok"] is True

    after = client.get("/api/taskcrawl/2/impact").json()
    assert after["可启动"] is False
    assert after["结论来源"] == "stale"
    assert after["校验指纹"] != before["校验指纹"]
    assert "旧结论已失效" in after["受影响原因"]

    schedule = client.get("/api/taskcrawl/schedule").json()
    assert schedule["受影响任务数"] >= 1
    listed = [item for item in schedule["任务"] if item["任务编号"] == "TASK-0002"][0]
    assert listed["版本标识"] == after["版本标识"]


def test_task_schedule_view_matches_template_impact_cross_page(client: TestClient) -> None:
    publish(client)
    client.patch("/api/templatelib/1", json={"values": {"字段映射": "标题,链接"}})

    schedule = client.get("/api/taskcrawl/schedule?template_code=TEMP-0001").json()
    impact = client.get("/api/templatelib/1/impact").json()
    assert schedule["受影响任务数"] == impact["受影响任务数"]
    schedule_reason = schedule["任务"][0]["受影响原因"]
    impact_reason = [item for item in impact["任务"] if item["任务编号"] == schedule["任务"][0]["任务编号"]][0]["受影响原因"]
    assert impact_reason == schedule_reason


def test_failed_validation_blocks_schedule_and_start(client: TestClient) -> None:
    response = client.post(
        "/api/templatelib/3/actions", json={"values": {"action": "提交模板验证"}},
    )
    assert "未通过" in response.json()["message"]
    assert response.json()["entry"]["abnormal"] is True

    start = client.post("/api/taskcrawl/4/actions", json={"values": {"action": "启动任务"}}).json()
    assert start["ok"] is False
    assert "不可启动" in start["message"]

    publish = client.post(
        "/api/templatelib/3/actions", json={"values": {"action": "发布模板"}},
    ).json()
    assert publish["ok"] is False
    assert "不能发布" in publish["message"]
    assert client.get("/api/templatelib/3").json()["status"] == "模板验证中"
    assert client.get("/api/templatelib/3").json()["abnormal"] is True


def test_deprecated_template_cannot_start_but_history_is_intact(client: TestClient) -> None:
    publish(client)
    started = client.post("/api/taskcrawl/2/actions", json={"values": {"action": "启动任务"}}).json()
    assert started["ok"] is True
    history = client.get("/api/taskcrawl/2/impact").json()["历史运行配置"]
    assert len(history["字段映射"]) == 8

    client.post("/api/templatelib/1/actions", json={"values": {"action": "废弃模板"}})
    verdict = client.get("/api/taskcrawl/2/impact").json()
    assert verdict["可启动"] is False
    assert "已废弃" in verdict["受影响原因"]
    assert verdict["历史运行配置"] == history


def test_rollback_reads_whole_version_and_keeps_history(client: TestClient) -> None:
    publish(client)
    old_version = client.get("/api/templatelib/1/versions").json()["版本标识"]

    client.patch(
        "/api/templatelib/1",
        json={"values": {"分页配置": "三页分页", "字段映射": "标题,链接", "解析规则集": "PARS-0001"}},
    )
    rolled = client.post(
        "/api/templatelib/1/rollback", json={"values": {"version_id": old_version}},
    ).json()
    assert rolled["ok"] is True

    row = client.get("/api/templatelib/1").json()
    assert row["分页配置"] == "第1页列表 + 第2页正文详情（共两页）"
    assert len(row["字段映射"]) == 8
    assert row["解析规则集"] == ["PARS-0001", "PARS-0002"]

    task = client.get("/api/taskcrawl/2/impact").json()
    assert task["版本标识"] == old_version
    assert len(task["完整配置"]["配置"]["字段映射"]) == 8
    assert task["完整配置"]["配置"]["分页配置"] == row["分页配置"]

    history = task["历史运行配置"]
    assert history is None or history["版本标识"] != old_version


def test_atomic_field_group_rejects_partial_update(client: TestClient) -> None:
    response = client.patch(
        "/api/templatelib/1", json={"values": {"请求模板": "GET https://other.example.com/?page={page}"}},
    ).json()
    assert response["ok"] is False
    assert "同进同退" in response["message"]

    accepted = client.patch(
        "/api/templatelib/1",
        json={
            "values": {
                "模板编号": "TEMP-0001",
                "适用域名": "news.example.com",
                "请求模板": "GET https://news.example.com/list?page={page}&size=20",
                "字符编码": "UTF-8",
            },
        },
    ).json()
    assert accepted["ok"] is True


def test_scheduler_view_uses_same_verdict(client: TestClient) -> None:
    publish(client)
    plan = client.get("/api/scheduler/1/impact").json()
    assert plan["可排期"] is True
    assert plan["校验指纹"] == client.get("/api/taskcrawl/1/impact").json()["校验指纹"]

    client.patch("/api/templatelib/1", json={"values": {"分页配置": "三页分页"}})
    blocked = client.get("/api/scheduler/1/impact").json()
    assert blocked["可排期"] is False
    assert "旧结论已失效" in blocked["受影响原因"]
