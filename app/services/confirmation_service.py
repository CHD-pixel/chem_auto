from __future__ import annotations

from typing import Any

from app.schemas.common import UserConfirmation

_YES_WORDS = {"是", "对", "正确", "已经", "yes", "y", "ok", "确认", "成功"}
_NO_WORDS = {"否", "不是", "没有", "不对", "失败", "no", "n", "不成功"}
_UNCERTAIN_WORDS = {"不确定", "不知道", "看不出来", "uncertain", "not sure"}


def normalize_user_confirmation(reply: str) -> UserConfirmation:
    text = reply.strip().lower()
    if text in _YES_WORDS:
        return "yes"
    if text in _NO_WORDS:
        return "no"
    if text in _UNCERTAIN_WORDS:
        return "uncertain"
    return "uncertain"


def merge_tool_and_user_confirmation(
    tool_success: bool,
    user_confirmation: UserConfirmation,
) -> dict[str, Any]:
    if tool_success and user_confirmation == "yes":
        return {
            "final_verdict": "pass",
            "should_continue_testing": True,
            "should_trigger_repair": False,
            "should_stop_for_human": False,
            "reason": "Tool reported success and user confirmed physical effect.",
        }
    if not tool_success:
        return {
            "final_verdict": "fail",
            "should_continue_testing": False,
            "should_trigger_repair": True,
            "should_stop_for_human": False,
            "reason": "Tool execution failed.",
        }
    if user_confirmation == "no":
        return {
            "final_verdict": "fail",
            "should_continue_testing": False,
            "should_trigger_repair": True,
            "should_stop_for_human": False,
            "reason": "Tool reported success but user denied physical effect.",
        }
    return {
        "final_verdict": "manual_review",
        "should_continue_testing": False,
        "should_trigger_repair": False,
        "should_stop_for_human": True,
        "reason": "User confirmation is uncertain.",
    }


