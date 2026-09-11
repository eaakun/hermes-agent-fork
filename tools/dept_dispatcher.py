"""
部门自动分发器 — 读取 group_routing.json，对消息内容做关键词匹配，
将消息路由到对应部门飞书群。可单独调用，也可嵌入 gateway/agent loop。
"""
import json
from pathlib import Path
from typing import Optional

# 优先读 1bot 的 group_routing（主数据源），备选 Hermes 内嵌副本
ROUTING_PATHS = [
    Path("/Users/eaakun/1bot/.cook_data/group_routing.json"),
    Path("/Users/eaakun/1cook/.cook_data/group_routing.json"),
]

def _load_routing():
    for p in ROUTING_PATHS:
        if p.exists():
            return json.loads(p.read_text())
    return None

def detect_dept(message_text: str) -> Optional[str]:
    """
    根据消息文本关键词匹配部门逻辑名。
    返回 dept key（如 'rd'/'sales'），未命中返回 None。
    """
    routing = _load_routing()
    if not routing:
        return None
    text = message_text.lower()
    scores = {}
    for rule in routing.get("routing", []):
        dept = rule["dept"]
        keywords = rule.get("keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > 0:
            scores[dept] = scores.get(dept, 0) + score
    if not scores:
        return None
    return max(scores, key=scores.get)

def get_chat_id(dept: str) -> Optional[str]:
    """根据部门逻辑名查 chat_id，fallback 到 management"""
    routing = _load_routing()
    if not routing:
        return None
    depts = routing.get("departments", {})
    return depts.get(dept, {}).get("chat_id") or depts.get("management", {}).get("chat_id")

def get_label(dept: str) -> str:
    """部门中文标签"""
    routing = _load_routing()
    if not routing:
        return dept
    return routing.get("departments", {}).get(dept, {}).get("label", dept)

def route(message_text: str) -> Optional[tuple[str, str]]:
    """
    主入口：输入消息文本，返回 (chat_id, label) 或 None。
    chat_id 可直接用于 feishu 发送。
    """
    dept = detect_dept(message_text)
    if not dept:
        return None
    chat_id = get_chat_id(dept)
    if not chat_id:
        return None
    return chat_id, get_label(dept)

if __name__ == "__main__":
    tests = [
        "有个新配方要申请专利",
        "供应商的原料检测报告出来了",
        "这批出货的报价单需要你确认",
        "车间产线明天排产计划",
        "wms库存数据对不上",
    ]
    for msg in tests:
        result = route(msg)
        print(f"[{msg[:10]}...] → {result}")
