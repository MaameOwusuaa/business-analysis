"""AI augmentation workflow: context -> prompt -> Snwolley -> validation -> audit."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select

from ..auth import audit
from ..config import get_settings
from ..db import get_engine
from ..logging_setup import get_logger
from ..schema import ai_outputs
from .client import AgentError, MockSnwolleyClient, get_agent_client
from .context import build_context
from .guardrails import build_messages, validate_output

log = get_logger("agent.service")

TASK_LABELS = {
    "data_quality_brief": "Data-quality brief",
    "kpi_explainer": "KPI explanation",
    "model_explainer": "Model explanation",
    "retention_actions": "Recommended actions",
    "executive_summary": "Executive summary",
    "ask": "Question",
}


@dataclass
class AIResult:
    id: int
    task: str
    text: str
    status: str
    provider: str
    validation: dict
    note: str | None = None


def run_task(task: str, workspace_id: int, run_id: int | None = None, dataset_id: int | None = None,
             question: str | None = None, user_id: int | None = None) -> AIResult:
    settings = get_settings()
    context = build_context(task, workspace_id, run_id, dataset_id)
    messages = build_messages(task, context, question)
    prompt_hash = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
    note = None
    try:
        resp = get_agent_client(settings).complete(messages, task)
    except AgentError as exc:
        if not settings.snwolley_fallback_to_mock:
            _store(workspace_id, run_id, task, "snwolley", prompt_hash, context, question, None,
                   {"error": str(exc)}, "error", None, user_id)
            raise
        log.warning("snwolley unavailable, falling back to template mode task=%s reason=%s", task, exc)
        resp = MockSnwolleyClient().complete(messages, task)
        note = f"Live agent unavailable ({exc}); template output shown instead."
    validation = validate_output(resp.text, context, question)
    text = resp.text if validation.status != "blocked" else "_Output withheld: it failed the safety checks._"
    out_id = _store(workspace_id, run_id, task, resp.provider, prompt_hash, context, question, text,
                    validation.as_dict(), validation.status, resp.latency_ms, user_id)
    audit("ai.output", workspace_id, user_id, task=task, provider=resp.provider, status=validation.status, ai_output_id=out_id)
    return AIResult(out_id, task, text, validation.status, resp.provider, validation.as_dict(), note or resp.note)


def _store(workspace_id, run_id, task, provider, prompt_hash, context, question, text, validation, status,
           latency, user_id) -> int:
    with get_engine().begin() as conn:
        return conn.execute(ai_outputs.insert().values(
            workspace_id=workspace_id, run_id=run_id, task=task, provider=provider, prompt_hash=prompt_hash,
            context_json=json.dumps(context, default=str), question=question, output_text=text,
            validation=json.dumps(validation), status=status, latency_ms=latency, created_by=user_id,
        )).inserted_primary_key[0]


def history(workspace_id: int, task: str | None = None, limit: int = 50) -> pd.DataFrame:
    q = select(ai_outputs.c.id, ai_outputs.c.task, ai_outputs.c.provider, ai_outputs.c.status,
               ai_outputs.c.question, ai_outputs.c.output_text, ai_outputs.c.validation, ai_outputs.c.run_id,
               ai_outputs.c.created_at).where(ai_outputs.c.workspace_id == workspace_id)
    if task:
        q = q.where(ai_outputs.c.task == task)
    with get_engine().connect() as conn:
        rows = conn.execute(q.order_by(ai_outputs.c.id.desc()).limit(limit)).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def latest_output(workspace_id: int, task: str, run_id: int | None = None) -> dict | None:
    q = select(ai_outputs).where(ai_outputs.c.workspace_id == workspace_id, ai_outputs.c.task == task,
                                 ai_outputs.c.status != "error")
    if run_id:
        q = q.where(ai_outputs.c.run_id == run_id)
    with get_engine().connect() as conn:
        row = conn.execute(q.order_by(ai_outputs.c.id.desc())).mappings().first()
    if not row:
        return None
    d = dict(row)
    d["validation"] = json.loads(d["validation"] or "{}")
    return d
