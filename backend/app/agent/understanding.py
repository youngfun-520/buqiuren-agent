"""
语义理解层：不依赖关键词/正则硬编码，而使用 LLM 做结构化语义理解。

核心设计：
- understand_user_request: 对用户消息做语义理解，输出结构化意图
- 当 LLM 不可用时，返回低置信度通用结构，不做业务路由
- 不在理解层写任何具体事项的 if/else 路由
"""

from __future__ import annotations

from typing import Any
from app.agent.llm import llm, LLMUnavailable

# 可用于 LLM prompt 的领域列表（仅用于 prompt 构造，不做路由判断）
DOMAIN_HINTS = [
    "深圳", "广州", "上海", "北京", "广东", "全国",
]

ACTION_TYPE_HINTS = [
    "apply", "renew", "withdraw", "query", "complaint",
    "transfer", "appointment", "consult", "cancel", "modify",
]

IDENTITY_STATUS_HINTS = [
    "在职", "离职", "退休", "待业", "学生", "灵活就业",
    "单位在职", "单位离职", "自由职业",
]

SCENARIO_HINTS = [
    "首次办理", "续签", "补办", "换领", "转移", "查询",
    "领取", "提取", "报销", "投诉", "预约", "办理",
]


def _build_understanding_prompt(message: str, context: dict[str, Any] | None = None) -> str:
    """构造 LLM 理解 prompt"""
    ctx_str = ""
    if context:
        ctx_parts = []
        for k, v in (context or {}).items():
            if v and str(v).strip():
                ctx_parts.append(f"{k}: {v}")
        if ctx_parts:
            ctx_str = f"\n已知上下文：{'; '.join(ctx_parts)}"

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y年%m月%d日")
    return f"""你是"不求人"政务办事智能体的语义理解模块。

【重要】当前日期：{now}。你在回复中引用的所有政策、规定、标准、日期等，必须以官方最新发布为准，不得引用已过时（2024年及以前）的政策信息。如果你不确定某项政策是否仍然有效，请在回复中说明"请以官方最新发布为准"。

用户输入："{message}"{ctx_str}

请从语义上理解用户真正想办理/查询/投诉/领取/转移的事项。

输出要求：只返回 JSON，不要有其他文字。
返回字段：
{{
  "is_public_service": true/false,        # 用户是否在询问公共服务/办事/政策类问题
  "domain": "社保/公积金/公安/劳动权益/教育/民政/交通/住房/其他",  # 事项所属领域
  "service_goal": "用户想办的实际事项，简洁描述",   # 如"居住证办理"、"公积金提取"、"工资拖欠投诉"
  "action_type": "apply/renew/withdraw/query/complaint/transfer/appointment/consult/unknown",
  "city": "城市名或未知",
  "slots": {{
    "identity_status": "用户身份状态，如：在职/离职/退休/学生/未知",
    "scenario": "办理场景，如：首次办理/续签/转移/查询/投诉/领取，未知时留空",
    "subtype": "事项子类型，如：租房提取/购房提取/初次领取，未知时留空"
  }},
  "confidence": 0.0到1.0之间的小数,
  "need_clarification": true/false,       # 是否需要追问
  "clarification_question": "如果需要追问，简短问句，否则空字符串",
  "quick_replies": [
    {{"label": "选项显示文字", "value": "选项值", "slot": "对应的槽位名", "context": {{"槽位名": "选项值"}}}},
    ...
  ]
}}

注意：
- 只返回 JSON，不要 markdown 代码块包裹
- is_public_service=false 时其他字段尽量也填，用 unknown 表示
- quick_replies 只在 need_clarification=true 时填写
- 如果用户说的是闲聊、问候、无关内容，is_public_service=false
- 不要编造官方来源或政策细节
- 只描述你从用户输入中直接理解到的内容，不要延伸"""


def _rule_fallback_understanding(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    当 LLM 不可用时，返回低置信度通用结构。
    不做事项路由判断，也不根据词面判断是否属于政务服务。
    """
    text = (message or "").strip()

    return {
        "is_public_service": False,
        "domain": "其他",
        "service_goal": text or "未知",
        "action_type": "unknown",
        "city": "未知",
        "slots": {"identity_status": "未知", "scenario": "", "subtype": ""},
        "confidence": 0.0,
        "need_clarification": False,
        "clarification_question": "",
        "quick_replies": [],
    }


def understand_user_request(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    主入口：对用户消息做语义理解，输出结构化意图。

    优先使用 LLM 结构化输出；LLM 不可用时用规则兜底。
    不在理解层做具体事项的路由判断。
    """
    prompt = _build_understanding_prompt(message, context)

    try:
        result = llm.invoke_json(prompt, _rule_fallback_understanding(message, context))
        # 验证关键字段存在
        if isinstance(result, dict) and "is_public_service" in result:
            return result
    except (LLMUnavailable, Exception):
        pass

    # LLM 不可用时的兜底
    return _rule_fallback_understanding(message, context)


def stream_understand_user_request(message: str, context: dict[str, Any] | None = None):
    """
    流式版本：对用户消息做语义理解，实时产出 thinking 步骤。
    生成器（generator）：每次 LLM 输出一个 thinking 片段时 yield 一步思考内容。
    返回结构化理解结果。
    """
    prompt = _build_understanding_prompt(message, context)
    collected_thinking = []

    def on_thinking(thinking: str):
        """每个 thinking 片段立即 yield"""
        collected_thinking.append(thinking)
        yield thinking

    # 实际上 on_thinking 被调用时直接 yield 出去
    # 但因为 on_thinking 是普通函数，我们改用生成器模式收集
    collected_thinking = []

    def collect_thinking(thinking: str):
        collected_thinking.append(thinking)

    try:
        result = llm.stream思考(prompt, collect_thinking)
        # result 是最终的 JSON 结构
        # 现在把收集到的 thinking 内容 yield 出去
        for step in collected_thinking:
            yield step
        return result
    except (LLMUnavailable, Exception):
        pass

    # LLM 不可用时的兜底
    return _rule_fallback_understanding(message, context)
