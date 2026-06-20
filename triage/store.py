from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from triage.graph import build_graph
from triage.models import BrokerMetadata, TriageState, UploadedDocument, new_id, now_iso


@dataclass
class AppStore:
    graph: Any = field(default_factory=build_graph)
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, metadata: BrokerMetadata, docs: list[UploadedDocument]) -> dict[str, Any]:
        submission_id = new_id("sub")
        thread_id = new_id("thread")
        state: TriageState = {
            "submission_id": submission_id,
            "thread_id": thread_id,
            "created_at": now_iso(),
            "status": "running",
            "broker_metadata": metadata.model_dump(),
            "documents": [d.model_dump() for d in docs],
            "audit_events": [],
            "review_completed": False,
        }
        result = self.graph.invoke(state, self._config(thread_id))
        return self._save_result(submission_id, thread_id, result)

    def resume(self, submission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.submissions[submission_id]
        thread_id = current["thread_id"]
        result = self.graph.invoke(Command(resume=payload), self._config(thread_id))
        return self._save_result(submission_id, thread_id, result)

    def _save_result(self, submission_id: str, thread_id: str, result: Any) -> dict[str, Any]:
        if isinstance(result, dict) and "__interrupt__" in result:
            current = self.submissions.get(submission_id, {})
            interrupt_payload = result["__interrupt__"][0].value if result["__interrupt__"] else {}
            current.update(
                {
                    "submission_id": submission_id,
                    "thread_id": thread_id,
                    "status": "needs_review",
                    "interrupt": interrupt_payload,
                    "queue": "missing_info",
                }
            )
            self.submissions[submission_id] = current
            return current

        if not isinstance(result, dict):
            result = {"submission_id": submission_id, "thread_id": thread_id, "status": "error", "error": str(result)}

        routing = result.get("routing") or {}
        result["queue"] = routing.get("queue")
        self.submissions[submission_id] = result
        return result

    def get(self, submission_id: str) -> dict[str, Any] | None:
        return self.submissions.get(submission_id)

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            self.submissions.values(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

