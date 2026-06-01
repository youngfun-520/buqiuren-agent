"""
完整回归测试：11 个验收场景。
运行方式：python tests/regression_check.py
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.session import BuQiuRenSession


FORBIDDEN_TIMELINE_LABELS = {
    "semantic_match",
    "local_kb_lookup",
    "fallback_guidance",
    "intelligent_fallback",
    "classify_life_event",
}


CASES = [
    # (name, input, reply, expected_type, must_contain, must_not_contain, sources_min)
    # 正例
    ("A: 居住证→首次办理", "深圳居住证怎么办？", "首次办理",
     "service_card", ["居住证"], [], 1),
    ("B: 公积金→租房提取", "深圳公积金怎么提取？", "租房提取",
     "service_card", ["公积金"], ["居住证"], 1),
    ("C: 工资拖欠", "公司拖欠工资怎么办？", None,
     "service_card", ["工资", "欠薪", "拖欠"], ["居住证", "公积金"], 1),
    # 未入库事项
    ("D: 企业年金第一轮", "企业年金怎么办", None,
     "agent_task_guidance", ["企业年金"], [], 0),
    ("E: 企业年金→领取", "企业年金怎么办", "领取企业年金",
     "agent_task_guidance", [], [], 0),
    ("F: 企业年金→领取→已退休", "企业年金怎么办", "已退休",
     "agent_task_guidance", [], [], 0),
    ("G: 企业年金→领取→已退休→成都", "企业年金怎么办", "成都",
     "agent_task_guidance", [], [], 0),
    # 边界
    ("H: 今天吃什么", "今天吃什么？", None,
     "unsupported", [], [], 0),
    ("I: 给我写首诗", "给我写首诗", None,
     "unsupported", [], [], 0),
    ("J: 投诉物业", "我要投诉物业", None,
     "agent_task_guidance", [], ["工资拖欠"], 0),
    ("K: 医保报销", "医保报销怎么办？", None,
     "agent_task_guidance", [], ["居住证", "公积金", "工资拖欠"], 0),
]


def run_one(name: str, first_input: str, reply: str | None,
            expected_type: str, must_contain: list[str],
            must_not_contain: list[str], sources_min: int) -> tuple[bool, str]:
    sess = BuQiuRenSession()
    s1 = sess.ask(first_input, new_query=True)
    if reply:
        s2 = sess.choose(reply)
        resp = s2.get("final_response") or {}
    else:
        resp = s1.get("final_response") or {}

    answer_type = resp.get("answer_type") or ""
    card = resp.get("card") or {}
    card_title = card.get("title", "") or ""
    msg = resp.get("message", "") or ""
    sources = card.get("sources") or resp.get("sources") or []
    sources_count = len(sources) if isinstance(sources, list) else 0

    task_state = resp.get("task_state")
    task_stage = (task_state.get("stage") if task_state else "") or ""

    # 合并检查
    combined = card_title + msg

    ok = True
    reasons = []

    if answer_type != expected_type:
        ok = False
        reasons.append(f"type={answer_type} expected={expected_type}")

    # must_contain 是 OR 关系（至少包含一个）
    if must_contain and not any(kw in combined for kw in must_contain):
        ok = False
        reasons.append(f"none of {must_contain} in card+msg")

    for kw in must_not_contain:
        if kw in combined:
            ok = False
            reasons.append(f"forbidden '{kw}'")

    if sources_min > 0 and sources_count < sources_min:
        ok = False
        reasons.append(f"sources={sources_count} < {sources_min}")

    status = "PASS" if ok else "FAIL"
    detail = f"[{status}] {name}: type={answer_type}"
    if card_title:
        detail += f" card={card_title[:20]}"
    if sources_count > 0:
        detail += f" sources={sources_count}"
    if task_stage:
        detail += f" stage={task_stage}"
    if reasons:
        detail += f" ({'; '.join(reasons)})"

    return ok, detail


def _labels(resp: dict) -> list[str]:
    return [x.get("label", "") for x in resp.get("quick_replies_raw") or resp.get("quick_replies") or []]


def _timeline_text(resp: dict) -> str:
    return "\n".join(
        f"{x.get('label', '')} {x.get('message', '')}"
        for x in resp.get("progress_events") or []
    )


def run_task_flow_checks() -> tuple[bool, list[str]]:
    ok = True
    details: list[str] = []

    sess = BuQiuRenSession()
    s1 = sess.ask("企业年金怎么办", new_query=True)
    r1 = s1.get("final_response") or {}
    ts1 = r1.get("task_state") or {}
    labels1 = _labels(r1)
    if r1.get("answer_type") != "agent_task_guidance":
        ok = False; details.append("企业年金首轮 type 不是 agent_task_guidance")
    if r1.get("card") is not None:
        ok = False; details.append("企业年金首轮误返回 card")
    if "没有已核验的完整官方办事卡片" not in (r1.get("message") or ""):
        ok = False; details.append("企业年金首轮未说明无完整官方卡片")
    for expected in ["查询账户", "领取企业年金", "企业年金转移", "投诉或咨询"]:
        if expected not in labels1:
            ok = False; details.append(f"企业年金首轮缺 quick reply: {expected}")
    if ts1.get("topic") != "企业年金" or ts1.get("stage") != "confirm_goal":
        ok = False; details.append(f"企业年金首轮 task_state 异常: {ts1}")

    s2 = sess.choose("领取企业年金")
    r2 = s2.get("final_response") or {}
    ts2 = r2.get("task_state") or {}
    labels2 = _labels(r2)
    for expected in ["已退休", "已离职", "仍在职", "不确定"]:
        if expected not in labels2:
            ok = False; details.append(f"领取企业年金缺身份 quick reply: {expected}")
    if ts2.get("goal") != "claim" or ts2.get("stage") != "confirm_identity":
        ok = False; details.append(f"领取企业年金 task_state 异常: {ts2}")

    s3 = sess.choose("已退休")
    r3 = s3.get("final_response") or {}
    ts3 = r3.get("task_state") or {}
    labels3 = _labels(r3)
    for expected in ["北京", "上海", "广州", "成都", "杭州", "西安"]:
        if expected not in labels3:
            ok = False; details.append(f"已退休后缺城市 quick reply: {expected}")
    if ts3.get("identity_status") != "已退休" or ts3.get("stage") != "confirm_city":
        ok = False; details.append(f"已退休 task_state 异常: {ts3}")

    s4 = sess.choose("成都")
    r4 = s4.get("final_response") or {}
    ts4 = r4.get("task_state") or {}
    msg4 = r4.get("message") or ""
    if r4.get("answer_type") != "agent_task_guidance" or r4.get("card") is not None:
        ok = False; details.append("城市确认后应为 guidance 且无 card")
    if ts4.get("city") != "成都":
        ok = False; details.append(f"成都未记录到 task_state: {ts4}")
    for expected in [
        "当前未匹配到可核验官方完整指南",
        "咨询原单位人事或企业年金经办机构",
        "查询个人企业年金账户管理机构",
        "当地人社部门",
        "不是最终材料清单",
    ]:
        if expected not in msg4:
            ok = False; details.append(f"深圳方向性指引缺内容: {expected}")

    for resp in [r1, r2, r3, r4]:
        leaked = FORBIDDEN_TIMELINE_LABELS.intersection(_timeline_text(resp).split())
        if leaked:
            ok = False; details.append(f"timeline 泄漏开发节点: {sorted(leaked)}")

    for message in ["今天吃什么", "给我写首诗"]:
        sess2 = BuQiuRenSession()
        resp = (sess2.ask(message, new_query=True).get("final_response") or {})
        if resp.get("answer_type") != "unsupported" or resp.get("card") is not None or resp.get("task_state"):
            ok = False; details.append(f"{message} 应 unsupported、无任务、无 card")

    for message, required in [
        ("我要投诉物业", ["城市", "投诉对象", "投诉类型", "证据"]),
        ("医保报销怎么办", ["医保参保城市", "参保类型", "报销类型"]),
    ]:
        sess3 = BuQiuRenSession()
        resp = (sess3.ask(message, new_query=True).get("final_response") or {})
        text = resp.get("message") or ""
        if resp.get("answer_type") != "agent_task_guidance" or resp.get("card") is not None:
            ok = False; details.append(f"{message} 应为 guidance 且无 card")
        for expected in required:
            if expected not in text:
                ok = False; details.append(f"{message} 缺追问信息: {expected}")

    return ok, details


def run_all():
    print("=" * 70)
    print("buqiuren v0.3 任务式智能体 回归测试")
    print("=" * 70)

    all_ok = True
    for case in CASES:
        name, first_input, reply, exp_type, must, forbid, src_min = case
        ok, detail = run_one(name, first_input, reply, exp_type, must, forbid, src_min)
        if not ok:
            all_ok = False
        print(detail)

    flow_ok, flow_details = run_task_flow_checks()
    if not flow_ok:
        all_ok = False
    print("[{}] 企业年金/负向/追问体验专项".format("PASS" if flow_ok else "FAIL"))
    for detail in flow_details:
        print(f"    - {detail}")

    print("=" * 70)
    if all_ok:
        print("全部通过 OK")
    else:
        print("存在失败 FAIL")
    print("=" * 70)
    return all_ok


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
