"""
Context checkpoint module — 9/4 三层防护第 2 层.

设计:
  - detect_compression_handoff(): 扫 history 找 [CONTEXT COMPACTION …] / [CONTEXT SUMMARY]: … handoff
  - record_checkpoint(): 异步线程写 JSONL (fire-and-forget, 不阻塞主链路)
  - recent_checkpoints_for_session(): 新会话注入用,按 session_id 取最近 N 条
  - empty_response_marker(): 第三层防护——LLM 跑完无内容时给用户一行可见状态

落盘: ~/.hermes/context_checkpoints.jsonl
  每行: {"ts": ISO8601, "session_id": str, "source": str,
         "endpoint": str, "summary_excerpt": str (前 500 字),
         "user_message_excerpt": str (前 200 字), "length": int}

为啥不用 supermemory API:
  - supermemory plugin 在 Hermes 进程未启用(无 ~/.hermes/supermemory.json、无进程)
  - 引入外网依赖增加 LLM 链路断的面积,违背"宁可输出无意义进度也不要沉默掉链子"原则
  - 本地 JSONL 可 grep、可 diff、可在新会话注入,符合 local-first 偏好
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_CHECKPOINT_FILE = _HERMES_HOME / "context_checkpoints.jsonl"
_CHECKPOINT_FILE_LOCK = threading.Lock()

# 匹配 Hermes 截断时插入的 handoff: "[CONTEXT COMPACTION — …]" 或 "[CONTEXT SUMMARY]: …"
_HANDBOFF_PATTERN = re.compile(
    r"\[CONTEXT\s+(?:COMPACTION|SUMMARY)[^\]]*\][\s\S]*?(?=\n\n|\Z)",
    re.IGNORECASE,
)
# fallback: 单行前缀扫描(防止跨行总结被吃)
_PREFIX_PATTERN = re.compile(
    r"^\s*\[CONTEXT\s+(?:COMPACTION|SUMMARY)[^\]]*\]",
    re.IGNORECASE | re.MULTILINE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def detect_compression_handoff(history: Any) -> Optional[str]:
    """扫 history 找压缩 handoff 块.

    Args:
        history: list of message dicts (role/content) 或 str 列表

    Returns:
        handoff 文本(去前缀包装),没找到返回 None
    """
    if not history:
        return None
    chunks: List[str] = []
    if isinstance(history, str):
        chunks = [history]
    elif isinstance(history, list):
        for m in history:
            if not isinstance(m, dict):
                if isinstance(m, str):
                    chunks.append(m)
                continue
            content = m.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chunks.append(part["text"])
    if not chunks:
        return None
    full = "\n\n".join(chunks)
    m = _HANDBOFF_PATTERN.search(full)
    if m:
        return m.group(0).strip()
    prefix_match = _PREFIX_PATTERN.search(full)
    if prefix_match:
        idx = prefix_match.start()
        return full[idx: idx + 2048].strip()
    return None


def record_checkpoint(
    session_id: str,
    handoff_text: str,
    *,
    endpoint: str = "",
    user_message_excerpt: str = "",
    source: str = "auto",
) -> None:
    """异步落盘 checkpoint — fire-and-forget, 失败不抛.

    设计: 用户压缩感知不应拖慢主链路. 用 daemon 线程写文件,
    写失败仅 logger.warning,不打断 LLM 调用.
    """
    def _write():
        try:
            excerpt = (handoff_text or "").strip()[:500]
            user_excerpt = (user_message_excerpt or "").strip()[:200]
            row = {
                "ts": _now_iso(),
                "session_id": session_id or "unknown",
                "source": source,
                "endpoint": endpoint,
                "summary_excerpt": excerpt,
                "user_message_excerpt": user_excerpt,
                "length": len(handoff_text or ""),
            }
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            with _CHECKPOINT_FILE_LOCK:
                _HERMES_HOME.mkdir(parents=True, exist_ok=True)
                with _CHECKPOINT_FILE.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:  # pragma: no cover — 防御
            import logging
            logging.getLogger(__name__).warning("checkpoint write failed: %s", e)

    t = threading.Thread(target=_write, name="ctx-checkpoint-writer", daemon=True)
    t.start()


def maybe_checkpoint(
    *,
    session_id: str,
    history: Any,
    user_message: str = "",
    endpoint: str = "",
    source: str = "auto",
) -> bool:
    """高层入口: 扫到 handoff 才写. 返回是否触发了 checkpoint."""
    handoff = detect_compression_handoff(history)
    if not handoff:
        return False
    record_checkpoint(
        session_id=session_id,
        handoff_text=handoff,
        endpoint=endpoint,
        user_message_excerpt=user_message,
        source=source,
    )
    return True


def recent_checkpoints_for_session(
    session_id: str, *, limit: int = 5
) -> List[Dict[str, Any]]:
    """新会话启动时注入用 — 取最近 N 条同 session 的 checkpoint."""
    if not session_id or not _CHECKPOINT_FILE.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with _CHECKPOINT_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("session_id") == session_id:
                    rows.append(r)
    except Exception:
        return []
    return rows[-limit:]


def empty_response_marker(
    *,
    final_response: str,
    elapsed_sec: float,
    usage: Any = None,
    max_chars: int = 400,
) -> str:
    """第三层防护: LLM 跑完但没内容时,给用户一行可见状态.

    用户偏好: "宁可输出无意义进度也不要沉默掉链子".
    只有 final_response 真正为空/空白才注入 marker.
    """
    if final_response and final_response.strip():
        return final_response
    parts = [
        f"[状态] LLM 已跑完,本次无文本内容输出 (用时 {elapsed_sec:.1f}s).",
        "可能原因: 压缩触发, history 被截断,或 LLM 返回空. ",
    ]
    if usage:
        try:
            u = usage if isinstance(usage, dict) else {}
            inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
            out = u.get("output_tokens") or u.get("completion_tokens") or 0
            if inp or out:
                parts.append(f"tokens: in={inp} out={out}. ")
        except Exception:
            pass
    parts.append("如需推进下一步请直接说话,我会从上下文感知恢复. ")
    marker = "".join(parts)
    return marker[:max_chars]


# 模块自检 (供调试 / 直接 python3 调用)
if __name__ == "__main__":  # pragma: no cover
    sample = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "[CONTEXT COMPACTION — summarizing earlier conversation]\ngoal: 修 1bot 上下文\nkey decisions: a/b/c"},
    ]
    h = detect_compression_handoff(sample)
    print("handoff:", bool(h), h[:80] if h else None)
    record_checkpoint("test_sid", h or "", endpoint="/test", user_message_excerpt="hi")
    print("recent:", recent_checkpoints_for_session("test_sid"))
    print("empty:", empty_response_marker(final_response="", elapsed_sec=1.2, usage={"input_tokens": 50, "output_tokens": 0})[:80])