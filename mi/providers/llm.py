from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_runner import run_codex_exec
from .mind_errors import MindCallError
from ..core.storage import now_rfc3339


def _schema_path(name: str) -> Path:
    # Schemas live under `mi/schemas` (shared across providers).
    return Path(__file__).resolve().parents[1] / "schemas" / name


def _extract_json(text: str) -> Any:
    text = text.strip()
    if not text:
        raise ValueError("empty model output")
    try:
        return json.loads(text)
    except Exception:
        pass
    # Best-effort recovery if the model wrapped JSON with extra text.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


@dataclass(frozen=True)
class MiPromptResult:
    obj: dict[str, Any]
    transcript_path: Path


class MiLlm:
    """
    V1 LLM interface: call Codex itself (non-interactive) with an output schema,
    then parse the agent_message as JSON.
    """

    def __init__(self, *, project_root: Path, transcripts_dir: Path):
        self._project_root = project_root
        self._transcripts_dir = transcripts_dir

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> MiPromptResult:
        schema_path = _schema_path(schema_filename)
        ts = now_rfc3339().replace(":", "").replace("-", "")
        transcript_path = self._transcripts_dir / "mind" / f"{ts}_{tag}.jsonl"
        try:
            result = run_codex_exec(
                prompt=prompt,
                project_root=self._project_root,
                transcript_path=transcript_path,
                full_auto=False,
                sandbox="read-only",
                output_schema_path=schema_path,
            )
        except Exception as e:
            raise MindCallError(
                f"codex_schema mind exec failed: {e}",
                schema_filename=schema_filename,
                tag=tag,
                transcript_path=transcript_path,
                cause=e,
            )

        try:
            msg = result.last_agent_message()
            obj = _extract_json(msg)
        except Exception as e:
            raise MindCallError(
                f"codex_schema mind output parse failed: {e}",
                schema_filename=schema_filename,
                tag=tag,
                transcript_path=transcript_path,
                cause=e,
            )

        if not isinstance(obj, dict):
            raise MindCallError(
                "schema output was not a JSON object",
                schema_filename=schema_filename,
                tag=tag,
                transcript_path=transcript_path,
            )

        return MiPromptResult(obj=obj, transcript_path=transcript_path)
