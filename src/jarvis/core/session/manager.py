from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from collections import deque
import uuid


@dataclass
class SessionContext:
    session_id: str
    transport: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    history: deque = field(default_factory=lambda: deque(maxlen=20))
    metadata: dict = field(default_factory=dict)
    active_tool_chain: Optional[str] = None
    tool_chain_step: int = 0

    def add_message(self, role: str, content: str, tool_call_id: Optional[str] = None):
        self.history.append({
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
            "timestamp": datetime.now().isoformat(),
        })
        self.updated_at = datetime.now()

    def get_history(self) -> list[dict]:
        return list(self.history)


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, SessionContext] = {}

    def create_session(self, transport: str, user_id: str) -> SessionContext:
        session_id = f"{transport}_{uuid.uuid4().hex[:8]}"
        session = SessionContext(session_id=session_id, transport=transport, user_id=user_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[SessionContext]:
        return self._sessions.get(session_id)

    def get_or_create(self, transport: str, user_id: str, session_id: Optional[str] = None) -> SessionContext:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session(transport, user_id)

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def get_history(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        return session.get_history() if session else []

    def add_to_history(self, session_id: str, role: str, content: str, tool_call_id: Optional[str] = None):
        session = self._sessions.get(session_id)
        if session:
            session.add_message(role, content, tool_call_id)