from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _fake_state(message: str, context: dict | None = None) -> dict:
    context = context or {}
    missing = [] if context else [
        {
            "slot": "scenario",
            "question": "请选择办理场景",
            "options": [{"label": "首次办理", "value": "首次办理"}],
        }
    ]
    answer_type = "service_card" if context else "clarification_required"
    return {
        "raw_query": message,
        "user_context": context,
        "progress_events": [{"node": "understand_user", "status": "done", "message": "已理解用户问题"}],
        "final_response": {
            "answer_type": answer_type,
            "message": "已继续处理" if context else "请选择办理场景",
            "missing_slots": missing,
            "progress_events": [{"node": "understand_user", "status": "done", "message": "已理解用户问题"}],
            "card": {"title": "深圳居住证办理指南", "materials": []} if context else None,
        },
    }


def test_session_choose_continues_with_selected_quick_reply(monkeypatch):
    from app.agent import session as session_mod

    calls: list[tuple[str, dict]] = []

    def fake_run(raw_query, user_context=None):
        calls.append((raw_query, user_context or {}))
        return _fake_state(raw_query, user_context)

    monkeypatch.setattr(session_mod, "run_buqiuren", fake_run)

    sess = session_mod.BuQiuRenSession()
    first = sess.ask("深圳居住证怎么办", new_query=True)
    assert first["final_response"]["answer_type"] == "clarification_required"

    second = sess.choose("首次办理")
    assert second["final_response"]["answer_type"] == "service_card"
    assert calls[-1][0] == "深圳居住证怎么办"
    assert calls[-1][1]["scenario"] == "首次办理"


def test_chat_and_choose_routes_share_session_store(monkeypatch):
    from app.agent import session as session_mod
    from app.api import chat as chat_mod
    from app.main import app

    def fake_run(raw_query, user_context=None):
        return _fake_state(raw_query, user_context)

    monkeypatch.setattr(session_mod, "run_buqiuren", fake_run)
    monkeypatch.setattr(chat_mod, "SESSION_STORE", {})

    client = TestClient(app)
    chat_resp = client.post("/chat", json={"message": "深圳居住证怎么办"})
    assert chat_resp.status_code == 200
    chat_payload = chat_resp.json()
    assert chat_payload["type"] == "clarification_required"
    assert chat_payload["session_id"]

    choose_resp = client.post(
        "/choose",
        json={"session_id": chat_payload["session_id"], "reply": "首次办理"},
    )
    assert choose_resp.status_code == 200
    choose_payload = choose_resp.json()
    assert choose_payload["type"] == "service_card"
    assert choose_payload["session_id"] == chat_payload["session_id"]


def test_sse_generator_is_sync_iterable_for_streaming_response():
    from app.api import chat as chat_mod

    class FakeSession:
        session_id = "session-1"

        def ask_stream(self, message: str):
            yield {"type": "start", "session_id": self.session_id}
            yield {
                "type": "thinking",
                "node": "understand_user",
                "thinking": f"thinking about {message}",
                "status": "thinking",
            }

    assert not inspect.isasyncgenfunction(chat_mod._sse_generator)

    chunks = list(chat_mod._sse_generator(FakeSession(), "hello"))
    assert chunks[0].startswith("event: start\ndata: ")
    assert '"session_id": "session-1"' in chunks[0]
    assert chunks[1].startswith("event: thinking\ndata: ")
    assert '"thinking": "thinking about hello"' in chunks[1]


def test_stream_thinking_events_render_only_public_model_output(monkeypatch):
    from app.agent import session as session_mod

    def fake_stream(raw_query, user_context=None):
        yield {
            "type": "thinking",
            "node": "city_recognition",
            "thinking": '正在分析城市：The user says: "深圳居住证怎么办". Need infer city.',
            "status": "thinking",
        }
        yield {
            "type": "thinking",
            "node": "city_recognition_done",
            "thinking": 'raw thought that must not render',
            "public_status": "我先确认到你要查的是深圳的居住证办理。",
            "status": "thinking",
        }
        yield {"type": "complete", "state": _fake_state(raw_query, user_context)}

    monkeypatch.setattr(session_mod, "run_buqiuren_stream", fake_stream)

    events = list(session_mod.BuQiuRenSession().ask_stream("深圳居住证怎么办", new_query=True))
    thinking_events = [event for event in events if event["type"] == "thinking"]

    assert len(thinking_events) == 1
    assert thinking_events[0]["thinking"] == "我先确认到你要查的是深圳的居住证办理。"
    assert "The user" not in thinking_events[0]["thinking"]
    assert "Need infer" not in thinking_events[0]["thinking"]


def test_stream_thinking_has_no_preprogrammed_status_dictionary():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app" / "agent" / "session.py").read_text(encoding="utf-8")

    assert "SAFE_THINKING_MESSAGES" not in text
    assert "_safe_thinking_message" not in text
    assert "SLOT_OPTION_REGISTRY" not in text


def test_quick_replies_use_only_llm_options():
    from app.agent.session import build_quick_replies

    response_without_llm_options = {
        "answer_type": "clarification_required",
        "missing_slots": [{"slot": "permit_scenario", "question": "请选择办理场景"}],
    }
    response_with_llm_options = {
        "answer_type": "clarification_required",
        "missing_slots": [
            {
                "slot": "permit_scenario",
                "question": "请选择办理场景",
                "options": [
                    {"label": "首次办理", "value": "首次办理"},
                    {"label": "续签", "value": "续签"},
                    {"label": "还没想清楚", "value": "不确定"},
                ],
            }
        ],
    }

    assert build_quick_replies(response_without_llm_options) == []
    assert [x["label"] for x in build_quick_replies(response_with_llm_options)] == ["首次办理", "续签", "还没想清楚"]


def test_custom_clarification_reply_is_preserved_as_context(monkeypatch):
    from app.agent import session as session_mod

    calls: list[tuple[str, dict]] = []

    def fake_stream(raw_query, user_context=None):
        calls.append((raw_query, user_context or {}))
        yield {"type": "complete", "state": _fake_state(raw_query, user_context)}

    monkeypatch.setattr(session_mod, "run_buqiuren_stream", fake_stream)

    sess = session_mod.BuQiuRenSession()
    sess.raw_query = "深圳居住证怎么办"
    sess.last_state = {
        "final_response": {
            "answer_type": "clarification_required",
            "missing_slots": [{"slot": "identity_status", "question": "请补充身份状态", "options": []}],
        }
    }
    sess.last_clarification_response = sess.last_state["final_response"]

    list(sess.ask_stream("我是灵活就业人员"))

    assert calls[-1][0] == "深圳居住证怎么办"
    assert calls[-1][1]["identity_status"] == "我是灵活就业人员"
    assert calls[-1][1]["custom_reply"] == "我是灵活就业人员"


def test_custom_guidance_reply_continues_original_query(monkeypatch):
    from app.agent import session as session_mod

    calls: list[tuple[str, dict]] = []

    def fake_stream(raw_query, user_context=None):
        calls.append((raw_query, user_context or {}))
        yield {"type": "complete", "state": _fake_state(raw_query, user_context)}

    monkeypatch.setattr(session_mod, "run_buqiuren_stream", fake_stream)

    sess = session_mod.BuQiuRenSession()
    sess.raw_query = "企业年金怎么办"
    sess.last_state = {
        "final_response": {
            "answer_type": "guidance_fallback",
            "message": "请补充城市和身份",
            "quick_replies_raw": [],
        }
    }

    list(sess.ask_stream("北京，已退休"))

    assert calls[-1][0] == "企业年金怎么办"
    assert calls[-1][1]["custom_reply"] == "北京，已退休"


def test_fallback_prompt_includes_known_user_context_and_does_not_force_slot(monkeypatch):
    from app.agent import fallback as fallback_mod

    captured: dict[str, str] = {}

    def fake_invoke_json(prompt, default):
        captured["prompt"] = prompt
        return {
            "service_item_name": "社保缴费",
            "message": "已记录你的个人参保信息，继续补充缴纳险种即可。",
            "quick_replies": [
                {"label": "养老保险", "value": "养老保险", "slot": "insurance_type", "context": {"insurance_type": "养老保险"}}
            ],
            "reasoning_summary": "缺少险种",
        }

    monkeypatch.setattr(fallback_mod.llm, "invoke_json", fake_invoke_json)

    fallback_mod.build_intelligent_fallback(
        "辽源社保怎么交",
        {
            "understanding": {"service_goal": "社保缴费", "city": "辽源"},
            "user_context": {"fallback_action": "我是个人参保（外地户籍/灵活就业）"},
            "user_slots": {"identity_status": "灵活就业", "household_registration": "外地户籍"},
            "semantic_candidates": [],
        },
    )

    prompt = captured["prompt"]
    assert "已知用户补充信息" in prompt
    assert "我是个人参保（外地户籍/灵活就业）" in prompt
    assert "灵活就业" in prompt
    assert "不要重复追问已明确的信息" in prompt
    assert "不得说缺少城市" in prompt
    assert "公积金缴纳、缴存、提取、查询都属于公共服务" in prompt
    assert '"slot": "fallback_action"' not in prompt


def test_scope_guard_treats_housing_fund_payment_as_public_service_when_llm_misses(monkeypatch):
    from app.agent import fallback as fallback_mod

    def fake_invoke_json(prompt, default):
        return {"is_public_service": False, "reason": "模型误判"}

    monkeypatch.setattr(fallback_mod.llm, "invoke_json", fake_invoke_json)

    assert fallback_mod.is_public_service_query("吉林公积金怎么缴纳") is True


def test_public_service_scope_gets_second_llm_check_before_unsupported(monkeypatch):
    from app.agent import workflow as workflow_mod

    def fake_scope_check(message: str):
        assert message == "吉林公积金怎么缴纳"
        return True

    def fake_fallback(message: str, state: dict):
        return {
            "answer_type": "guidance_fallback",
            "message": "已按公共服务问题继续分析。",
            "progress_events": [],
            "quick_replies_raw": [],
        }

    monkeypatch.setattr(workflow_mod, "is_public_service_query", fake_scope_check)
    monkeypatch.setattr(workflow_mod, "build_intelligent_fallback", fake_fallback)

    state = workflow_mod.build_response_node({
        "raw_query": "吉林公积金怎么缴纳",
        "service_item_code": "unknown",
        "progress_events": [],
        "understanding": {"is_public_service": False, "service_goal": "未识别到有效办理事项"},
    })

    assert state["final_response"]["answer_type"] == "guidance_fallback"


def test_official_source_whitelist_supports_national_gov_domains():
    from app.agent.workflow import is_official_url, source_name_for_url

    assert is_official_url("https://www.gov.cn/fuwu/2026/example.htm")
    assert is_official_url("https://www.beijing.gov.cn/fuwu/bmfw/")
    assert source_name_for_url("https://www.beijing.gov.cn/fuwu/bmfw/") == "政府官方网站"


def test_search_queries_are_city_aware_without_defaulting_to_shenzhen():
    from app.agent.workflow import build_search_queries

    beijing_queries = build_search_queries("residence_permit_apply", "居住证办理", "北京")
    generic_queries = build_search_queries("housing_fund_withdraw", "公积金提取", "")

    assert all("北京" in q for q in beijing_queries[:4])
    assert not any("深圳" in q for q in generic_queries)
    assert any("site:gov.cn" in q for q in beijing_queries)


def test_city_specific_verified_record_is_not_reused_for_other_city():
    from app.agent.workflow import _city_matches

    assert _city_matches("北京", "全国")
    assert _city_matches("北京", "")
    assert _city_matches("北京", "北京")
    assert _city_matches("北京市", "北京")
    assert _city_matches("北京", "北京市")
    assert _city_matches("广西", "广西壮族自治区")
    assert _city_matches("宁夏", "宁夏回族自治区")
    assert _city_matches("新疆", "新疆维吾尔自治区")
    assert not _city_matches("北京", "深圳")
    assert not _city_matches("", "深圳")


def test_agent_decision_path_has_no_regex_or_keyword_fallbacks():
    root = Path(__file__).resolve().parents[1]
    checked = [
        root / "app" / "agent" / "workflow.py",
        root / "app" / "agent" / "understanding.py",
        root / "app" / "agent" / "fallback.py",
        root / "app" / "storage" / "guide_store.py",
    ]
    forbidden = [
        "import re",
        "re.search",
        "re.sub",
        "PUBLIC_SERVICE_PATTERNS",
        "NON_SERVICE_PATTERNS",
        "service_indicators",
        "_rule_similarity_fallback",
    ]
    offenders: dict[str, list[str]] = {}
    for path in checked:
        text = path.read_text(encoding="utf-8")
        hits = [token for token in forbidden if token in text]
        if hits:
            offenders[str(path.relative_to(root))] = hits

    assert offenders == {}, json.dumps(offenders, ensure_ascii=False, indent=2)


def test_safe_json_supports_json_arrays():
    from app.agent.llm import safe_json

    assert safe_json('[{"record_key":"a","score":0.9}]', []) == [{"record_key": "a", "score": 0.9}]
    assert safe_json('```json\n[{"record_key":"b","score":0.7}]\n```', []) == [{"record_key": "b", "score": 0.7}]
