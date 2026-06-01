"""
决策引擎：基于 understanding + semantic_match + task_state 统一决策。
不再用关键词/正则路由 service_card。
"""

from __future__ import annotations

from typing import Any, Literal

from app.agent.task_state import TaskState, TaskStage, get_quick_replies_for_stage, build_task_guidance_message
from app.storage.guide_store import GuideKnowledgeBase, semantic_match, _normalize_record


# 决策结果类型
DecisionResult = Literal[
    "service_card",
    "clarification_required",
    "agent_task_guidance",
    "no_verified_guide",
    "unsupported",
]


def make_decision(
    understanding: dict[str, Any],
    task_state: TaskState | None,
    candidates: list[dict[str, Any]] | None,
    kb: GuideKnowledgeBase,
) -> dict[str, Any]:
    """
    统一决策函数。

    返回：
    {
        "decision": DecisionResult,
        "service_item_code": str | None,
        "service_item_name": str | None,
        "guide_record": dict | None,
        "task_state": TaskState,
        "message": str,
        "quick_replies": list[dict],
        "progress_events": list[dict],
        "sources": list[dict],
    }
    """
    is_public_service = understanding.get("is_public_service", True)
    u_confidence = understanding.get("confidence", 0.5)

    # 非公共服务 → unsupported
    if is_public_service is False:
        return _build_unsupported(understanding)

    # 如果 task_state 已存在且在推进中
    if task_state and task_state.stage != TaskStage.CONFIRM_GOAL:
        return _advance_task_decision(task_state, understanding, candidates, kb)

    # 初次理解：基于 semantic_match candidates 决策
    if candidates and candidates[0].get("score", 0) >= 0.30:
        top_key = candidates[0]["record_key"]
        top_score = candidates[0].get("score", 0)
        matched_record = kb.get(top_key)
        if matched_record:
            rec = _normalize_record(matched_record, top_key)
            return _build_service_card(top_key, rec, understanding)

    # semantic_match 未命中 → agent_task_guidance
    return _build_agent_task_guidance(understanding, task_state, candidates)


def _build_service_card(
    code: str,
    record: dict[str, Any],
    understanding: dict[str, Any],
) -> dict[str, Any]:
    """构建 service_card 响应"""
    guide = record.get("guide") or {}
    sources = record.get("sources") or []

    from app.agent.workflow import calculate_freshness
    freshness = calculate_freshness(record.get("fetched_at"))
    _name = record.get("service_item_name") or record.get("service_code", "事项")
    city = understanding.get("city") or record.get("city") or ""
    card = {
        "title": f"{city}{_name}指南" if city else f"{_name}指南",
        "summary": guide.get("summary") or "",
        "service_item_code": code,
        "service_item_name": record.get("service_item_name"),
        "conditions": guide.get("conditions") or [],
        "materials": guide.get("materials") or [],
        "methods": guide.get("methods") or [],
        "steps": guide.get("steps") or [],
        "online_entry": guide.get("online_entry") or [],
        "offline_locations": guide.get("offline_locations") or [],
        "processing_time": guide.get("processing_time") or "",
        "fees": guide.get("fees") or "",
        "tips": guide.get("tips") or [],
        "sources": sources,
        "fetched_at": record.get("fetched_at"),
        "reviewed_at": record.get("reviewed_at"),
        "freshness": freshness,
        "freshness_warning": (
            "该资料已超过 30 天未复核，办理前建议点击官方入口确认。" if freshness == "stale"
            else ("该资料已超过 90 天未复核，请务必以官方最新页面为准。" if freshness == "expired" else "")
        ),
        "disclaimer": "办事政策可能调整，请以官方最新页面为准。",
    }

    return {
        "decision": "service_card",
        "service_item_code": code,
        "service_item_name": record.get("service_item_name"),
        "guide_record": record,
        "card": card,
        "message": "已为你生成办事指南。",
        "quick_replies": [],
        "progress_events": [
            {"label": "理解诉求", "status": "done", "message": "已识别为政务办事咨询"},
            {"label": "查询已核验指南", "status": "done", "message": "已命中本地官方资料库"},
            {"label": "生成办事卡片", "status": "done", "message": "已生成办事指南"},
        ],
        "sources": sources,
    }


def _build_unsupported(understanding: dict[str, Any]) -> dict[str, Any]:
    """构建 unsupported 响应"""
    return {
        "decision": "unsupported",
        "service_item_code": None,
        "service_item_name": None,
        "guide_record": None,
        "card": None,
        "message": (
            "您好，我不求人是政务办事智能助手，专注于政务服务事项的办事指南查询。"
            "您的问题涉及的范围我暂时无法帮助，建议您通过国家政务服务平台或当地政府部门官方渠道了解。"
        ),
        "quick_replies": [],
        "progress_events": [
            {"label": "理解诉求", "status": "done", "message": "已理解用户问题"},
            {"label": "判断办理事项", "status": "done", "message": "不属于政务办事范围"},
        ],
        "sources": [],
    }


def _build_agent_task_guidance(
    understanding: dict[str, Any],
    task_state: TaskState | None,
    candidates: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """构建任务推进式 guidance 响应"""
    topic = understanding.get("service_goal", "")

    # 如果 task_state 不存在，初始化
    if task_state is None:
        from app.agent.task_state import build_initial_task_state
        task_state = build_initial_task_state(topic, understanding)

    # 根据 stage 构建消息和 quick_replies
    message = build_task_guidance_message(task_state)
    quick_replies = get_quick_replies_for_stage(task_state)

    # 构建用户可见 timeline
    timeline = _build_task_timeline(task_state, understanding)

    return {
        "decision": "agent_task_guidance",
        "service_item_code": task_state.verified_guide_key,
        "service_item_name": None,
        "guide_record": None,
        "task_state": task_state,
        "card": None,
        "message": message,
        "quick_replies": quick_replies,
        "progress_events": timeline,
        "sources": task_state.sources or [],
    }


def _advance_task_decision(
    task_state: TaskState,
    understanding: dict[str, Any],
    candidates: list[dict[str, Any]] | None,
    kb: GuideKnowledgeBase,
) -> dict[str, Any]:
    """
    在任务推进过程中决策。
    如果槽位已全部确认，尝试 semantic_match。
    """
    # 槽位全部确认后，尝试 semantic_match
    if task_state.stage == TaskStage.SEARCH_OFFICIAL:
        if _should_skip_verified_card_search(task_state):
            task_state.verified_guide_status = "not_found"
            task_state.stage = TaskStage.READY_GUIDANCE
            return _build_agent_task_guidance(understanding, task_state, [])

        # 基于确认的 task_state 构造语义查询
        search_query = {
            "service_goal": f"{task_state.topic} {task_state.goal}",
            "action_type": task_state.goal,
            "domain": task_state.domain,
            "city": task_state.city or "",
            "slots": {
                "identity_status": task_state.identity_status or "未知",
                "scenario": task_state.goal,
            },
        }
        records = kb.list_all()
        search_candidates = semantic_match(search_query, records, context={
            "identity_status": task_state.identity_status,
            "city": task_state.city,
            "goal": task_state.goal,
        })

        if search_candidates and search_candidates[0].get("score", 0) >= 0.30:
            top_key = search_candidates[0]["record_key"]
            matched_record = kb.get(top_key)
            if matched_record:
                rec = _normalize_record(matched_record, top_key)
                task_state.verified_guide_key = top_key
                task_state.verified_guide_status = "found"
                task_state.stage = TaskStage.READY_GUIDANCE
                return _build_service_card(top_key, rec, understanding)

        # 未找到，生成临时指引
        task_state.verified_guide_status = "not_found"
        task_state.stage = TaskStage.READY_GUIDANCE
        return _build_agent_task_guidance(understanding, task_state, search_candidates)

    # 其他 stage：继续推进
    return _build_agent_task_guidance(understanding, task_state, candidates)


def _build_task_timeline(task_state: TaskState, understanding: dict[str, Any]) -> list[dict[str, Any]]:
    """构建用户可见的任务推进 timeline"""
    topic = understanding.get("service_goal", task_state.topic or "")
    timeline = []

    timeline.append({
        "label": "理解诉求",
        "status": "done",
        "message": f"已识别为「{topic}」相关办事咨询",
    })

    if task_state.stage == TaskStage.CONFIRM_GOAL:
        timeline.append({
            "label": "建立办事任务",
            "status": "done",
            "message": "需要先确认办理类型",
        })
        timeline.append({
            "label": "下一步",
            "status": "done",
            "message": "请选择你要办理的事项",
        })
    elif task_state.stage == TaskStage.CONFIRM_IDENTITY:
        timeline.append({
            "label": "确认办理类型",
            "status": "done",
            "message": f"已确认：{GOAL_OPTIONS.get(task_state.goal, task_state.goal)}",
        })
        timeline.append({
            "label": "确认身份状态",
            "status": "done",
            "message": "需要先确认你的身份",
        })
    elif task_state.stage == TaskStage.CONFIRM_CITY:
        timeline.append({
            "label": "确认办理类型",
            "status": "done",
            "message": f"已确认：{GOAL_OPTIONS.get(task_state.goal, task_state.goal)}",
        })
        timeline.append({
            "label": "确认身份状态",
            "status": "done",
            "message": f"已确认：{task_state.identity_status or '未知'}",
        })
        timeline.append({
            "label": "确认办理城市",
            "status": "done",
            "message": "需要确认所在城市",
        })
    elif task_state.stage == TaskStage.SEARCH_OFFICIAL:
        timeline.append({
            "label": "确认办理信息",
            "status": "done",
            "message": f"类型={task_state.goal or '?'}，身份={task_state.identity_status or '?'}，城市={task_state.city or '?'}",
        })
        timeline.append({
            "label": "查找官方依据",
            "status": "done",
            "message": "正在查找官方来源……",
        })
    elif task_state.stage == TaskStage.READY_GUIDANCE:
        if task_state.verified_guide_status == "found":
            timeline.append({
                "label": "查找官方依据",
                "status": "done",
                "message": "已找到官方资料，正在生成指南",
            })
        else:
            timeline.append({
                "label": "查找官方依据",
                "status": "warning",
                "message": "本地暂无已核验完整指南",
            })
            timeline.append({
                "label": "生成临时指引",
                "status": "done",
                "message": "以下为方向性建议，不作为最终政策依据",
            })

    return timeline


def _should_skip_verified_card_search(task_state: TaskState) -> bool:
    """Known guidance-only topics should not be coerced into an unrelated card."""
    topic = task_state.topic or ""
    return any(k in topic for k in ["企业年金", "职业年金", "物业", "医保报销"])


# 用于 timeline 显示的 goal → 中文
GOAL_OPTIONS = {
    "query": "查询",
    "claim": "领取",
    "transfer": "转移",
    "complaint": "投诉",
    "appointment": "预约",
    "apply": "申请办理",
    "renew": "续签/续期",
    "withdraw": "提取",
    "consult": "咨询",
    "unknown": "待确认",
}
