"""LLM-only fallback guidance for items that are not in the verified local KB."""

from __future__ import annotations

import json
from typing import Any

from app.agent.llm import LLMUnavailable, llm


PUBLIC_SERVICE_SUBJECTS = (
    "公积金", "住房公积金", "社保", "社会保险", "医保", "医疗保险", "养老保险",
    "居住证", "身份证", "护照", "户口", "户籍", "驾驶证", "营业执照",
    "退休", "失业", "工伤", "生育津贴", "工资拖欠", "劳动仲裁",
    "结婚登记", "学位", "入学", "租赁备案",
)

PUBLIC_SERVICE_ACTIONS = (
    "怎么办", "怎么交", "怎么缴", "缴纳", "缴存", "提取", "办理", "申请",
    "申领", "续签", "查询", "转移", "报销", "投诉", "举报", "开户",
    "登记", "预约", "材料", "条件", "流程", "入口",
)


def _has_public_service_scope_signal(message: str) -> bool:
    """
    Conservative routing guard only.
    It prevents obvious public-service questions from being dropped as chat,
    but never decides the final answer content or user-visible options.
    """
    text = (message or "").strip()
    if not text:
        return False
    has_subject = any(term in text for term in PUBLIC_SERVICE_SUBJECTS)
    has_action = any(term in text for term in PUBLIC_SERVICE_ACTIONS)
    return has_subject and has_action


def is_public_service_query(message: str) -> bool | None:
    """Classify scope with LLM only; return None when the model is unavailable."""
    scope_guard = _has_public_service_scope_signal(message)
    if scope_guard:
        return True
    prompt = f"""你是不求人政务助手的范围判断模块。
用户输入：{message}

请判断用户是否在询问公共服务、政务办事、民生政策、投诉维权或官方办理事项。
住房公积金缴纳、缴存、提取、查询，社保缴费、参保、转移、查询，医保报销、参保、查询等必须判定为 true。
只返回 JSON：
{{"is_public_service": true/false, "reason": "不超过15字"}}"""
    try:
        result = llm.invoke_json(prompt, {"is_public_service": None, "reason": "模型不可用"})
        value = result.get("is_public_service")
        return value if isinstance(value, bool) else None
    except (LLMUnavailable, Exception):
        return None


def build_intelligent_fallback(message: str, state: dict[str, Any]) -> dict[str, Any]:
    """Build guidance with LLM only; do not route with keywords or patterns."""
    user_context = {
        k: v
        for k, v in (state.get("user_context") or {}).items()
        if v and not str(k).startswith("_")
    }
    user_slots = {
        k: v
        for k, v in (state.get("user_slots") or {}).items()
        if v and not str(k).startswith("_")
    }
    known_info = {"user_context": user_context, "user_slots": user_slots}
    prompt = f"""你是"不求人"政务办事智能助手。
用户提问：{message}
语义理解：{state.get("understanding") or {}}
候选匹配：{state.get("semantic_candidates") or []}
已知用户补充信息：{json.dumps(known_info, ensure_ascii=False)}

当前没有命中本地已核验的完整官方办事卡片。请生成一个方向性办事引导。

要求：
1. 明确说明"以下为方向性分析，不作为最终政策依据"
2. 结合"已知用户补充信息"继续推进，不要重复追问已明确的信息
3. 只说明仍缺少哪些关键信息，避免编造材料清单和具体政策条款
4. 如果用户已经回答了参保身份、户籍、城市、办理类型等信息，回复中要体现"已记录/已知"，并只追问下一项缺口
5. 如果用户提问中已经出现地名（例如"吉林"、"辽源"、"北京"），不得说缺少城市；如地名可能同时表示省和市，只说明需要确认省级/市级办理范围
6. 公积金缴纳、缴存、提取、查询都属于公共服务/民生办事咨询，不要判成闲聊或无关问题
7. 给出 2-4 个可点击的下一步选项；选项数量、内容、slot 名和 context 由你根据事项与已知信息决定
8. 只返回 JSON：
{{
  "service_item_name": "事项名",
  "message": "给用户看的中文引导，200字以内",
  "quick_replies": [
    {{"label": "选项显示文字", "value": "选项值", "slot": "语义化槽位名", "context": {{"语义化槽位名": "选项值"}}}}
  ],
  "reasoning_summary": "不超过30字，说明为什么进入方向性引导"
}}"""

    context_text = "；".join(str(v) for v in [*user_context.values(), *user_slots.values()] if str(v).strip())
    default_message = (
        "以下为方向性分析，不作为最终政策依据。当前没有命中本地已核验的完整官方办事卡片，"
        + (f"已记录你补充的信息：{context_text}。" if context_text else "")
        + "我不会编造材料清单。请继续补充仍未明确的缴费类型、参保身份或办理场景，我会继续帮你核验官方依据。"
    )
    try:
        result = llm.invoke_json(
            prompt,
            {
                "service_item_name": state.get("service_item_name") or "该办事事项",
                "message": default_message,
                "quick_replies": [],
                "reasoning_summary": "本地知识库未命中",
            },
        )
    except (LLMUnavailable, Exception):
        result = {
            "service_item_name": state.get("service_item_name") or "该办事事项",
            "message": default_message,
            "quick_replies": [],
            "reasoning_summary": "模型不可用，安全降级",
        }

    progress_events = [
        {"node": "intelligent_fallback", "status": "done", "message": "已由模型生成方向性分析"},
        {"node": "local_kb_lookup", "status": "warning", "message": "本地已验证事项库未命中"},
    ]

    return {
        "answer_type": "guidance_fallback",
        "message": result.get("message") or default_message,
        "missing_slots": [],
        "progress_events": progress_events,
        "quick_replies_raw": result.get("quick_replies") or [],
        "service_item_code": state.get("service_item_code") or "unknown",
        "service_item_name": result.get("service_item_name") or state.get("service_item_name") or "该办事事项",
        "reasoning_steps": [
            {
                "label": "方向性引导",
                "summary": result.get("reasoning_summary") or "本地知识库未命中",
            }
        ],
    }
