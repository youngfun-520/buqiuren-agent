from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.agent.schemas import ChatRequest, ChooseRequest
from app.agent.session import new_buqiuren_session, BuQiuRenSession
from app.core.config import CONFIG

router = APIRouter()
SESSION_STORE: dict[str, BuQiuRenSession] = {}

def _get_or_create_session(session_id: str | None = None) -> BuQiuRenSession:
    if session_id and session_id in SESSION_STORE:
        return SESSION_STORE[session_id]
    session = new_buqiuren_session()
    if session_id:
        session.session_id = session_id
    SESSION_STORE[session.session_id] = session
    return session


def _payload(session: BuQiuRenSession) -> dict:
    payload = session.payload()
    payload["session_id"] = session.session_id
    return payload


@router.post("/chat")
def chat(req: ChatRequest):
    session = _get_or_create_session(req.session_id)
    try:
        session.ask(req.message)
        return _payload(session)
    except Exception as exc:
        return {
            "session_id": session.session_id,
            "type": "error",
            "message": str(exc) if CONFIG.dev_mode else "系统处理失败，请稍后重试。",
            "timeline": [], "quick_replies": [], "card": None, "actions": [], "sources": [], "reasoning_steps": [],
        }


@router.post("/choose")
def choose(req: ChooseRequest):
    session = _get_or_create_session(req.session_id)
    try:
        session.choose(req.reply)
        return _payload(session)
    except Exception as exc:
        return {
            "session_id": session.session_id,
            "type": "error",
            "message": str(exc) if CONFIG.dev_mode else "系统处理失败，请稍后重试。",
            "timeline": [], "quick_replies": [], "card": None, "actions": [], "sources": [], "reasoning_steps": [],
        }


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming endpoint"""
    session = _get_or_create_session(req.session_id)
    try:
        return StreamingResponse(
            _sse_generator(session, req.message),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as exc:
        return {
            "session_id": session.session_id,
            "type": "error",
            "message": str(exc) if CONFIG.dev_mode else "系统处理失败",
        }

def _sse_generator(session: BuQiuRenSession, message: str):
    """Convert ask_stream events to SSE format"""
    try:
        for event in session.ask_stream(message):
            event_type = event.pop("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception as exc:
        err = {"session_id": session.session_id, "message": f"系统处理失败: {exc}" if CONFIG.dev_mode else "系统处理失败，请稍后重试。"}
        yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"
