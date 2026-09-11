"""
1bot Bridge Hermes plugin

Purpose
-------
Hermes FeishuAdapter 用 cli_aa88479639789bdd app 凭证发飞书消息 — 但
该 app 没权限发到 1bot DM (oc_9727f04f9190044fe5c126422c72a278,
那是 1bot app cli_a96708f4bd389bd3 的 DM).

这个 plugin 注册 Platform.1bot adapter, send() POST 1bot webhook_server
/hermes/send (1bot webhook_server 已有 endpoint, 用 1bot app 凭证发).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from gateway.platforms.base import BasePlatformAdapter, SendResult

logger = logging.getLogger("1bot-bridge")


class OneBotBridgeAdapter(BasePlatformAdapter):
    """1bot Bridge adapter — POST to bot webhook_server /hermes/send"""

    def __init__(self, config):
        from gateway.config import Platform
        super().__init__(config, Platform("1bot"))
        self.base_url = os.environ.get("ONEBOT_BRIDGE_URL", "http://127.0.0.1:14098")
        self.timeout = 10.0

    # BasePlatformAdapter abstract methods (1bot bridge 是 outbound relay,
    # 不需要 inbound connect — 我们只 POST 到 webhook_server)
    async def connect(self, is_reconnect: bool = False) -> bool:
        """1bot bridge 是纯 outbound relay, 无 inbound connect. No-op."""
        return True

    async def disconnect(self) -> None:
        """1bot bridge 是纯 outbound relay, 无 inbound disconnect. No-op."""
        return None

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """1bot bridge 不知道 chat 内部结构. 返空 dict 让上层用默认."""
        return {}

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """POST 1bot webhook_server /hermes/send (同步 IO 跑在线程池)."""
        url = f"{self.base_url}/hermes/send"
        body_obj = {
            "text": content,
            "chat_id": chat_id,
            "receive_id_type": "chat_id",
        }
        if reply_to:
            body_obj["message_id"] = reply_to
        body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

        def _post():
            return urllib.request.urlopen(
                urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                ),
                timeout=self.timeout,
            )

        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, _post)
            body_resp = r.read().decode()
            logger.info("[1bot-bridge] POST %s -> HTTP %s body=%s", url, r.status, body_resp[:200])
            if r.status == 200:
                try:
                    parsed = json.loads(body_resp)
                    mid = parsed.get("message_id")
                    return SendResult(success=True, message_id=mid)
                except Exception:
                    return SendResult(success=True, message_id=None)
            return SendResult(success=False, error=f"HTTP {r.status}: {body_resp[:200]}")
        except urllib.error.HTTPError as e:
            body_resp = e.read().decode() if e.fp else ""
            logger.warning("[1bot-bridge] HTTPError %s: %s", e.code, body_resp[:200])
            return SendResult(success=False, error=f"HTTP {e.code}: {body_resp[:200]}")
        except Exception as exc:
            logger.warning("[1bot-bridge] POST failed: %s", exc)
            return SendResult(success=False, error=str(exc))


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""

    def _check_fn() -> bool:
        """1bot 不需要额外 dep — urllib stdlib 就够. 1bot webhook_server 跑着 = true."""
        try:
            import urllib.request
            urllib.request.urlopen(
                f"{os.environ.get('ONEBOT_BRIDGE_URL', 'http://127.0.0.1:14098')}/health",
                timeout=2,
            )
            return True
        except Exception:
            # webhook_server 没起来时仍允许 register, 让 send() 时返 SendResult(success=False)
            return True

    ctx.register_platform(
        name="1bot",
        label="1bot Bridge",
        adapter_factory=lambda c: OneBotBridgeAdapter(c),
        check_fn=_check_fn,
    )
    logger.info(
        "[1bot-bridge] registered Platform.1bot → %s",
        os.environ.get("ONEBOT_BRIDGE_URL", "http://127.0.0.1:14098"),
    )