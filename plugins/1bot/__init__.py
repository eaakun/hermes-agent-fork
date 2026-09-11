"""
1bot plugin for Hermes

Provides integration between Hermes and 1bot for engineering task execution.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("1bot-task-tool")

# ── Configuration ──

ONEBOT_API_URL = os.environ.get("ONEBOT_API_URL", "http://127.0.0.1:14098")
HERMES_FEISHU_CHAT_ID = os.environ.get("FEISHU_HOME_CHANNEL", "oc_302628d7c6dc1e59b141396ee790af87")


# ── Tool Schema ──

SEND_TASK_SCHEMA = {
    "name": "send_task_to_1bot",
    "description": "Send an engineering task to 1bot for execution. 1bot will use its PCR (Plan-Code-Review) pipeline to complete the task and report results back via Feishu.",
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Clear description of what needs to be built or done"
            },
            "requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of specific requirements or acceptance criteria"
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high", "urgent"],
                "description": "Task priority level"
            }
        },
        "required": ["description"]
    }
}


# ── Tool Handler ──

async def _handle_send_task(args: dict, **kwargs) -> str:
    """Send an engineering task to 1bot for execution."""
    import httpx
    
    description = args.get("description", "")
    requirements = args.get("requirements", [])
    priority = args.get("priority", "medium")
    
    if not description:
        return json.dumps({"success": False, "error": "description is required"})
    
    # Generate unique task ID
    hermes_task_id = f"hermes-{uuid.uuid4().hex[:8]}"
    
    # Prepare request payload
    payload = {
        "task_id": hermes_task_id,
        "description": description,
        "requirements": requirements,
        "priority": priority,
        "callback_chat_id": HERMES_FEISHU_CHAT_ID,
        "callback_message_prefix": f"[Hermes Task {hermes_task_id}]"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ONEBOT_API_URL}/api/task",
                json=payload
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"[1bot-task] Task {hermes_task_id} sent to 1bot: {result}")
                return json.dumps({
                    "success": True,
                    "task_id": hermes_task_id,
                    "1bot_task_id": result.get("1bot_task_id"),
                    "status": "accepted",
                    "message": f"Task sent to 1bot successfully. 1bot will execute using PCR pipeline and report results via Feishu.",
                    "estimated_time": "5-15 minutes depending on complexity"
                })
            else:
                error_msg = f"1bot returned status {response.status_code}: {response.text}"
                logger.error(f"[1bot-task] {error_msg}")
                return json.dumps({
                    "success": False,
                    "error": error_msg,
                    "task_id": hermes_task_id
                })
    
    except httpx.ConnectError:
        error_msg = "Cannot connect to 1bot. Make sure 1bot webhook server is running on port 14098."
        logger.error(f"[1bot-task] {error_msg}")
        return json.dumps({
            "success": False,
            "error": error_msg,
            "task_id": hermes_task_id
        })
    except Exception as e:
        error_msg = f"Failed to send task to 1bot: {str(e)}"
        logger.error(f"[1bot-task] {error_msg}")
        return json.dumps({
            "success": False,
            "error": error_msg,
            "task_id": hermes_task_id
        })


async def _handle_task_status(args: dict, **kwargs) -> str:
    """Query task status from 1bot."""
    import httpx
    
    task_id = args.get("task_id", "")
    
    if not task_id:
        return json.dumps({"success": False, "error": "task_id is required"})
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{ONEBOT_API_URL}/api/task/{task_id}"
            )
            
            if response.status_code == 200:
                result = response.json()
                return json.dumps({
                    "success": True,
                    "task": result
                })
            elif response.status_code == 404:
                return json.dumps({
                    "success": False,
                    "error": "Task not found"
                })
            else:
                error_msg = f"1bot returned status {response.status_code}: {response.text}"
                return json.dumps({
                    "success": False,
                    "error": error_msg
                })
    
    except httpx.ConnectError:
        return json.dumps({
            "success": False,
            "error": "Cannot connect to 1bot"
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        })


# ── Tool Schemas ──

TASK_STATUS_SCHEMA = {
    "name": "get_1bot_task_status",
    "description": "Query the status of a task sent to 1bot",
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID returned by send_task_to_1bot"
            }
        },
        "required": ["task_id"]
    }
}


# ── Plugin Registration ──

def register(ctx) -> None:
    """Register 1bot tools with the plugin context."""
    ctx.register_tool(
        name="send_task_to_1bot",
        toolset="1bot",
        schema=SEND_TASK_SCHEMA,
        handler=_handle_send_task,
        emoji="🤖",
    )
    
    ctx.register_tool(
        name="get_1bot_task_status",
        toolset="1bot",
        schema=TASK_STATUS_SCHEMA,
        handler=_handle_task_status,
        emoji="📊",
    )
    
    logger.info("[1bot] Tools registered: send_task_to_1bot, get_1bot_task_status")
