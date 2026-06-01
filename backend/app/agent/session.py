"""
会话管理。
session.ask() 是智能体的入口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.agent.workflow import run_buqiuren, run_buqiuren_stream

# 旧版 workflow 节点 → 用户可见标签
PROGRESS_LABELS = {
    # 新节点名（新会话优先匹配）
    "understand_user":"理解你的问题",
    "smart_match":"匹配办理事项",
    "check_integrity":"确认信息完整性",
    "retrieve_guide":"搜索官方指南",
    "build_response":"生成办事指南",
    # 保留旧节点标签用于向后兼容（已有会话历史）
    "understand_user_query":"理解你的问题",
    "classify_life_event":"识别办理事项",
    "semantic_match":"查询已核验指南",
    "check_missing_info":"确认关键信息",
    "ask_clarification":"生成追问",
    "generate_service_card":"生成办事卡片",
    "quality_check":"完成质量检查",
    "safe_degrade":"安全降级",
    "intelligent_fallback":"分析办理方向",
    "fallback_guidance":"生成方向性指引",
    "search_official_sources":"搜索官方资料",
    "fetch_official_pages":"抓取官方页面",
    "extract_guide_from_pages":"整理办事指南",
    "retrieve_service_guide":"保存官方指南",
    "local_kb_lookup":"查询本地事项库",
    "match_service_item":"匹配具体事项",
}

def _as_list(value: Any) -> list[Any]: return value if isinstance(value, list) else ([] if not value else [value])
def _clean_text(value: Any) -> str:
    if value is None: return ""
    if isinstance(value, (dict, list)): return json.dumps(value, ensure_ascii=False)
    return str(value)


def _user_friendly_timeline(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """将开发节点名转换为用户可见标签"""
    result = []
    seen_keys = set()
    for e in (events or []):
        key = (e.get("node") or e.get("label") or "", e.get("message") or "")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        node = e.get("node") or ""
        label = PROGRESS_LABELS.get(node, e.get("label") or node or "处理步骤")
        result.append({
            "label": label,
            "status": e.get("status", "done"),
            "message": e.get("message") or "",
        })
    return result


def build_quick_replies(response: dict[str, Any]) -> list[dict[str, Any]]:
    answer_type = response.get("answer_type") or ""
    if answer_type in ("clarification_required", "clarification"):
        replies: list[dict[str, Any]] = []
        for spec in response.get("missing_slots") or []:
            if not isinstance(spec, dict):
                continue
            slot = spec.get("slot")
            # 优先用 LLM 生成的 options
            llm_opts = spec.get("options")
            if llm_opts and isinstance(llm_opts, list):
                for opt in llm_opts:
                    label = opt.get("label", opt.get("value", ""))
                    value = opt.get("value", label)
                    if label or value:
                        replies.append({"label": label or value, "value": value or label, "slot": slot, "context": {slot: value or label}})
        return replies
    if answer_type in ("guidance_fallback", "agent_task_guidance"):
        return response.get("quick_replies_raw") or response.get("quick_replies") or []
    return []


def build_progress_timeline(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """构建用户可见 timeline"""
    return _user_friendly_timeline(events)


def build_reasoning_steps(response: dict[str, Any]) -> list[dict[str, str]]:
    """Build safe, user-visible reasoning summaries without exposing raw model thoughts."""
    explicit = response.get("reasoning_steps")
    if isinstance(explicit, list):
        return [
            {"label": _clean_text(x.get("label") or "思考摘要"), "summary": _clean_text(x.get("summary") or "")}
            for x in explicit
            if isinstance(x, dict) and (x.get("label") or x.get("summary"))
        ]

    steps = []
    for event in response.get("progress_events") or []:
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if not data:
            continue
        label = PROGRESS_LABELS.get(event.get("node") or "", event.get("label") or "思考摘要")
        summary = event.get("message") or ""
        if isinstance(data, dict):
            compact = []
            for key in ("understanding", "score", "u_confidence", "top_score"):
                if key in data:
                    compact.append(f"{key}={data[key]}")
            if compact:
                summary = f"{summary}（{'; '.join(compact)}）"
        steps.append({"label": label, "summary": summary})
    return steps


def normalize_sources(sources: Any) -> list[dict[str, str]]:
    out = []
    for s in _as_list(sources):
        if isinstance(s, dict):
            url = _clean_text(s.get("url") or s.get("source_url")).strip()
            title = _clean_text(s.get("title") or s.get("name") or s.get("source_name") or "官方来源")
            out.append({"title": title or "官方来源", "name": title or "官方来源", "url": url})
        elif s:
            out.append({"title": _clean_text(s), "name": _clean_text(s), "url": ""})
    return out


def build_materials_clipboard_text(card: dict[str, Any]) -> str:
    materials = _as_list(card.get("materials"))
    if not materials: return "当前卡片暂未提取到材料清单，请以官方页面为准。"
    return "\n".join([f"{card.get('title', '办事指南')} - 材料清单", *[f"{i}. {_clean_text(x)}" for i, x in enumerate(materials, 1)], "", "提示：材料要求可能调整，提交前请以官方最新页面为准。"])


def build_frontend_payload(state_or_response: dict[str, Any], task_state: Any = None) -> dict[str, Any]:
    """
    构建统一 frontend payload。
    支持：
    - service_card
    - clarification_required
    - agent_task_guidance / guidance_fallback
    - no_verified_guide
    - unsupported
    - error
    """
    response = state_or_response.get("final_response", state_or_response) or {}

    answer_type = response.get("answer_type") or response.get("type") or "unknown"
    card = None
    sources = []
    actions = []

    if answer_type == "service_card":
        card = response.get("card")
        if card:
            sources = normalize_sources(card.get("sources") or [])
            online_entry = _as_list(card.get("online_entry")); official_url = ""
            if online_entry and isinstance(online_entry[0], dict): official_url = online_entry[0].get("url") or ""
            if not official_url and sources: official_url = sources[0].get("url") or ""
            actions = [
                {"type":"copy_materials", "label":"复制材料清单", "text": build_materials_clipboard_text(card)},
                {"type":"open_official", "label":"打开官方入口" if official_url else "查看官方来源", "url": official_url},
                {"type":"restart_scenario", "label":"重新选择办理场景"},
                {"type":"follow_up", "label":"继续追问"},
            ]

    elif answer_type in ("agent_task_guidance", "guidance_fallback"):
        sources = normalize_sources(response.get("sources") or [])

    elif answer_type == "clarification_required":
        sources = normalize_sources(response.get("sources") or [])

    elif answer_type == "no_verified_guide":
        sources = normalize_sources(response.get("sources") or [])

    timeline = build_progress_timeline(response.get("progress_events") or [])

    payload = {
        "type": answer_type,
        "message": response.get("message") or "",
        "timeline": timeline,
        "quick_replies": build_quick_replies(response),
        "card": card,
        "actions": actions,
        "sources": sources,
        "reasoning_steps": build_reasoning_steps(response),
    }

    # task_state 透传
    if task_state:
        payload["task_state"] = task_state.to_payload()
    elif response.get("task_state"):
        payload["task_state"] = response.get("task_state")

    return payload


def _find_quick_reply(response: dict[str, Any], label_or_value: str) -> dict[str, Any] | None:
    """从 quick_replies 中查找匹配的选项"""
    for x in build_quick_replies(response or {}):
        if label_or_value in {x.get("label"), x.get("value")}: return x
    return None


def _response_type(state: dict[str, Any] | None) -> str:
    return ((state or {}).get("final_response") or {}).get("answer_type") or ""


INTERACTIVE_RESPONSE_TYPES = {
    "clarification_required",
    "clarification",
    "guidance_fallback",
    "agent_task_guidance",
}


def _safe_no_verified_state(base: dict[str, Any], message: str) -> dict[str, Any]:
    progress = list(base.get("progress_events") or []) + [{"node":"safe_degrade","status":"warning","message":"已安全降级，未向用户展示底层异常"}]
    return {**base, "progress_events": progress, "final_response": {"answer_type":"no_verified_guide", "message": message, "progress_events": progress}}


# =============================================================================
# 会话类
# =============================================================================
@dataclass
class BuQiuRenSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    raw_query: str | None = None
    user_context: dict[str, Any] = field(default_factory=dict)
    last_state: dict[str, Any] | None = None
    last_clarification_response: dict[str, Any] | None = None

    def _apply_reply_selection(self, reply: str) -> bool:
        response = self.last_clarification_response or ((self.last_state or {}).get("final_response") or {})
        selected = _find_quick_reply(response, reply)
        if not selected:
            if response.get("answer_type") not in INTERACTIVE_RESPONSE_TYPES:
                return False
            missing_slots = [x for x in (response.get("missing_slots") or []) if isinstance(x, dict)]
            slot = (missing_slots[0].get("slot") if missing_slots else "") or "custom_reply"
            self.user_context[slot] = reply
            self.user_context["custom_reply"] = reply
            # 将上一轮的 missing_slots 传给后续 workflow，让 check_integrity 知道追问已回答
            self.user_context["_last_clarification_slots"] = missing_slots
            return True
        selected_context = dict(selected.get("context") or {})
        slot = selected.get("slot")
        if slot and selected.get("value") and slot not in selected_context:
            selected_context[slot] = selected.get("value")
        self.user_context.update(selected_context)
        # 将上一轮的 missing_slots 传给后续 workflow
        missing_slots = [x for x in (response.get("missing_slots") or []) if isinstance(x, dict)]
        self.user_context["_last_clarification_slots"] = missing_slots
        return True

    def ask(self, message: str, *, context: dict[str, Any] | None = None, new_query: bool = False) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            raise ValueError("请输入问题。")

        if self.raw_query is None or new_query:
            self.raw_query = message
            self.user_context = {}

        if context:
            self.user_context.update(context)

        state = run_buqiuren(self.raw_query, user_context=self.user_context)
        self.last_state = state

        resp = state.get("final_response") or {}
        if resp.get("answer_type") == "clarification_required":
            self.last_clarification_response = resp
        else:
            self.last_clarification_response = None
        return state

    def choose(self, reply: str) -> dict[str, Any]:
        reply = (reply or "").strip()
        if not reply:
            raise ValueError("请选择一个选项。")
        if self.raw_query is None:
            raise ValueError("会话还没有原始问题。")

        self._apply_reply_selection(reply)
        state = run_buqiuren(self.raw_query, user_context=self.user_context)
        self.last_state = state

        resp = state.get("final_response") or {}
        if resp.get("answer_type") == "clarification_required":
            self.last_clarification_response = resp
        else:
            self.last_clarification_response = None
        return state

    def ask_stream(self, message: str, *, context: dict[str, Any] | None = None, new_query: bool = False):
        """Generator: yields SSE events for real-time streaming"""
        message = (message or "").strip()
        if not message:
            raise ValueError("请输入问题。")

        if self.raw_query is None or new_query:
            self.raw_query = message
            self.user_context = {}

        if context:
            self.user_context.update(context)

        # Check if this message is a quick reply choice for the current session
        if self.last_state is not None and self.raw_query is not None and self.raw_query != message:
            if _response_type(self.last_state) in INTERACTIVE_RESPONSE_TYPES:
                self._apply_reply_selection(message)
            else:
                self.raw_query = message
                self.user_context = {}

        # Emit start event
        yield {"type": "start", "session_id": self.session_id}

        # Stream workflow events
        for event in run_buqiuren_stream(self.raw_query, user_context=self.user_context):
            if event["type"] == "thinking":
                public_status = _clean_text(event.get("public_status") or "").strip()
                if not public_status:
                    continue
                yield {
                    "type": "thinking",
                    "node": event.get("node", ""),
                    "thinking": public_status,
                    "status": event.get("status", "thinking"),
                }
            elif event["type"] == "node_update":
                events = event.get("events", [])
                yield {
                    "type": "node_update",
                    "node_name": event["node_name"],
                    "timeline": _user_friendly_timeline(events),
                    "reasoning_steps": build_reasoning_steps({"progress_events": events}),
                }
            elif event["type"] == "complete":
                state = event["state"]
                self.last_state = state
                resp = state.get("final_response") or {}
                answer_type = resp.get("answer_type") or ""

                if answer_type == "clarification_required":
                    self.last_clarification_response = resp
                else:
                    self.last_clarification_response = None

                try:
                    payload = build_frontend_payload(state, None)
                    payload["session_id"] = self.session_id
                except Exception:
                    payload = {
                        "session_id": self.session_id,
                        "type": "error",
                        "message": "系统处理失败，请稍后重试。",
                        "timeline": [], "quick_replies": [],
                        "card": None, "actions": [], "sources": [],
                    }

                yield {"type": "complete", "payload": payload}
            elif event["type"] == "error":
                yield {
                    "type": "error",
                    "session_id": self.session_id,
                    "message": event.get("error", "系统处理失败，请稍后重试。"),
                }



    def payload(self) -> dict[str, Any]:
        return build_frontend_payload(self.last_state or {}, None)


def new_buqiuren_session() -> BuQiuRenSession:
    return BuQiuRenSession()
