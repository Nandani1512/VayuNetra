"""/agent/chat — LangGraph supervisor endpoint."""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter

from vayunetra.agents import graph

router = APIRouter(prefix="/agent", tags=["agent"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    intent: str
    response: str


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    result = graph.invoke({"user_message": req.message})
    return ChatResponse(intent=result.get("intent", "general"), response=result.get("response", ""))
