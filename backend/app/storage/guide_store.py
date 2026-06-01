"""
知识库管理：LLM 语义匹配，不再依赖关键词路由。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from app.agent.llm import llm, LLMUnavailable


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GuideKnowledgeBase:
    path: str
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    def load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            self.records = {}
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        self.records = data.get("records", {})

    def save(self) -> None:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"records": self.records, "saved_at": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, code: str | None) -> dict[str, Any] | None:
        if not code:
            return None
        return self.records.get(code)

    def upsert(self, code: str, record: dict[str, Any]) -> None:
        self.records[code] = record
        self.save()

    def list_all(self) -> list[dict[str, Any]]:
        """返回所有知识库记录，用于语义匹配"""
        return [
            {"record_key": k, **v}
            for k, v in self.records.items()
            if v.get("review_status") in ["auto_verified_official", "human_reviewed"]
        ]


def _normalize_record(record: dict[str, Any], code: str) -> dict[str, Any]:
    """标准化记录格式"""
    guide = record.get("guide") if isinstance(record.get("guide"), dict) else record
    sources = []
    for s in record.get("sources") or guide.get("sources") or []:
        if isinstance(s, dict):
            url = s.get("url") or s.get("source_url") or ""
            title = s.get("title") or s.get("name") or s.get("source_name") or "官方来源"
            sources.append({"title": title, "url": url, "source_name": s.get("source_name") or title})
    return {
        **record,
        "service_code": record.get("service_code") or record.get("service_item_code") or code,
        "guide": guide,
        "sources": sources,
    }


def _build_match_prompt(query: dict[str, Any], records: list[dict[str, Any]], context: dict[str, Any] | None = None) -> str:
    """构造语义匹配 prompt"""
    user_goal = query.get("service_goal", "")
    action_type = query.get("action_type", "")
    domain = query.get("domain", "")
    city = query.get("city") or "未明确"
    slots = query.get("slots", {})
    scenario = slots.get("scenario", "")
    subtype = slots.get("subtype", "")

    # 会话上下文（choose 场景）
    ctx_parts = []
    if context:
        for k, v in context.items():
            if v and str(v).strip():
                ctx_parts.append(f"{k}: {v}")
    ctx_text = f"\n已知上下文：{'; '.join(ctx_parts)}" if ctx_parts else ""

    # 构造候选摘要（包含更多 KB 信息）
    candidate_lines = []
    for i, rec in enumerate(records):
        name = rec.get("service_item_name") or rec.get("record_key", "")
        record_city = rec.get("city") or "未标注"
        guide = rec.get("guide", {})
        summary = guide.get("summary", "")[:150] if isinstance(guide, dict) else ""
        conditions = "; ".join(guide.get("conditions", [])[:3]) if isinstance(guide, dict) and guide.get("conditions") else ""
        sources = "; ".join([s.get("source_name", "") or s.get("title", "") for s in rec.get("sources", []) if isinstance(s, dict)][:2])
        candidate_lines.append(
            f"候选{i+1}: 代码={rec.get('record_key', '')} | 城市={record_city} | 名称={name} | 摘要={summary} | 条件={conditions} | 来源={sources}"
        )
    candidate_text = "\n".join(candidate_lines)

    return f"""你是"不求人"政务办事智能体的知识库匹配模块。

用户需求：
- 想办的事项：{user_goal}
- 行动类型：{action_type}
- 领域：{domain}
- 城市：{city}
- 场景：{scenario} | 子类型：{subtype}{ctx_text}

本地知识库候选（已核验官方指南）：
{candidate_text}

请从语义相关性角度，判断哪个候选最能解答用户的办事需求。

评分标准：
- 事项名称与用户想办的事是否语义相近（不是字面重叠，而是意图匹配）
- conditions（办理条件）是否与用户身份/场景匹配
- action_type（apply/withdraw/query/complaint 等）是否与用户意图匹配
- 当用户点击了 quick_reply 选项时（如"租房提取"），需结合上下文判断该选项是否与候选事项匹配
- 如果用户城市已明确，而候选城市是另一个城市，除非候选是全国通用指南，否则 score 不得高于 0.35
- 如果用户城市未明确，而候选是单一城市指南，不要把它当作最终可直接命中的指南，score 应保守

输出要求：只返回 JSON 数组，不要 markdown 代码块包裹。
输出格式：
[
  {{"record_key": "候选代码", "score": 0.0到1.0, "reason": "匹配原因简述（10字以内）", "matched_goal": "对应的用户需求描述"}},
  ...
]

注意：
- 只返回 JSON，不要任何解释文字
- 按 score 从高到低排序，最多返回 3 个候选
- 如果没有候选匹配，score 填 0，仍返回数组
- score < 0.6 时不要强行提高，即使有字面重叠
- 不确定时优先返回 lower score 而非错误高估
- 不要编造不在候选中的事项"""


def semantic_match(
    query: dict[str, Any],
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    语义匹配主函数：对用户意图和知识库记录做相关性评分。

    只使用 LLM 结构化评分；LLM 不可用时保守返回空候选。
    不在匹配层写任何具体事项的 if/else 路由。
    """
    if not records:
        return []

    prompt = _build_match_prompt(query, records, context)

    try:
        result = llm.invoke_json(prompt, [])
        if isinstance(result, list):
            valid = [r for r in result if isinstance(r, dict) and r.get("record_key")]
            if valid:
                return valid
    except (LLMUnavailable, Exception):
        pass

    return []
