"""
ArchivistProxy — compiled-inference proxy layer.

Every inference call routes through intercept():
  1. graph pre-check (always-miss stub until knowledge graph is populated)
  2. on miss: forward to dispatch_fn, fan out answer → caller + payload → pipeline
  3. log PROXY_INTERCEPT at INFO per call

Module-level register_proxy / clear_proxy let ArchivistShim wire this into
all existing InferenceDevice instances without touching each call site.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from devices.archivist.learning_pipeline import LearningPipeline

if TYPE_CHECKING:
    from devices.inference.shim import InferenceRequest, InferenceResponse

log = logging.getLogger(__name__)


class ArchivistProxy:
    """
    Proxy layer that sits in front of InferenceDevice.dispatch().

    Graph pre-check is a stub (always-miss) until T-inference-learning-pipeline
    populates the knowledge graph.
    """

    def __init__(self, pipeline: LearningPipeline | None = None) -> None:
        self._pipeline = pipeline or LearningPipeline()

    def intercept(
        self,
        request: "InferenceRequest",
        dispatch_fn: "callable",
    ) -> "InferenceResponse":
        """
        Intercept an inference call.

        graph pre-check → on miss → dispatch_fn → fan-out learning payload
        """
        graph_hit = self._graph_check(request)
        log.info(
            "PROXY_INTERCEPT|ticket=%s|caller=%s|graph_hit=%s",
            request.coa_id or "",
            request.agent_id or "",
            "true" if graph_hit else "false",
        )

        if graph_hit:
            return self._graph_response(request)

        response = dispatch_fn(request)
        self._enqueue_learning(request, response)
        return response

    def _graph_check(self, request: "InferenceRequest") -> bool:
        """Check knowledge graph for a compiled answer. Stub — always miss."""
        return False

    def _graph_response(self, request: "InferenceRequest") -> "InferenceResponse":
        """Return a compiled graph answer. Unreachable until _graph_check returns True."""
        raise NotImplementedError("graph responses not yet implemented")

    def _enqueue_learning(
        self,
        request: "InferenceRequest",
        response: "InferenceResponse",
    ) -> None:
        """Fan out: enqueue (query, response) pair for overnight pipeline processing."""
        self._pipeline.enqueue(
            {
                "query_text": _last_user_message(request),
                "response_text": response.text,
                "model": response.model,
                "caller": request.agent_id or "",
                "session_id": request.session_id or "",
                "task_class": request.task_class,
            }
        )

    @property
    def pipeline(self) -> LearningPipeline:
        return self._pipeline


def _last_user_message(request: "InferenceRequest") -> str:
    """Extract the last user message from the request for learning storage."""
    for msg in reversed(request.messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content[:2000] if isinstance(content, str) else str(content)[:2000]
    return ""
