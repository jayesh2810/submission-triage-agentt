from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from triage.graph import build_graph
from triage.models import BrokerMetadata, TriageState, UploadedDocument, new_id, now_iso

logger = logging.getLogger("triage.store")


@dataclass
class AppStore:
    graph: Any = field(default_factory=build_graph)
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, metadata: BrokerMetadata, docs: list[UploadedDocument]) -> dict[str, Any]:
        submission_id = new_id("sub")
        thread_id = new_id("thread")
        logger.info(
            "STORE start submission_id=%s thread_id=%s broker=%s docs=%s",
            submission_id,
            thread_id,
            metadata.broker_name or "-",
            len(docs),
        )
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
        logger.info("STORE invoke graph submission_id=%s thread_id=%s", submission_id, thread_id)
        result = self.graph.invoke(state, self._config(thread_id))
        logger.info("STORE graph returned submission_id=%s result_type=%s", submission_id, type(result).__name__)
        return self._save_result(submission_id, thread_id, result)

    def resume(self, submission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.submissions[submission_id]
        thread_id = current["thread_id"]
        logger.info(
            "STORE resume submission_id=%s thread_id=%s payload_keys=%s",
            submission_id,
            thread_id,
            sorted(payload.keys()),
        )
        result = self.graph.invoke(Command(resume=payload), self._config(thread_id))
        logger.info("STORE resume graph returned submission_id=%s result_type=%s", submission_id, type(result).__name__)
        return self._save_result(submission_id, thread_id, result)

    def _save_result(self, submission_id: str, thread_id: str, result: Any) -> dict[str, Any]:
        if isinstance(result, dict) and "__interrupt__" in result:
            current = self.submissions.get(submission_id, {})
            interrupt_payload = result["__interrupt__"][0].value if result["__interrupt__"] else {}
            logger.info(
                "STORE save interrupt submission_id=%s thread_id=%s reasons=%s",
                submission_id,
                thread_id,
                interrupt_payload.get("reasons") if isinstance(interrupt_payload, dict) else "-",
            )
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
            logger.info("STORE save complete submission_id=%s status=needs_review queue=missing_info", submission_id)
            return current

        if not isinstance(result, dict):
            logger.info("STORE save non_dict_result submission_id=%s result=%s", submission_id, result)
            result = {"submission_id": submission_id, "thread_id": thread_id, "status": "error", "error": str(result)}

        routing = result.get("routing") or {}
        result["queue"] = routing.get("queue")
        self.submissions[submission_id] = result
        logger.info(
            "STORE save complete submission_id=%s status=%s queue=%s count=%s",
            submission_id,
            result.get("status"),
            result.get("queue"),
            len(self.submissions),
        )
        return result

    def get(self, submission_id: str) -> dict[str, Any] | None:
        logger.info("STORE get submission_id=%s found=%s", submission_id, submission_id in self.submissions)
        return self.submissions.get(submission_id)

    def list(self) -> list[dict[str, Any]]:
        logger.info("STORE list count=%s", len(self.submissions))
        return sorted(
            self.submissions.values(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
