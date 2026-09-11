"""
1bot_task — Hermes tool for sending tasks to 1bot

This tool allows Hermes to dispatch engineering tasks to 1bot for execution.
1bot will use its PCR (Plan-Code-Review) pipeline to complete the task.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Optional
from dataclasses import dataclass

logger = logging.getLogger("1bot-task-tool")

# ── Configuration ──

ONEBOT_API_URL = os.environ.get("ONEBOT_API_URL", "http://127.0.0.1:14098")
HERMES_FEISHU_CHAT_ID = os.environ.get("FEISHU_HOME_CHANNEL", "oc_302628d7c6dc1e59b141396ee790af87")


# ── Tool Definition ──

TOOL_SCHEMA = {
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


# ── Tool Implementation ──

async def send_task_to_1bot(
    description: str,
    requirements: list[str] = None,
    priority: str = "medium"
) -> dict:
    """
    Send an engineering task to 1bot for execution.
    
    Args:
        description: Clear description of what needs to be built or done
        requirements: List of specific requirements or acceptance criteria
        priority: Task priority level (low/medium/high/urgent)
    
    Returns:
        dict with task_id, status, and message
    """
    import httpx
    
    if requirements is None:
        requirements = []
    
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
                return {
                    "success": True,
                    "task_id": hermes_task_id,
                    "1bot_task_id": result.get("1bot_task_id"),
                    "status": "accepted",
                    "message": f"Task sent to 1bot successfully. 1bot will execute using PCR pipeline and report results via Feishu.",
                    "estimated_time": "5-15 minutes depending on complexity"
                }
            else:
                error_msg = f"1bot returned status {response.status_code}: {response.text}"
                logger.error(f"[1bot-task] {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "task_id": hermes_task_id
                }
    
    except httpx.ConnectError:
        error_msg = "Cannot connect to 1bot. Make sure 1bot webhook server is running on port 14098."
        logger.error(f"[1bot-task] {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "task_id": hermes_task_id
        }
    except Exception as e:
        error_msg = f"Failed to send task to 1bot: {str(e)}"
        logger.error(f"[1bot-task] {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "task_id": hermes_task_id
        }


# ── Tool Registration ──

def register_tools(registry):
    """Register 1bot task tools with the tool registry."""
    registry.register_tool(
        name="send_task_to_1bot",
        handler=send_task_to_1bot,
        schema=TOOL_SCHEMA,
        description="Send engineering tasks to 1bot for PCR pipeline execution"
    )
    logger.info("[1bot-task] Tool registered: send_task_to_1bot")
