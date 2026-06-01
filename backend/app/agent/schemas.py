from typing import Any, Literal
from pydantic import BaseModel, Field

PayloadType = Literal["clarification_required", "service_card", "no_verified_guide", "unsupported", "guidance_fallback", "agent_task_guidance", "error", "unknown"]

class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="可选会话 ID")
    message: str = Field(..., min_length=1)

class ChooseRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    reply: str = Field(..., min_length=1)

class TimelineItem(BaseModel):
    label: str
    status: str = "done"
    message: str = ""

class ReasoningStep(BaseModel):
    label: str
    summary: str = ""

class QuickReply(BaseModel):
    label: str
    value: str
    slot: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

class Source(BaseModel):
    title: str = "官方来源"
    name: str = "官方来源"
    url: str = ""

class Action(BaseModel):
    type: str
    label: str
    url: str | None = None
    text: str | None = None

class TaskStatePayload(BaseModel):
    """任务状态 payload"""
    topic: str = ""
    domain: str = "其他"
    goal: str = "unknown"
    city: str | None = None
    identity_status: str | None = None
    subitem: str | None = None
    confirmed: dict[str, str] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    verified_guide_key: str | None = None
    verified_guide_status: str = "not_found"
    stage: str = "confirm_goal"
    sources: list[Source] = Field(default_factory=list)

class FrontendPayload(BaseModel):
    session_id: str
    type: PayloadType | str
    message: str
    timeline: list[TimelineItem] = Field(default_factory=list)
    quick_replies: list[QuickReply] = Field(default_factory=list)
    card: dict[str, Any] | None = None
    actions: list[Action] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    task_state: TaskStatePayload | None = None
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
