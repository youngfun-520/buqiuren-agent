"""LLM-only fallback guidance for items that are not in the verified local KB."""

from __future__ import annotations

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


def _unique_context_terms(*sources: dict[str, Any] | None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if not value or str(key).startswith("_"):
                continue
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            terms.append(text)
    return terms


def _known_context_summary(user_context: dict[str, Any], user_slots: dict[str, Any]) -> str:
    terms = _unique_context_terms(user_context, user_slots)
    return "；".join(terms[:6])


def _trim_repeated_known_terms(message: str, terms: list[str]) -> str:
    text = (message or "").strip()
    if not text:
        return text
    for term in sorted({term.strip() for term in terms if term and str(term).strip()}, key=len, reverse=True):
        first = text.find(term)
        if first == -1:
            continue
        prefix = text[: first + len(term)]
        suffix = text[first + len(term) :].replace(term, "")
        text = prefix + suffix
    return " ".join(text.split())


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
    known_context_terms = _unique_context_terms(user_context, user_slots)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y年%m月%d日")
    known_context_summary = _known_context_summary(user_context, user_slots)
    prompt = f"""你是"不求人"政务办事智能助手。
【重要】当前日期：{now}。你在回复中引用的所有政策、规定、标准、日期等，必须以官方最新发布（2025-2026年）为准，不得引用已过时的政策信息。如果你不确定某项政策是否仍然有效，请说明"请以官方最新发布为准"。

用户提问：{message}
语义理解：{state.get("understanding") or {}}
候选匹配：{state.get("semantic_candidates") or []}
已知用户补充信息（不要重复这些已知信息）：{known_context_summary}

当前没有命中本地已核验的完整官方办事卡片。请生成一个方向性办事引导。

要求：
1. 明确说明"以下为方向性分析，不作为最终政策依据"
2. 结合"已知用户补充信息"继续推进，不要重复追问已明确的信息
3. 只说明仍缺少哪些关键信息，避免编造材料清单和具体政策条款
4. 不要重复追问已明确的信息，只追问下一项缺口；如果用户已经回答了参保身份、户籍、城市、办理类型等信息，直接推进到下一项缺失信息的确认
5. 如果用户提问中已经出现地名（例如"吉林"、"辽源"、"北京"），不得说缺少城市；如地名可能同时表示省和市，只说明需要确认省级/市级办理范围
6. 公积金缴纳、缴存、提取、查询都属于公共服务/民生办事咨询，不要判成闲聊或无关问题
7. 身份状态（identity_status）仅在与用户本人就业/社保参保直接相关的事项（如社保参保、公积金提取）时才追问；对于新生儿医保、少儿医保等以子女为参保主体的业务，不应追问用户本人的身份状态，不得无依据假设用户身份为"在职"
8. 给出 2-4 个可点击的下一步选项；选项数量、内容、slot 名和 context 由你根据事项与已知信息决定
9. 只返回 JSON：
{{
  "service_item_name": "事项名",
  "message": "给用户看的中文引导，200字以内",
  "quick_replies": [
    {{"label": "选项显示文字", "value": "选项值", "slot": "语义化槽位名", "context": {{"语义化槽位名": "选项值"}}}}
  ],
  "reasoning_summary": "不超过30字，说明为什么进入方向性引导"
}}"""

    # 不要在默认消息中重复所有已知信息，只说正在分析方向
    default_message = (
        "以下为方向性分析，不作为最终政策依据。"
        "正在为你梳理办理方向，请提供更多关键信息以便精准匹配指南。"
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
    message_text = _trim_repeated_known_terms(
        result.get("message") or default_message,
        known_context_terms,
    )

    progress_events = [
        {"node": "intelligent_fallback", "status": "done", "message": "已由模型生成方向性分析"},
        {"node": "local_kb_lookup", "status": "warning", "message": "本地已验证事项库未命中"},
    ]

    return {
        "answer_type": "guidance_fallback",
        "message": message_text,
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
