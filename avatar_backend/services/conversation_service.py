from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avatar_backend.services.chat_service import ChatResult, run_chat
from avatar_backend.services.context_builder import ContextBuilder


@dataclass
class ConversationTurnRequest:
    session_id: str
    user_text: str
    context: dict[str, Any] | None = None


@dataclass
class EventFollowupRequest:
    session_id: str
    user_text: str
    event_type: str
    event_summary: str | None = None
    event_context: dict[str, Any] | None = None
    followup_prompt: str | None = None


class ConversationService:
    """Compatibility-first coordinator for text and voice conversation turns.

    This wraps the existing run_chat orchestration behind a higher-level service
    so voice and chat can converge on one coordinator before deeper V2 refactors.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._context_builder = ContextBuilder()

    async def handle_text_turn(self, turn: ConversationTurnRequest) -> ChatResult:
        return await self._run_turn(
            session_id=turn.session_id,
            user_text=self._context_builder.build_text_context(turn.user_text, turn.context),
        )

    async def handle_voice_turn(self, *, session_id: str, user_text: str) -> ChatResult:
        return await self._run_turn(session_id=session_id, user_text=user_text)

    async def handle_event_followup(self, turn: EventFollowupRequest) -> ChatResult:
        return await self._run_turn(
            session_id=turn.session_id,
            user_text=self._context_builder.build_event_followup_context(
                user_text=turn.user_text,
                event_type=turn.event_type,
                event_summary=turn.event_summary,
                event_context=turn.event_context,
                followup_prompt=turn.followup_prompt,
            ),
        )

    async def _run_turn(self, *, session_id: str, user_text: str) -> ChatResult:
        return await run_chat(
            session_id=session_id,
            user_text=user_text,
            llm=self._app.state.llm_service,
            sm=self._app.state.session_manager,
            ha=self._app.state.ha_proxy,
            decision_log=getattr(self._app.state, "decision_log", None),
            memory_service=getattr(self._app.state, "memory_service", None),
        )
