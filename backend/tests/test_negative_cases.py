"""
防误配回归测试：验证低阈值 semantic_match 不会将未知问题误配到已有的 service_card。
不修改业务逻辑，只报告误配。
"""

from __future__ import annotations

import sys
import os

# 确保从 backend 目录导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.session import BuQiuRenSession


FORBIDDEN_CARDS = ["居住证", "公积金", "工资拖欠"]


def check_misallocation(message: str, session: BuQiuRenSession) -> dict:
    """运行单次查询，返回误配检测结果"""
    state = session.ask(message, new_query=True)
    resp = state.get("final_response", {})
    answer_type = resp.get("answer_type", "")
    card = resp.get("card") or {}
    card_title = card.get("title", "") or ""
    msg = resp.get("message", "")
    task_state = resp.get("task_state")

    # 检查误配条件
    misallocated = (
        answer_type == "service_card"
        and any(kw in card_title for kw in FORBIDDEN_CARDS)
    )

    return {
        "message": message,
        "type": answer_type,
        "card_title": card_title,
        "has_card": bool(card),
        "has_task_state": bool(task_state),
        "misallocated": misallocated,
        "pass": not misallocated,
    }


def run_tests():
    """运行所有回归测试"""
    test_cases = [
        ("企业年金怎么办", "不能误配居住证/公积金/工资拖欠"),
        ("社保卡丢了怎么办", "不能误配居住证/公积金/工资拖欠"),
        ("医保报销怎么办", "不能误配居住证/公积金/工资拖欠"),
        ("办护照需要什么材料", "不能误配居住证/公积金/工资拖欠"),
        ("小孩上幼儿园怎么报名", "不能误配居住证/公积金/工资拖欠"),
        ("我要投诉物业", "不能返回工资拖欠"),
        ("今天吃什么", "unsupported，不返回任何 service_card"),
        ("给我写首诗", "unsupported，不建立任务，不返回 service_card"),
    ]

    print("=" * 70)
    print("防误配回归测试")
    print("=" * 70)

    all_passed = True
    for i, (input_text, requirement) in enumerate(test_cases, 1):
        session = BuQiuRenSession()
        result = check_misallocation(input_text, session)
        if input_text in {"今天吃什么", "给我写首诗"} and (
            result["type"] != "unsupported" or result["has_card"] or result["has_task_state"]
        ):
            result["pass"] = False
        status = "PASS" if result["pass"] else "FAIL"
        if not result["pass"]:
            all_passed = False

        print(f"\n[{i}] {input_text}")
        print(f"    要求: {requirement}")
        print(f"    type: {result['type']}")
        if result["card_title"]:
            print(f"    card: {result['card_title']}")
        print(f"    误配: {result['misallocated']}")
        print(f"    结果: {status}")

    print("\n" + "=" * 70)
    if all_passed:
        print("全部通过 OK")
    else:
        print("存在误配 FAIL")
    print("=" * 70)

    return all_passed


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
