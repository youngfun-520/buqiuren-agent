"""
通用智能体化的工作流：LLM 语义理解 + 知识库语义匹配 + 智能兜底。
不再用关键词/正则做业务路由。
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from typing_extensions import TypedDict
try:
    from langgraph.graph import StateGraph, START, END
except ImportError:
    StateGraph = None
    START = END = None

from app.core.config import CONFIG
from app.agent.llm import llm, LLMUnavailable
from app.agent.providers.firecrawl_mcp import get_search_provider, SearchProviderError
from app.agent.understanding import understand_user_request, stream_understand_user_request
from app.agent.fallback import is_public_service_query, build_intelligent_fallback
from app.storage.guide_store import GuideKnowledgeBase, semantic_match, _normalize_record

from pathlib import Path

def _resolve_kb_path() -> str:
    configured = Path(CONFIG.kb_path)
    if configured.exists():
        return str(configured)
    bundled = Path(__file__).resolve().parents[2] / "data" / "buqiuren_production_guide_kb.json"
    return str(bundled)

production_guide_kb = GuideKnowledgeBase(_resolve_kb_path())
production_guide_kb.load()

# =============================================================================
# 状态定义
# =============================================================================
class BuQiuRenState(TypedDict, total=False):
    raw_query: str
    user_context: dict[str, Any]
    normalized_query: str
    city: str
    district: Optional[str]
    service_subject: Optional[str]
    scenario: Optional[str]
    user_slots: dict[str, Any]
    life_event_category: str
    life_event_name: str
    service_item_code: str
    service_item_name: str
    missing_slots: list[dict[str, Any]]
    need_clarification: bool
    clarification_question: str
    guide_record: Optional[dict[str, Any]]
    service_card: Optional[dict[str, Any]]
    final_response: dict[str, Any]
    progress_events: list[dict[str, Any]]
    debug_traces: list[dict[str, Any]]
    # 语义理解结果
    understanding: Optional[dict[str, Any]]
    semantic_candidates: Optional[list[dict[str, Any]]]

# =============================================================================
# 保留：仅用于 prompt 构造，不做业务路由
# =============================================================================
LIFE_EVENT_CATEGORIES = {
    "career": {"name": "业有所成", "description": "就业、社保、劳动关系、工资拖欠、劳动仲裁等"},
    "transport": {"name": "行有所载", "description": "驾驶证、车辆、交通等"},
    "housing": {"name": "住有所居", "description": "居住证、住房、公积金、租房备案等"},
    "marriage": {"name": "婚有所系", "description": "结婚登记、离婚登记等"},
    "child": {"name": "幼有所长", "description": "少儿医保、入学、出生登记等"},
    "elderly": {"name": "老有所依", "description": "退休、养老金、高龄津贴等"},
    "unknown": {"name": "未识别", "description": "无法判断"},
}
SERVICE_ITEMS = {
    "housing": [
        {"code":"residence_permit_apply","name":"居住证办理","subjects":["居住证"],"scenarios":["办理","申请","申领"],"keywords":["居住证办理","居住证申请","居住证申领"],"default_query":"居住证 办理 官方 办事指南"},
        {"code":"residence_permit_renew","name":"居住证续签","subjects":["居住证"],"scenarios":["续签","续期"],"keywords":["居住证续签","居住证续期"],"default_query":"居住证 续签 官方 办事指南"},
        {"code":"housing_fund_withdraw","name":"公积金提取","subjects":["公积金","住房公积金"],"scenarios":["提取"],"keywords":["公积金提取","住房公积金提取"],"default_query":"住房公积金 提取 官方 办事指南"},
        {"code":"rental_filing","name":"房屋租赁登记备案","subjects":["租房备案","房屋租赁"],"scenarios":["办理","申请","备案"],"keywords":["租房备案","房屋租赁登记备案"],"default_query":"房屋租赁登记备案 官方 办事指南"},
    ],
    "career": [
        {"code":"wage_arrears_complaint","name":"工资拖欠投诉","subjects":["工资拖欠","拖欠工资","欠薪","劳动维权"],"scenarios":["投诉","举报","维权","办理"],"keywords":["工资拖欠投诉","拖欠工资投诉","欠薪投诉","劳动维权"],"default_query":"拖欠工资 投诉 官方 办事指南"},
        {"code":"social_security_apply","name":"社保参保","subjects":["社保","社会保险"],"scenarios":["办理","申请","参保"],"keywords":["社保参保","社保办理"],"default_query":"社保 参保 官方 办事指南"},
    ],
    "transport": [{"code":"driver_license_renew","name":"驾驶证换证","subjects":["驾驶证","驾照"],"scenarios":["换证","到期换证"],"keywords":["驾驶证换证","驾照换证","驾驶证到期"],"default_query":"驾驶证 期满换证 官方 办事指南"}],
    "marriage": [
        {"code":"marriage_registration_appointment","name":"结婚登记预约","subjects":["结婚登记","结婚","领证"],"scenarios":["预约"],"keywords":["结婚登记预约","结婚预约","领证预约"],"default_query":"结婚登记 预约 官方 办事指南"},
        {"code":"marriage_registration","name":"结婚登记","subjects":["结婚登记","结婚","领证"],"scenarios":["办理","申请","登记"],"keywords":["结婚登记","领证"],"default_query":"结婚登记 官方 办事指南"},
    ],
    "child": [
        {"code":"child_medical_insurance_apply","name":"少儿医保参保","subjects":["少儿医保","儿童医保","小孩医保"],"scenarios":["申请","办理","参保"],"keywords":["少儿医保","少儿医保参保","儿童医保"],"default_query":"少儿医保 参保 官方 办事指南"},
        {"code":"primary_school_admission","name":"小学学位申请","subjects":["小学一年级报名","小一报名","小学","学位"],"scenarios":["查询","申请","报名"],"keywords":["小一报名","小学一年级报名","小学学位申请"],"default_query":"小学一年级报名 学位申请 官方"},
    ],
    "elderly": [{"code":"retirement_apply","name":"退休办理","subjects":["退休"],"scenarios":["办理","申请"],"keywords":["退休办理"],"default_query":"退休 办理 官方 办事指南"}],
}
SERVICE_ITEM_INDEX = {item["code"]: item for items in SERVICE_ITEMS.values() for item in items}
OFFICIAL_DOMAIN_RULES = [
    {"domain":"gov.cn","name":"政府官方网站"}, {"domain":"gjzwfw.gov.cn","name":"国家政务服务平台"}, {"domain":"www.gov.cn","name":"中国政府网"},
    {"domain":"mohrss.gov.cn","name":"人力资源和社会保障部"}, {"domain":"nhsa.gov.cn","name":"国家医保局"}, {"domain":"mps.gov.cn","name":"公安部"},
    {"domain":"mca.gov.cn","name":"民政部"}, {"domain":"moe.gov.cn","name":"教育部"}, {"domain":"mot.gov.cn","name":"交通运输部"},
    {"domain":"gjj.gov.cn","name":"住房公积金监管服务平台"}, {"domain":"gdzwfw.gov.cn","name":"广东政务服务网"}, {"domain":"sz.gov.cn","name":"深圳政府在线"}, {"domain":"ga.sz.gov.cn","name":"深圳市公安局"},
    {"domain":"hrss.sz.gov.cn","name":"深圳市人力资源和社会保障局"}, {"domain":"hsa.sz.gov.cn","name":"深圳市医疗保障局"}, {"domain":"zjj.sz.gov.cn","name":"深圳市住房和建设局"},
    {"domain":"gjj.sz.gov.cn","name":"深圳市住房公积金管理中心"}, {"domain":"mzj.sz.gov.cn","name":"深圳市民政局"}, {"domain":"szeb.sz.gov.cn","name":"深圳市教育局"},
]

# =============================================================================
# 工具函数
# =============================================================================
def now_iso() -> str: return datetime.now(timezone.utc).isoformat()
def sha256_text(text: str) -> str: return hashlib.sha256((text or "").encode()).hexdigest()
def _domain(url: str) -> str:
    try: return urlparse(url).netloc.lower().split(":")[0]
    except Exception: return ""
def _domain_matches(domain: str, base: str) -> bool: return domain == base or domain.endswith("." + base)
def is_official_url(url: str) -> bool: return any(_domain_matches(_domain(url), r["domain"]) for r in OFFICIAL_DOMAIN_RULES)
def source_name_for_url(url: str) -> str:
    domain = _domain(url)
    for rule in sorted(OFFICIAL_DOMAIN_RULES, key=lambda x: len(x["domain"]), reverse=True):
        if _domain_matches(domain, rule["domain"]): return rule["name"]
    if _domain_matches(domain, "gov.cn"):
        return "政府官方网站"
    return "未知官方来源"


def _record_city(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return ""
    guide = record.get("guide") if isinstance(record.get("guide"), dict) else {}
    return str(record.get("city") or guide.get("city") or "").strip()


def _normalize_city_name(city: str | None) -> str:
    value = (city or "").strip()
    for suffix in ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区", "自治州", "地区", "盟", "省", "市"):
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _city_matches(user_city: str | None, record_city: str | None) -> bool:
    expected = _normalize_city_name(user_city)
    actual = _normalize_city_name(record_city)
    if not actual or actual in {"全国", "国家"}:
        return True
    if not expected:
        return False
    return expected == actual or actual.startswith(expected) or expected.startswith(actual)

def calculate_freshness(value: str | None) -> str:
    if not value: return "unknown"
    try: dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception: return "unknown"
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    age = max(0, (datetime.now(timezone.utc) - dt).days)
    if age <= CONFIG.guide_freshness_days: return "fresh"
    if age <= CONFIG.guide_expiry_days: return "stale"
    return "expired"


def _base_service_code(code: str | None) -> str:
    return (code or "").split(":", 1)[0]


def _collapse_blank_lines(text: str) -> str:
    lines = (text or "").splitlines()
    collapsed: list[str] = []
    blank_seen = False
    for line in lines:
        if line.strip():
            collapsed.append(line)
            blank_seen = False
        elif not blank_seen:
            collapsed.append("")
            blank_seen = True
    return "\n".join(collapsed).strip()


# =============================================================================
# 语义理解节点 - 多步骤流式推理
# =============================================================================
def understand_user_node(state: BuQiuRenState) -> BuQiuRenState:
    """
    多步骤流式推理：
    Step 1 - 城市识别
    Step 2 - 事项识别
    Step 3 - 槽位提取
    每步实时 yield thinking，主线程通过 queue 收到后立即推送到前端。
    """
    raw = (state.get("raw_query") or "").strip()
    ctx = state.get("user_context") or {}
    q = state.get("_thinking_queue") or _stream_thinking_queue

    def emit(node: str, thinking: str, public_status: str | None = None):
        if q is not None:
            try:
                event = {"node": node, "thinking": thinking, "status": "thinking"}
                if public_status:
                    event["public_status"] = public_status
                q.put_nowait(event)
            except queue.Full:
                pass

    understanding = {
        "is_public_service": True,
        "city": "",
        "service_goal": raw,
        "action_type": "unknown",
        "slots": {},
        "confidence": 0.5,
        "need_clarification": False,
        "clarification_question": "",
        "quick_replies": [],
    }

    # ── Step 1: 城市识别 ──────────────────────────────────────────────
    try:
        emit("city_recognition", "正在识别办理城市...")
        for step in llm.stream思考_gen(
            f"""用户输入：「{raw}」
已知上下文：{json.dumps(ctx, ensure_ascii=False)}
请判断用户想办理哪个城市的政务服务。

要求：
- 只返回 JSON：{{"city": "城市名", "city_confidence": 0.0-1.0, "public_status": "一句给用户看的阶段输出"}}
- 如果明确提到城市名，提取该城市；如果没提到，city_confidence 设为低（如 0.2），city 留空
- city_confidence 表示识别把握度，低于 0.3 表示未识别到明确城市
- public_status 只描述系统正在识别或已识别到什么；必须是陈述句。
- public_status 禁止出现"请""提供""确认""选择""回复""是否"和问号；如果需要后续追问，public_status 写"正在准备下一步可选项"。
- public_status 不展示推理过程、规则、模型内部判断。

只返回 JSON，不要其他文字。""",
        ):
            if not step.get("done"):
                emit("city_recognition", f"正在分析城市：{step['thinking']}")
            else:
                city_result = step.get("result", {})
                city = city_result.get("city", "")
                confidence = city_result.get("city_confidence", 0)
                if confidence >= 0.3 and city:
                    understanding["city"] = city
                else:
                    understanding["city"] = ""  # 未识别到明确城市
                public_status = (city_result.get("public_status") or "").strip()
                if public_status:
                    emit("city_recognition_done", public_status, public_status=public_status)
    except Exception as exc:
        emit("city_recognition_error", f"城市识别异常：{exc}")

    # ── Step 2: 事项识别 ─────────────────────────────────────────────
    city_for_prompt = understanding["city"] or "未明确"
    try:
        emit("service_recognition", f"正在识别「{city_for_prompt}」的办事事项...")
        for step in llm.stream思考_gen(
            f"""你是"不求人"政务办事智能体的语义理解模块。
用户输入：「{raw}」
办理城市：{city_for_prompt}
已知上下文：{json.dumps(ctx, ensure_ascii=False)}

请从语义上理解用户真正想办理/查询/投诉/领取/转移的事项。先用 <think> 思考分析过程，再用  输出 JSON。

返回字段：
{{
  "is_public_service": true/false,
  "service_goal": "用户想办的实际事项，简洁描述",
  "action_type": "apply/renew/withdraw/query/complaint/transfer/appointment/consult/unknown",
  "domain": "社保/公积金/公安/劳动权益/教育/民政/交通/住房/其他",
  "confidence": 0.0到1.0之间的小数,
  "need_clarification": true/false,
  "clarification_question": "如果需要追问，简短问句，否则空字符串",
  "quick_replies": [],
  "public_status": "一句给用户看的阶段输出，只描述正在识别或已识别到的办理事项；必须是陈述句，禁止出现请、提供、确认、选择、回复、是否和问号"
}}

只返回 JSON，不要有其他文字。""",
        ):
            if not step.get("done"):
                emit("service_recognition", f"正在分析事项：{step['thinking']}")
            else:
                result = step.get("result", {})
                if result:
                    for k in ["is_public_service", "service_goal", "action_type", "domain", "confidence", "need_clarification", "clarification_question", "quick_replies"]:
                        if k in result:
                            understanding[k] = result[k]
                public_status = (result.get("public_status") or "").strip()
                if public_status:
                    emit("service_recognition_done", public_status, public_status=public_status)
    except Exception as exc:
        emit("service_recognition_error", f"事项识别异常：{exc}")

    # ── Step 3: 槽位提取 ──────────────────────────────────────────────
    try:
        emit("slot_extraction", "正在提取用户身份和场景信息...")
        for step in llm.stream思考_gen(
            f"""用户输入：「{raw}」
事项：{understanding.get('service_goal', '')}
城市：{city_for_prompt}

请提取用户的身份状态和办理场景。

返回 JSON：
{{
  "slots": {{
    "identity_status": "用户身份状态，如：在职/离职/退休/学生/自由职业/未知",
    "scenario": "办理场景，如：首次办理/续签/转移/查询/投诉/领取，未知时留空",
    "subtype": "事项子类型，如：租房提取/购房提取/初次领取，未知时留空"
  }},
  "public_status": "一句给用户看的阶段输出，只概括正在提取或已提取到的关键信息；必须是陈述句，禁止出现请、提供、确认、选择、回复、是否和问号"
}}

只返回 JSON。""",
        ):
            if not step.get("done"):
                emit("slot_extraction", f"正在提取信息：{step['thinking']}")
            else:
                slots_result = step.get("result", {})
                if slots_result and isinstance(slots_result, dict):
                    understanding["slots"] = slots_result.get("slots", {})
                public_status = (slots_result.get("public_status") or "").strip() if isinstance(slots_result, dict) else ""
                if public_status:
                    emit("slot_extraction_done", public_status, public_status=public_status)
    except Exception as exc:
        emit("slot_extraction_error", f"槽位提取异常：{exc}")

    state["understanding"] = understanding

    progress = state.get("progress_events", []) + [
        {"node": "understand_user", "status": "done", "message": "正在分析您的问题...", "data": {"thinking": f"分析：用户想了解「{understanding.get('service_goal', raw)}」的相关办事指南"}},
        {"node": "understand_user_done", "status": "done", "message": f"已理解，您想办「{understanding.get('service_goal', raw)}」", "data": {"thinking": f"确认服务事项：{understanding.get('service_goal', raw)}"}},
    ]

    city = understanding.get("city") or ""  # 城市由 LLM 识别，不再默认深圳

    llm_slots = {}
    if understanding.get("slots"):
        for k, v in understanding["slots"].items():
            if v and str(v).strip():
                llm_slots[k] = v

    user_slots = {**llm_slots, **(state.get("user_context") or {})}

    return {
        **state,
        "raw_query": raw,
        "normalized_query": understanding.get("service_goal", raw) if understanding else raw,
        "city": city,
        "service_subject": understanding.get("service_goal", "") if understanding else "",
        "scenario": understanding.get("action_type", "") if understanding else "",
        "user_slots": user_slots,
        "progress_events": progress,
    }

# =============================================================================
# 语义匹配节点
# =============================================================================
def smart_match_node(state: BuQiuRenState) -> BuQiuRenState:
    """
    LLM 语义匹配 + 生活事件分类，找到最相关的知识库记录。
    流式版本实时输出 thinking 步骤。

    决策阈值（基于 semantic_match score + understanding.confidence 联合决策）：
    - understanding.confidence >= 0.7 且 score >= 0.30：直接 service_card
    - 0.5 <= understanding.confidence < 0.7 且 score >= 0.45：clarification_required
    - understanding.confidence < 0.5 或 score < 0.45：进入 guidance_fallback
    """
    understanding = state.get("understanding") or {}
    ctx = state.get("user_context") or {}
    u_confidence = understanding.get("confidence", 0.5)
    q = state.get("_thinking_queue") or _stream_thinking_queue
    raw = state.get("raw_query", "")

    def emit(node: str, thinking: str, public_status: str | None = None):
        if q is not None:
            try:
                event = {"node": node, "thinking": thinking, "status": "thinking"}
                if public_status:
                    event["public_status"] = public_status
                q.put_nowait(event)
            except queue.Full:
                pass

    # 先做语义匹配（同步，不涉及 LLM 调用）
    records = production_guide_kb.list_all()
    candidates = semantic_match(understanding, records, context=ctx)
    top_score = candidates[0].get("score", 0) if candidates else 0

    # 根据 understanding confidence 调整阈值
    if u_confidence >= 0.7:
        score_threshold = 0.30
    elif u_confidence >= 0.5:
        score_threshold = 0.45
    else:
        score_threshold = 0.30

    # Life event classification（仅用于展示标签，不做路由）
    domain = understanding.get("domain", "")
    domain_map = {"社保": "career", "劳动权益": "career", "公积金": "housing", "住房": "housing", "公安": "transport", "交通": "transport", "教育": "child", "民政": "marriage"}
    if domain in domain_map:
        cat = domain_map[domain]
        name = LIFE_EVENT_CATEGORIES[cat]["name"]
    else:
        cat = "unknown"
        name = "未识别"

    # LLM 二次确认（流式）
    if top_score >= score_threshold:
        top_key = candidates[0]["record_key"]
        matched_record = production_guide_kb.get(top_key)
        if matched_record:
            rec = _normalize_record(matched_record, top_key)
            service_name = rec.get("service_item_name", top_key)
            record_city = _record_city(rec)
            user_city = state.get("city") or understanding.get("city") or ""

            if q is not None:
                try:
                    q.put_nowait({"node": "smart_match", "thinking": f"发现「{service_name}」相关指南，匹配度 {top_score:.0%}", "status": "thinking"})
                except queue.Full:
                    pass

            return {
                **state,
                "service_item_code": top_key,
                "service_item_name": service_name,
                "guide_record": rec if _city_matches(user_city, record_city) else None,
                "semantic_candidates": candidates,
                "life_event_category": cat,
                "life_event_name": name,
                "progress_events": state.get("progress_events", []) + [
                    {"node": "smart_match", "status": "done", "message": "正在查询知识库...", "data": {"thinking": f"查找「{service_name}」的相关指南"}},
                    {"node": "smart_match_done", "status": "done", "message": f"找到了！事项：{service_name}", "data": {"thinking": f"匹配度 {top_score:.0%}，确认办理「{service_name}」"}},
                ],
            }

    return {
        **state,
        "service_item_code": "unknown",
        "service_item_name": "未识别",
        "semantic_candidates": candidates,
        "life_event_category": cat,
        "life_event_name": name,
        "progress_events": state.get("progress_events", []) + [
            {"node": "smart_match", "status": "done", "message": "正在分析...", "data": {"thinking": "置信度较低，继续深度分析"}},
        ],
    }

# =============================================================================
# 检查信息完整性（基于指南实际需要生成追问）
# =============================================================================
def check_integrity_node(state: BuQiuRenState) -> BuQiuRenState:
    """
    基于 guide_record 的实际字段需求，检查信息完整性。
    如果 guide_record 有缺必填项，则生成追问；否则认为完整。
    流式输出分析过程。
    """
    code = state.get("service_item_code", "")
    if code == "unknown":
        return {
            **state,
            "need_clarification": False,
            "missing_slots": [],
            "clarification_question": "",
            "progress_events": state.get("progress_events", []) + [{"node": "check_integrity", "status": "done", "message": "无需补充信息"}],
        }

    raw_query = state.get("raw_query", "")
    slots = state.get("user_slots") or {}
    item_name = state.get("service_item_name", "")
    guide_record = state.get("guide_record") or {}
    guide = guide_record.get("guide", {}) if guide_record else {}

    q = state.get("_thinking_queue") or _stream_thinking_queue

    def emit(node: str, thinking: str, public_status: str | None = None):
        if q is not None:
            try:
                event = {"node": node, "thinking": thinking, "status": "thinking"}
                if public_status:
                    event["public_status"] = public_status
                q.put_nowait(event)
            except queue.Full:
                pass

    # ── 如果用户刚回答了追问（user_context 中有来自上一轮 missing_slots 的值），直接认为信息完整 ──
    last_clarification = (state.get("user_context") or {}).get("_last_clarification_slots") or []
    if last_clarification:
        answered_slots = set()
        for spec in last_clarification:
            if isinstance(spec, dict) and spec.get("slot"):
                slot_name = spec["slot"]
                if slots.get(slot_name):
                    answered_slots.add(slot_name)
        if answered_slots:
            emit("check_integrity", "已根据上一轮回答确认关键信息，信息完整。")
            return {
                **state,
                "need_clarification": False,
                "missing_slots": [],
                "clarification_question": "",
                "progress_events": state.get("progress_events", []) + [
                    {"node": "check_integrity", "status": "done", "message": "关键信息已确认"},
                    {"node": "check_integrity_done", "status": "done", "message": "信息完整，可以生成指南"},
                ],
            }

    # 分析 guide 需要哪些必填字段
    required_fields = []
    for key in ["conditions", "materials", "methods"]:
        items = guide.get(key, [])
        if isinstance(items, list) and items:
            required_fields.append(key)

    emit("check_integrity", "正在检查信息完整性...")

    # 从 guide 提取必须项，然后让 LLM 判断用户已提供哪些
    if required_fields:
        prompt_required = f"""事项名称：{item_name}
已知用户信息：{json.dumps(slots, ensure_ascii=False)}

该事项的办事指南包含以下关键信息类别：{required_fields}

请判断用户已提供的信息是否足够。如果不够，一次只问一个最关键的问题，并为这个问题生成 2 到 5 个可选回复；选项数量和内容由你根据事项、城市和上下文决定。

返回JSON：
{{"complete": bool, "public_status": "一句给用户看的阶段输出，只描述正在判断信息完整性或正在准备下一步选项；必须是陈述句，禁止出现请、提供、确认、选择、回复、是否和问号", "missing_slots": [{{"slot": "字段名", "label": "中文标签", "question": "追问问题", "options": [{{"label":"选项1","value":"选项1"}},{{"label":"选项2","value":"选项2"}}]}}]}}

如果信息完整足够，返回 {{"complete": true, "public_status": "一句给用户看的阶段输出，只描述正在整理后续指南", "missing_slots": []}}。"""
    else:
        prompt_required = f"""事项名称：{item_name}
已知用户信息：{json.dumps(slots, ensure_ascii=False)}

请判断用户已提供的信息是否足够回答办事指南问题。如果不够，一次只问一个最关键的问题，并为这个问题生成 2 到 5 个可选回复；选项数量和内容由你根据事项、城市和上下文决定。

返回JSON：
{{"complete": bool, "public_status": "一句给用户看的阶段输出，只描述正在判断信息完整性或正在准备下一步选项；必须是陈述句，禁止出现请、提供、确认、选择、回复、是否和问号", "missing_slots": [{{"slot": "字段名", "label": "中文标签", "question": "追问问题", "options": [{{"label":"选项1","value":"选项1"}},{{"label":"选项2","value":"选项2"}}]}}]}}

如果信息完整足够，返回 {{"complete": true, "public_status": "一句给用户看的阶段输出，只描述正在整理后续指南", "missing_slots": []}}。"""

    try:
        result = llm.invoke_json(prompt_required, {"complete": True, "missing_slots": []})
    except (LLMUnavailable, Exception):
        result = {"complete": True, "missing_slots": []}

    public_status = (result.get("public_status") or "").strip() if isinstance(result, dict) else ""
    if public_status:
        emit("check_integrity_analyze", public_status, public_status=public_status)

    missing = result.get("missing_slots", [])
    if missing:
        return {
            **state,
            "need_clarification": True,
            "missing_slots": missing,
            "clarification_question": missing[0]["question"],
            "progress_events": state.get("progress_events", []) + [
                {"node": "check_integrity", "status": "done", "message": "正在确认办理条件...", "data": {"thinking": "分析需要哪些关键信息"}},
                {"node": "check_integrity_done", "status": "done", "message": f"需要确认：{missing[0]['question']}", "data": {"thinking": f"追问：{missing[0]['question']}"}},
            ],
        }

    return {
        **state,
        "need_clarification": False,
        "missing_slots": [],
        "clarification_question": "",
        "progress_events": state.get("progress_events", []) + [
            {"node": "check_integrity", "status": "done", "message": "正在确认办理条件...", "data": {"thinking": "检查信息完整性"}},
            {"node": "check_integrity_done", "status": "done", "message": "信息已齐全，可以生成指南", "data": {"thinking": "信息完整"}},
        ],
    }

# =============================================================================
# 检索服务指南
# =============================================================================
def retrieve_service_guide(state: BuQiuRenState) -> BuQiuRenState:
    code = state.get("service_item_code")
    if not code or code == "unknown":
        return {**state, "guide_record": None}

    city = (state.get("city") or "").strip()
    existing = production_guide_kb.get(f"{_base_service_code(code)}:{city}") if city else None
    existing = existing or production_guide_kb.get(code) or production_guide_kb.get(_base_service_code(code))
    if (
        existing
        and existing.get("review_status") in ["auto_verified_official", "human_reviewed"]
        and _city_matches(state.get("city"), _record_city(existing))
    ):
        rec = _normalize_record(existing, code)
        return {
            **state,
            "guide_record": rec,
            "progress_events": state.get("progress_events", []) + [
                {"node": "retrieve_guide", "status": "done", "message": "正在查找官方指南...", "data": {"thinking": "查询本地已核验的指南库"}},
                {"node": "retrieve_guide_done", "status": "done", "message": "已找到核验过的官方指南", "data": {"thinking": f"命中本地指南，来源：{existing.get('review_status')}"}},
            ],
        }

    # MCP-only 搜索
    try:
        updated = search_and_extract_official_guide(state)
        if updated.get("guide_record"):
            return updated
    except Exception as exc:
        return {
            **state,
            "guide_record": None,
            "progress_events": state.get("progress_events", []) + [
                {"node": "retrieve_guide", "status": "warning", "message": "实时检索暂不可用", "data": {"thinking": "网络检索暂不可用"}},
            ],
        }
    return {**state, "guide_record": None}

def build_search_queries(code: str | None, normalized_query: str | None, city: str | None = None) -> list[str]:
    item = SERVICE_ITEM_INDEX.get(_base_service_code(code), {})
    name = item.get("name") or normalized_query or ""
    base = item.get("default_query") or normalized_query or name
    city = (city or "").strip()
    city_base = f"{city} {base}".strip()
    city_name = f"{city} {name}".strip()
    primary_query = city_base if ("官方" in base or "办理指南" in base) else f"{city_base} 官方 办理指南"
    return [q for q in [
        primary_query,
        f"{city_name} 政务服务网 办理",
        f"{city_name} 人民政府 办理",
        f"site:gov.cn {city_name} 办理",
        f"国家政务服务平台 {city_name} 办理",
    ] if q.strip()]

def _clean_html_to_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return title, soup.get_text("\n", strip=True)

def fetch_url_text(url: str) -> dict[str, Any]:
    if not is_official_url(url):
        raise ValueError(f"非官方白名单 URL，拒绝抓取: {url}")
    page = get_search_provider().scrape(url)
    text = page.get("text") or page.get("markdown") or ""
    title = page.get("title") or ""
    if not text and page.get("html"):
        title2, text = _clean_html_to_text(page["html"])
        title = title or title2
    if not text:
        raise SearchProviderError("Firecrawl MCP 未返回可用页面正文")
    return {
        "url": url, "title": title, "text": _collapse_blank_lines(text)[:50000],
        "source_name": source_name_for_url(url), "fetched_at": now_iso(),
        "content_hash": sha256_text(text), "provider": "firecrawl_mcp",
    }

def search_and_extract_official_guide(state: BuQiuRenState) -> BuQiuRenState:
    official_results = []
    for q in build_search_queries(state.get("service_item_code"), state.get("normalized_query"), state.get("city")):
        for r in get_search_provider().search(q, limit=CONFIG.max_search_results):
            url = r.get("url")
            if url and is_official_url(url) and all(x.get("url") != url for x in official_results):
                official_results.append({"title": r.get("title") or source_name_for_url(url), "url": url, "snippet": r.get("snippet") or "", "source_name": source_name_for_url(url)})
        if len(official_results) >= 2:
            break

    progress = state.get("progress_events", []) + [
        {"node": "search_official", "status": "done" if official_results else "warning", "message": f"正在查询官方来源..." if official_results else "未找到官方来源"},
    ]

    pages = []
    for r in official_results[:CONFIG.max_fetch_pages]:
        try:
            pages.append(fetch_url_text(r["url"]))
        except Exception:
            pass

    progress.append({"node": "fetch_pages", "status": "done" if pages else "warning", "message": f"正在抓取页面内容..." if pages else "抓取失败"})

    if not pages:
        return {**state, "progress_events": progress, "guide_record": None}

    prompt_pages = "\n\n".join([f"标题：{p['title']}\nURL：{p['url']}\n正文：{p['text'][:12000]}" for p in pages])
    prompt = f"""你是"不求人"的官方办事指南抽取节点。只能根据页面正文抽取"办事指南"，不得编造。只返回 JSON，字段：is_relevant, service_item_name, summary, conditions, materials, methods, steps, online_entry, offline_locations, processing_time, fees, tips, source_urls, confidence, missing_fields。\n用户问题：{state.get('raw_query')}\n官方页面：\n{prompt_pages}"""

    try:
        guide = llm.invoke_json(prompt, {"is_relevant": False, "confidence": 0, "missing_fields": ["LLM JSON 解析失败"]})
    except Exception as exc:
        raise SearchProviderError(f"LLM 抽取失败：{exc}")

    if not guide.get("is_relevant") or float(guide.get("confidence") or 0) < 0.35:
        progress.append({"node": "extract_guide", "status": "warning", "message": "未能从页面提取到有效指南"})
        return {**state, "progress_events": progress, "guide_record": None}

    source_urls = [u for u in guide.get("source_urls", []) if any(p["url"] == u for p in pages)] or [pages[0]["url"]]
    sources = [{"title": next((p["title"] for p in pages if p["url"] == u), source_name_for_url(u)), "url": u, "source_name": source_name_for_url(u)} for u in source_urls]
    record = {
        "service_code": state.get("service_item_code"),
        "service_item_name": state.get("service_item_name"),
        "city": state.get("city") or "",
        "guide": guide, "sources": sources,
        "fetched_at": now_iso(), "reviewed_at": now_iso(),
        "review_status": "auto_verified_official",
        "provider": "firecrawl_mcp",
    }
    save_key = _base_service_code(state.get("service_item_code")) or "unknown"
    if state.get("city"):
        save_key = f"{save_key}:{state.get('city')}"
    production_guide_kb.upsert(save_key, record)
    progress.extend([
        {"node": "extract_guide", "status": "done", "message": "正在整理办事指南...", "data": {"thinking": "从官方页面提取关键办事信息"}},
        {"node": "extract_guide_done", "status": "done", "message": "已生成办事指南", "data": {"thinking": "指南整理完成，可查看详情"}},
    ])
    return {**state, "progress_events": progress, "guide_record": record}

# =============================================================================
# 构建最终响应
# =============================================================================
def build_response_node(state: BuQiuRenState) -> BuQiuRenState:
    """Build final response with card or fallback"""
    progress = state.get("progress_events", [])

    if state.get("need_clarification"):
        resp = {
            "answer_type": "clarification_required",
            "message": "",  # 追问内容由 missing_slots 选项承载，不再单独显示文本
            "missing_slots": state.get("missing_slots") or [],
            "progress_events": progress,
        }
    elif state.get("service_item_code") in [None, "unknown"]:
        understanding = state.get("understanding") or {}
        is_service = understanding.get("is_public_service")
        if is_service is None:
            from app.agent.fallback import is_public_service_query as _is_ps
            is_service = _is_ps(state.get("raw_query") or "")
        if is_service:
            from app.agent.fallback import build_intelligent_fallback as _bif
            fb = _bif(state.get("raw_query") or "", state)
            resp = {**fb, "progress_events": progress + fb.get("progress_events", [])}
        else:
            resp = {
                "answer_type": "unsupported",
                "message": "您好，我不求人是政务办事智能助手，专注于政务服务事项的办事指南查询。",
                "progress_events": progress,
            }
    else:
        record = state.get("guide_record")
        if record:
            guide = record.get("guide") or {}
            freshness = calculate_freshness(record.get("fetched_at"))
            card = {
                "title": f"{state.get('city', '')}{state.get('service_item_name', '')}指南",
                "summary": guide.get("summary") or "",
                "service_item_code": state.get("service_item_code"),
                "service_item_name": state.get("service_item_name"),
                "life_event": {"category": state.get("life_event_category"), "name": state.get("life_event_name")},
                "conditions": guide.get("conditions") or [],
                "materials": guide.get("materials") or [],
                "methods": guide.get("methods") or [],
                "steps": guide.get("steps") or [],
                "online_entry": guide.get("online_entry") or [],
                "offline_locations": guide.get("offline_locations") or [],
                "processing_time": guide.get("processing_time") or "",
                "fees": guide.get("fees") or "",
                "tips": guide.get("tips") or [],
                "sources": record.get("sources") or [],
                "fetched_at": record.get("fetched_at"),
                "reviewed_at": record.get("reviewed_at"),
                "freshness": freshness,
                "disclaimer": "办事政策可能调整，请以官方最新页面为准。",
            }
            resp = {"answer_type": "service_card", "message": "已为你生成办事指南。", "card": card, "progress_events": progress}
        else:
            resp = {
                "answer_type": "no_verified_guide",
                "message": "暂未找到可验证的官方办事指南，因此不生成可能误导你的答案。建议稍后再试或前往官方渠道确认。",
                "service_item_code": state.get("service_item_code"),
                "service_item_name": state.get("service_item_name"),
                "progress_events": progress,
            }

    return {
        **state,
        "final_response": resp,
        "progress_events": progress + [
            {"node": "build_response", "status": "done", "message": "正在生成回复...", "data": {"thinking": "整理最终回复"}},
            {"node": "build_response_done", "status": "done", "message": "已生成回复", "data": {"thinking": "回复完成"}},
        ],
    }

# =============================================================================
# LangGraph 图（5 个真实 LLM/搜索 Agent 节点）
# =============================================================================

# 流式 thinking 共享 queue（通过 threading.local 传递，避免全局变量冲突）
_stream_thinking_queue: "queue.Queue | None" = None

def build_graph():
    if StateGraph is None:
        raise ImportError("langgraph is required but not installed")
    builder = StateGraph(BuQiuRenState)
    builder.add_node("understand_user", understand_user_node)
    builder.add_node("smart_match", smart_match_node)
    builder.add_node("check_integrity", check_integrity_node)
    builder.add_node("retrieve_guide", retrieve_service_guide)
    builder.add_node("build_response", build_response_node)
    builder.add_edge(START, "understand_user")
    builder.add_edge("understand_user", "smart_match")
    builder.add_conditional_edges(
        "smart_match",
        lambda s: "build_response" if s.get("service_item_code") in [None, "unknown"] else "check_integrity",
        {"build_response": "build_response", "check_integrity": "check_integrity"},
    )
    builder.add_conditional_edges(
        "check_integrity",
        lambda s: "build_response" if s.get("need_clarification") else "retrieve_guide",
        {"build_response": "build_response", "retrieve_guide": "retrieve_guide"},
    )
    builder.add_edge("retrieve_guide", "build_response")
    builder.add_edge("build_response", END)
    return builder.compile()

graph = build_graph()

def run_buqiuren(raw_query: str, user_context: dict[str, Any] | None = None) -> dict[str, Any]:
    initial: BuQiuRenState = {
        "raw_query": raw_query,
        "user_context": user_context or {},
        "progress_events": [],
        "debug_traces": [],
    }
    try:
        return graph.invoke(initial)
    except Exception as exc:
        response = {"answer_type": "error", "message": "系统处理失败，请稍后重试。", "progress_events": initial["progress_events"]}
        if CONFIG.dev_mode:
            response["error"] = str(exc)
        return {**initial, "final_response": response, "debug_traces": [{"node": "runtime_error", "error": str(exc)}] if CONFIG.dev_mode else []}

def run_buqiuren_stream(raw_query: str, user_context: dict[str, Any] | None = None):
    """
    Generator: 流式输出 thinking 步骤和节点事件。

    策略：节点执行在后台线程，通过全局 queue 实时传递 thinking 事件；
    主线程同时轮询 queue 和监控节点完成状态，实现真正的流式输出。
    """
    import queue
    import threading

    global _stream_thinking_queue

    thinking_queue: queue.Queue = queue.Queue(maxsize=100)
    _stream_thinking_queue = thinking_queue  # 全局共享，供节点内部使用
    result_holder = [None]
    stream_holder = [None]

    initial: BuQiuRenState = {
        "raw_query": raw_query,
        "user_context": user_context or {},
        "progress_events": [],
        "debug_traces": [],
        # 注意：不传 _thinking_queue，因为 LangGraph 不会把它传给节点状态
        # 节点通过全局 _stream_thinking_queue 访问 queue
    }

    def run_graph():
        try:
            result = graph.invoke(initial)
            result_holder[0] = (result, None)
        except Exception as exc:
            result_holder[0] = (None, exc)

    thread = threading.Thread(target=run_graph, daemon=True)
    thread.start()

    import time

    while True:
        # 1. 先检查 thinking queue（非阻塞）
        try:
            while True:
                thinking_event = thinking_queue.get_nowait()
                public_status = str(thinking_event.get("public_status") or "").strip()
                if not public_status:
                    continue
                yield {
                    "type": "thinking",
                    "node": thinking_event["node"],
                    "thinking": public_status,
                    "public_status": public_status,
                    "status": thinking_event.get("status", "thinking"),
                }
        except queue.Empty:
            pass

        # 2. 检查线程是否结束
        if thread.is_alive():
            time.sleep(0.05)
            continue

        # 3. 线程已结束，收集最终结果
        thread.join(timeout=1)
        if result_holder[0] is None:
            break

        result, exc = result_holder[0]
        if exc is not None:
            msg = str(exc) if CONFIG.dev_mode else "系统处理失败，请稍后重试。"
            yield {"type": "error", "error": msg}
            break

        # 4. 最后一次清空 queue
        try:
            while True:
                thinking_event = thinking_queue.get_nowait()
                public_status = str(thinking_event.get("public_status") or "").strip()
                if not public_status:
                    continue
                yield {
                    "type": "thinking",
                    "node": thinking_event["node"],
                    "thinking": public_status,
                    "public_status": public_status,
                    "status": thinking_event.get("status", "thinking"),
                }
        except queue.Empty:
            pass

        yield {"type": "complete", "state": result}
        _stream_thinking_queue = None
        break
