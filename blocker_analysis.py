"""Generic, conservative summary for locally recorded blockers."""


def explain(item):
    if item.get("status") != "阻塞":
        return None
    reason = item.get("blocker", "").strip() or "当前任务尚未提供可核实的阻塞原因。"
    return {
        "summary": reason,
        "basis": "依据本机记录的状态与原因，不推断未验证的根因。",
        "topics": [{"key": "recorded", "title": "待处理的阻塞", "impact": reason,
                    "solution": "核对当前会话和证据，明确下一步与验证方式后再继续。", "tests": []}],
        "unexplained_tests": [],
        "approval": {"scope": "develop",
                     "next_step": "先核对记录的阻塞原因，再拟定本地排查和验证范围。",
                     "exclusions": "不包含推送、合并、部署或对外部系统的操作。"},
    }
