"""
任务状态管理器：为每个会话维护一个逐步推进的办事任务状态。
不是每轮从零回答，而是根据 task_state 推进任务。
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from enum import Enum


class TaskStage(str):
    # 初始/确认目标
    CONFIRM_GOAL = "confirm_goal"
    # 确认身份状态
    CONFIRM_IDENTITY = "confirm_identity"
    # 确认城市
    CONFIRM_CITY = "confirm_city"
    # 确认具体子项
    CONFIRM_SUBITEM = "confirm_subitem"
    # 搜索官方来源
    SEARCH_OFFICIAL = "search_official"
    # 生成临时指引
    READY_GUIDANCE = "ready_guidance"
    # 完成
    DONE = "done"
    # 已知无法办理
    UNSUPPORTED = "unsupported"


@dataclass
class TaskState:
    """
    会话级任务状态。
    在多轮对话中逐步推进，直到生成 service_card 或确认无法办理。
    """
    topic: str = ""                    # 用户事项主题，如"企业年金"、"工资拖欠"
    domain: str = "其他"              # 领域
    goal: str = "unknown"              # apply/renew/withdraw/query/claim/transfer/complaint/appointment/consult/unknown
    city: str | None = None            # 城市
    identity_status: str | None = None # 在职/离职/退休/学生/未知
    subitem: str | None = None         # 子项，如"首次办理"、"租房提取"
    confirmed: dict[str, str] = field(default_factory=dict)  # 已确认的槽位
    missing_slots: list[str] = field(default_factory=list)   # 仍缺失的槽位
    verified_guide_key: str | None = None  # 命中的 KB record key
    verified_guide_status: str = "not_found"  # not_found/found/searching/unverified
    stage: TaskStage = TaskStage.CONFIRM_GOAL
    sources: list[dict] = field(default_factory=list)  # 找到的官方来源

    # 引导消息（用于 guidance_fallback / agent_task_guidance）
    guidance_message: str = ""
    quick_replies: list[dict] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """序列化为 payload 中的 task_state 字段"""
        return {
            "topic": self.topic,
            "domain": self.domain,
            "goal": self.goal,
            "city": self.city,
            "identity_status": self.identity_status,
            "subitem": self.subitem,
            "confirmed": self.confirmed,
            "missing_slots": self.missing_slots,
            "verified_guide_key": self.verified_guide_key,
            "verified_guide_status": self.verified_guide_status,
            "stage": self.stage.value if isinstance(self.stage, TaskStage) else self.stage,
            "sources": self.sources,
        }


# =============================================================================
# 任务推进引擎
# =============================================================================

# 各 stage 对应的用户可见标题
STAGE_DISPLAY = {
    TaskStage.CONFIRM_GOAL: "确认办理类型",
    TaskStage.CONFIRM_IDENTITY: "确认身份状态",
    TaskStage.CONFIRM_CITY: "确认办理城市",
    TaskStage.CONFIRM_SUBITEM: "确认具体事项",
    TaskStage.SEARCH_OFFICIAL: "查找官方依据",
    TaskStage.READY_GUIDANCE: "生成办事指引",
    TaskStage.DONE: "完成",
    TaskStage.UNSUPPORTED: "无法办理",
}

# 常见 goal 对应的选项
GOAL_OPTIONS = {
    "query": "查询账户",
    "claim": "领取企业年金",
    "transfer": "转移",
    "complaint": "投诉",
    "appointment": "预约",
    "apply": "申请办理",
    "renew": "续签/续期",
    "withdraw": "提取",
}

# goal → 下一 stage 需要的槽位
GOAL_NEXT_SLOTS = {
    "query": ["identity_status", "city"],
    "claim": ["identity_status", "city"],
    "transfer": ["identity_status", "city"],
    "complaint": ["city"],
    "appointment": ["city"],
    "apply": ["city"],
    "renew": ["city"],
    "withdraw": ["city"],
}


def build_initial_task_state(message: str, understanding: dict[str, Any]) -> TaskState:
    """基于理解结果初始化任务状态"""
    topic = understanding.get("service_goal", message)
    domain = understanding.get("domain", "其他")
    goal = understanding.get("action_type", "unknown")

    state = TaskState(
        topic=topic,
        domain=domain,
        goal=goal,
        missing_slots=["goal"] if goal == "unknown" else [],
        stage=TaskStage.CONFIRM_GOAL,
    )

    # 如果 goal 已知，直接进入下一阶段
    if goal != "unknown" and goal in GOAL_NEXT_SLOTS:
        state.missing_slots = GOAL_NEXT_SLOTS.get(goal, [])
        _advance_to_next_slot(state)

    return state


def advance_task_state(state: TaskState, reply: str, reply_context: dict[str, Any] | None = None) -> TaskState:
    """
    根据用户回复推进任务状态。
    reply_context 是从 quick_reply 的 context 字段提取的。
    """
    ctx = reply_context or {}
    reply = (reply or "").strip()

    if state.stage == TaskStage.CONFIRM_GOAL:
        # 从 reply 推断 goal
        inferred_goal = _infer_goal_from_reply(reply)
        context_goal = ctx.get("fallback_action")
        selected_goal = context_goal if context_goal in GOAL_NEXT_SLOTS else inferred_goal
        if selected_goal and selected_goal != "unknown":
            state.goal = selected_goal
            state.confirmed["goal"] = selected_goal
            # 从 reply context 提取
            # 移除已确认槽位
            if "goal" in state.missing_slots:
                state.missing_slots.remove("goal")
            state.missing_slots = list(GOAL_NEXT_SLOTS.get(state.goal, []))
            # 推进到下一槽位
            _advance_to_next_slot(state)
            return state
        else:
            # 仍需确认
            state.goal = "unknown"
            state.missing_slots = ["goal"]
            return state

    if state.stage == TaskStage.CONFIRM_IDENTITY:
        inferred = ctx.get("identity_status") or _infer_identity_from_reply(reply)
        if inferred:
            state.identity_status = inferred
            state.confirmed["identity_status"] = inferred
            if "identity_status" in state.missing_slots:
                state.missing_slots.remove("identity_status")
            _advance_to_next_slot(state)
        return state

    if state.stage == TaskStage.CONFIRM_CITY:
        inferred = ctx.get("city") or _infer_city_from_reply(reply)
        if inferred:
            state.city = inferred
            state.confirmed["city"] = inferred
            if "city" in state.missing_slots:
                state.missing_slots.remove("city")
            _advance_to_next_slot(state)
        return state

    if state.stage == TaskStage.CONFIRM_SUBITEM:
        state.subitem = reply
        state.confirmed["subitem"] = reply
        if "subitem" in state.missing_slots:
            state.missing_slots.remove("subitem")
        _advance_to_next_slot(state)
        return state

    return state


def _advance_to_next_slot(state: TaskState) -> None:
    """根据已确认槽位推进 stage"""
    if state.missing_slots:
        next_needed = state.missing_slots[0]
        if next_needed == "identity_status":
            state.stage = TaskStage.CONFIRM_IDENTITY
        elif next_needed == "city":
            state.stage = TaskStage.CONFIRM_CITY
        elif next_needed == "subitem":
            state.stage = TaskStage.CONFIRM_SUBITEM
    else:
        # 所有槽位已确认
        state.stage = TaskStage.SEARCH_OFFICIAL


def _llm_infer_slots(reply: str, topic: str) -> dict[str, str | None]:
    """Use LLM to infer goal, identity_status, and city from user reply."""
    from app.agent.llm import llm, LLMUnavailable
    prompt = f"""你是不求人政务助手的信息提取模块。
用户原始事项：{topic}
用户最新回复：{reply}

从回复中提取以下信息，JSON 格式：
{{
  "goal": "用户想办理的具体操作类型",
  "identity_status": "用户身份状态，推断不出来则null",
  "city": "城市名，推断不出来则null"
}}

goal 取以下值之一: query(查询账户), claim(领取), transfer(转移), complaint(投诉), appointment(预约), apply(申请), renew(续签), withdraw(提取), consult(咨询), unknown(无法判断)
identity_status 取以下值之一: 在职, 已离职, 已退休, 学生, null(无法判断)
city 取城市名或 null

只返回 JSON，不要其他文字。"""
    try:
        result = llm.invoke_json(prompt, {"goal": "unknown", "identity_status": None, "city": None})
        if isinstance(result, dict):
            return result
    except (LLMUnavailable, Exception):
        pass
    return {"goal": "unknown", "identity_status": None, "city": None}


def _infer_goal_from_reply(reply: str) -> str | None:
    """从用户回复推断 goal，使用 LLM"""
    slots = _llm_infer_slots(reply, "")
    g = slots.get("goal", "unknown")
    return g if g != "unknown" else None


def _infer_identity_from_reply(reply: str) -> str | None:
    """从用户回复推断身份状态，使用 LLM"""
    slots = _llm_infer_slots(reply, "")
    return slots.get("identity_status")


def _infer_city_from_reply(reply: str) -> str | None:
    """从用户回复推断城市，使用 LLM"""
    slots = _llm_infer_slots(reply, "")
    return slots.get("city")


def get_quick_replies_for_stage(state: TaskState) -> list[dict[str, Any]]:
    """根据当前 stage 生成 quick_replies"""
    if state.stage == TaskStage.CONFIRM_GOAL:
        options = []
        # 基于 topic 和 domain 推断可用选项
        topic = state.topic or ""
        domain = state.domain or ""

        if any(k in topic for k in ["企业年金", "职业年金", "年金"]):
            options = [
                {"label": "查询账户", "value": "查询账户", "slot": "fallback_action", "context": {"fallback_action": "query"}},
                {"label": "领取企业年金", "value": "领取企业年金", "slot": "fallback_action", "context": {"fallback_action": "claim"}},
                {"label": "企业年金转移", "value": "企业年金转移", "slot": "fallback_action", "context": {"fallback_action": "transfer"}},
                {"label": "投诉或咨询", "value": "投诉或咨询", "slot": "fallback_action", "context": {"fallback_action": "complaint"}},
                {"label": "我不确定", "value": "不确定", "slot": "fallback_action", "context": {"fallback_action": "unknown"}},
            ]
        elif any(k in topic for k in ["生育津贴", "生育保险"]):
            options = [
                {"label": "查询生育津贴", "value": "查询", "slot": "fallback_action", "context": {"fallback_action": "query"}},
                {"label": "领取生育津贴", "value": "领取", "slot": "fallback_action", "context": {"fallback_action": "claim"}},
                {"label": "生育津贴计算", "value": "计算", "slot": "fallback_action", "context": {"fallback_action": "consult"}},
                {"label": "我不确定", "value": "不确定", "slot": "fallback_action", "context": {"fallback_action": "unknown"}},
            ]
        elif any(k in topic for k in ["医保", "医疗报销"]):
            options = [
                {"label": "查询医保账户", "value": "查询", "slot": "fallback_action", "context": {"fallback_action": "query"}},
                {"label": "医保报销流程", "value": "报销", "slot": "fallback_action", "context": {"fallback_action": "apply"}},
                {"label": "领取医保卡", "value": "领取", "slot": "fallback_action", "context": {"fallback_action": "claim"}},
                {"label": "我不确定", "value": "不确定", "slot": "fallback_action", "context": {"fallback_action": "unknown"}},
            ]
        elif any(k in topic for k in ["社保", "养老金", "退休金"]):
            options = [
                {"label": "查询社保账户", "value": "查询", "slot": "fallback_action", "context": {"fallback_action": "query"}},
                {"label": "社保转移", "value": "转移", "slot": "fallback_action", "context": {"fallback_action": "transfer"}},
                {"label": "办理退休", "value": "退休", "slot": "fallback_action", "context": {"fallback_action": "apply"}},
                {"label": "我不确定", "value": "不确定", "slot": "fallback_action", "context": {"fallback_action": "unknown"}},
            ]
        elif any(k in topic for k in ["物业", "小区", "投诉"]):
            options = [
                {"label": "物业投诉", "value": "物业投诉", "slot": "fallback_action", "context": {"fallback_action": "complaint"}},
                {"label": "物业咨询", "value": "咨询", "slot": "fallback_action", "context": {"fallback_action": "consult"}},
                {"label": "其他", "value": "其他", "slot": "fallback_action", "context": {"fallback_action": "unknown"}},
            ]
        else:
            options = [
                {"label": "查询办理条件", "value": "查询", "slot": "fallback_action", "context": {"fallback_action": "query"}},
                {"label": "了解所需材料", "value": "材料", "slot": "fallback_action", "context": {"fallback_action": "consult"}},
                {"label": "在线办理入口", "value": "办理", "slot": "fallback_action", "context": {"fallback_action": "apply"}},
                {"label": "投诉", "value": "投诉", "slot": "fallback_action", "context": {"fallback_action": "complaint"}},
            ]

        return options if options else []

    if state.stage == TaskStage.CONFIRM_IDENTITY:
        if _is_enterprise_annuity(state):
            return [
                {"label": "已退休", "value": "已退休", "slot": "identity_status", "context": {"identity_status": "已退休"}},
                {"label": "已离职", "value": "已离职", "slot": "identity_status", "context": {"identity_status": "已离职"}},
                {"label": "仍在职", "value": "仍在职", "slot": "identity_status", "context": {"identity_status": "在职"}},
                {"label": "不确定", "value": "不确定", "slot": "identity_status", "context": {"identity_status": "不确定"}},
            ]
        return [
            {"label": "已退休", "value": "已退休", "slot": "identity_status", "context": {"identity_status": "已退休"}},
            {"label": "已离职", "value": "已离职", "slot": "identity_status", "context": {"identity_status": "已离职"}},
            {"label": "仍在职", "value": "在职", "slot": "identity_status", "context": {"identity_status": "在职"}},
            {"label": "学生", "value": "学生", "slot": "identity_status", "context": {"identity_status": "学生"}},
            {"label": "返回上一步", "value": "返回", "slot": "back", "context": {"back": "confirm_goal"}},
        ]

    if state.stage == TaskStage.CONFIRM_CITY:
        return [
            {"label": "北京", "value": "北京", "slot": "city", "context": {"city": "北京"}},
            {"label": "上海", "value": "上海", "slot": "city", "context": {"city": "上海"}},
            {"label": "广州", "value": "广州", "slot": "city", "context": {"city": "广州"}},
            {"label": "成都", "value": "成都", "slot": "city", "context": {"city": "成都"}},
            {"label": "杭州", "value": "杭州", "slot": "city", "context": {"city": "杭州"}},
            {"label": "西安", "value": "西安", "slot": "city", "context": {"city": "西安"}},
            {"label": "返回上一步", "value": "返回", "slot": "back", "context": {"back": "confirm_identity"}},
        ]

    if state.stage == TaskStage.CONFIRM_SUBITEM:
        return [
            {"label": "返回上一步", "value": "返回", "slot": "back", "context": {"back": "confirm_goal"}},
        ]

    return []


def build_task_guidance_message(state: TaskState) -> str:
    """根据当前 task_state 构建推进式消息"""
    topic = state.topic or "该事项"
    goal_display = GOAL_OPTIONS.get(state.goal, state.goal) if state.goal != "unknown" else ""

    if state.stage == TaskStage.CONFIRM_GOAL:
        if _is_enterprise_annuity(state):
            return (
                "我先把「企业年金」作为一个办事任务帮你推进。当前没有已核验的完整官方办事卡片，"
                "我不会编造材料清单。请先确认你要办哪一类。"
            )
        return (
            f"我先把这件事作为一个办事任务帮你推进。你问的是「{topic}」相关事项，"
            f"目前本地没有已核验的完整官方办事卡片，我们先确认你要办的是哪一类。"
        )

    if state.stage == TaskStage.CONFIRM_IDENTITY:
        goal_str = f"「{goal_display}」" if goal_display else ""
        return (
            f"好的，你是要{goal_str}。{goal_str}场景通常需要先确认你的身份状态，"
            f"因为退休、离职、在职对应路径可能不同。你现在属于哪种情况？"
        )

    if state.stage == TaskStage.CONFIRM_CITY:
        if "物业" in topic:
            return (
                "可以，我先按「物业投诉」帮你推进。请补充：所在城市或小区所在地、投诉对象"
                "（物业公司/业委会/开发商等）、投诉类型（收费、维修、服务、公共收益等），"
                "以及目前已有的证据（合同、缴费记录、照片、沟通记录等）。先从城市开始确认。"
            )
        if "医保报销" in topic or "医保" in topic:
            return (
                "可以，我先按「医保报销」帮你推进。请补充：医保参保城市、参保类型"
                "（职工医保/居民医保等）、报销类型（门诊、住院、异地就医、生育或特殊病种等），"
                "这样才能继续判断入口和所需核验信息。先从医保城市开始确认。"
            )
        identity_str = f"你是「{state.identity_status}」，" if state.identity_status else ""
        return (
            f"收到，{identity_str}下一步需要确认所在城市或单位所在地，"
            f"因为不同地区和政策入口可能不同。你在哪个城市办理？"
        )

    if state.stage == TaskStage.SEARCH_OFFICIAL:
        identity_str = f"，身份「{state.identity_status}」" if state.identity_status else ""
        city_str = f"在「{state.city}」" if state.city else ""
        return (
            f"好的，信息已收齐。正在根据你提供的信息{city_str}{identity_str}查找「{topic}」相关官方依据……"
        )

    if state.stage == TaskStage.READY_GUIDANCE:
        if _is_enterprise_annuity(state):
            city = state.city or "当前城市"
            goal = GOAL_OPTIONS.get(state.goal, state.goal)
            identity = state.identity_status or "身份状态未确认"
            return (
                f"已记录：事项「企业年金」，办理类型「{goal}」，身份「{identity}」，城市「{city}」。"
                "当前未匹配到可核验官方完整指南，以下为方向性推进，不作为最终政策依据。"
                "下一步建议：1. 咨询原单位人事或企业年金经办机构，确认领取/查询/转移的具体经办路径；"
                "2. 查询个人企业年金账户管理机构或受托管理机构，核对账户状态；"
                "3. 关注国家政务服务平台、当地人社部门和当地政务服务网等官方渠道的最新说明；"
                "4. 可先准备身份信息、退休或离职状态证明、原单位信息、单位年金账户或个人账户信息等可能需要的信息，"
                "但这些不是最终材料清单，办理前请以官方或经办机构要求为准。"
            )
        return f"已生成「{topic}」的办事方向指引，请查收。"

    if state.stage == TaskStage.UNSUPPORTED:
        return f"「{topic}」不在我能帮助办理的政务服务范围内，建议通过官方渠道了解。"

    return f"正在处理「{topic}」相关请求……"


def _is_enterprise_annuity(state: TaskState) -> bool:
    topic = state.topic or ""
    return any(k in topic for k in ["企业年金", "职业年金", "年金"])
