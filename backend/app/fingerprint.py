"""配置指纹：模板版本、解析规则与任务校验共用的唯一版本口径。

这里只放纯函数，不依赖内存仓库；示例数据初始化时也能算出同一套指纹。
模板验证、任务排期和回滚动作都通过 ``app/services/versioning.py`` 复用这里的结果，
避免各个动作各自判断版本、各自维护缓存。

指纹的粒度刻意以“整套配置”为单位：
- 模板的请求模板、分页配置、字段映射任一变化，模板指纹立即变化；
- 解析规则的选择器、清洗正则或状态变化，规则指纹立即变化；
- 模板引用的规则集合变化，规则集指纹立即变化。
因此旧校验结论无法串到新配置上，也不需要额外记忆“谁依赖谁”。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

# 模板配置的完整字段集合。生成版本、回滚、任务排期读取配置时都以这一整套为准，
# 不允许把新旧版本的同名字段拼在一起。
TEMPLATE_CONFIG_FIELDS = (
    "模板编号",
    "站点分类",
    "模板名称",
    "解析规则集",
    "请求模板",
    "分页配置",
    "适用域名",
    "字符编码",
    "字段映射",
)

# 模板编号、适用域名、请求模板、字符编码是原子字段组：修改任意一项都必须整组一起提交，
# 避免出现“新域名配旧请求模板”这类半新半旧的版本。
ATOMIC_TEMPLATE_FIELDS = ("模板编号", "适用域名", "请求模板", "字符编码")

# 规则指纹覆盖规则本身与启用状态。规则被改写或停用后，原校验结论立即失效；
# 引用该规则的模板指纹随之变化，失效传播不需要各模块自行记忆依赖关系。
RULE_CONFIG_FIELDS = (
    "规则编号",
    "页面选择器",
    "内容选择器",
    "属性提取器",
    "列表容错",
    "空值占位符",
    "字符清洗正则",
    "输出字段映射",
    "状态",
)

_SPLIT_CHARS = {",", "，", "、", ";", "；", "\n", "\r", "\t", " "}


def split_codes(value: Any, *, dedupe: bool = True) -> list[str]:
    """把规则集/字段映射统一成名称列表，兼容字符串与列表两种写法。

    ``dedupe=False`` 保留重复项，用于校验“字段映射是否重复声明”。
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        parts = []
        current: list[str] = []
        for char in str(value):
            if char in _SPLIT_CHARS:
                if current:
                    parts.append("".join(current))
                    current = []
            else:
                current.append(char)
        if current:
            parts.append("".join(current))
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        name = part.strip()
        if not name:
            continue
        if dedupe:
            if name in seen:
                continue
            seen.add(name)
        result.append(name)
    return result


def normalize_ruleset(value: Any) -> list[str]:
    """解析规则集按名称排序：同一组规则换顺序复用同一校验结论。"""
    return sorted(split_codes(value))


def normalize_field_mapping(value: Any) -> list[str]:
    """字段映射保留声明顺序：输出顺序本身也是配置的一部分。"""
    return split_codes(value)


def normalize_text(value: Any) -> Any:
    """去掉文本两端空白；结构化配置按 JSON 原样保留并参与规范化。"""
    if isinstance(value, str):
        return value.strip()
    return value


def canonical_payload(payload: Mapping[str, Any]) -> str:
    """稳定序列化：字段顺序、空白、列表顺序都不会造成“伪变更”。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(payload: Mapping[str, Any]) -> str:
    """返回短指纹，便于在页面与日志里直接展示。"""
    raw = canonical_payload(payload)
    value = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return value[:16]


def template_config(row: Mapping[str, Any]) -> dict[str, Any]:
    """按固定字段集合抽取模板配置，缺字段时补 None，保证指纹结构稳定。"""
    config: dict[str, Any] = {}
    for field in TEMPLATE_CONFIG_FIELDS:
        value = normalize_text(row.get(field))
        if field == "解析规则集":
            value = normalize_ruleset(value)
        elif field == "字段映射":
            value = normalize_field_mapping(value)
        elif isinstance(value, (list, tuple, set)):
            value = list(value)
        config[field] = value
    return config


def template_fingerprint(row: Mapping[str, Any]) -> str:
    """模板完整配置的校验指纹。"""
    return digest(template_config(row))


def rule_config(row: Mapping[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for field in RULE_CONFIG_FIELDS:
        config[field] = normalize_text(row.get(field))
    return config


def rule_fingerprint(row: Mapping[str, Any]) -> str:
    """单条解析规则的校验指纹。"""
    return digest(rule_config(row))


def ruleset_fingerprint(rule_rows: Iterable[Mapping[str, Any]]) -> str:
    """一组解析规则的联合指纹：规则编号与各自指纹都参与计算。"""
    entries = sorted(
        (
            {
                "规则编号": normalize_text(row.get("规则编号")),
                "规则指纹": rule_fingerprint(row),
            }
            for row in rule_rows
        ),
        key=lambda item: str(item["规则编号"]),
    )
    return digest({"解析规则集": entries})


def combine(template_fp: str, rules_fp: str) -> str:
    """模板指纹与规则集指纹共同决定一份校验结论的归属。"""
    return digest({"模板指纹": template_fp, "规则集指纹": rules_fp})


def atomic_group_errors(changes: Mapping[str, Any]) -> list[str]:
    """模板编号、适用域名、请求模板、字符编码必须作为一组一起提交。

    只提交其中一部分时直接拦下，避免出现“新域名配旧请求模板”这类半新半旧的版本；
    四个字段都提交但内容没变是允许的，指纹不变就不会产生伪变更。
    """
    supplied = [field for field in ATOMIC_TEMPLATE_FIELDS if field in changes]
    if not supplied:
        return []
    missing = [field for field in ATOMIC_TEMPLATE_FIELDS if field not in changes]
    if missing:
        return [
            "模板编号、适用域名、请求模板、字符编码必须同进同退；"
            f"本次缺少「{'、'.join(missing)}」"
        ]
    return []
