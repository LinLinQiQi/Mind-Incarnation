from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.schema_validate import validate_json_schema
from ..core.storage import now_rfc3339
from .mind_errors import MindCallError
from .mind_utils import append_jsonl as _append_jsonl
from .mind_utils import extract_json as _extract_json
from .mind_utils import new_mind_transcript_path
from .mind_utils import schema_path as _schema_path


def _extract_text_from_openai_like(payload: dict[str, Any]) -> str:
    # OpenAI-style chat.completions only.
    try:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                msg = c0.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    return msg["content"]
    except Exception:
        pass

    return ""


@dataclass(frozen=True)
class MindResult:
    obj: dict[str, Any]
    transcript_path: Path


class OpenAICompatibleMindProvider:
    """Mind provider using an OpenAI-compatible Chat Completions endpoint.

    This is best-effort across vendors: we rely on prompt + local schema validation.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        transcripts_dir: Path,
        timeout_s: int,
        max_retries: int,
        http_post_json: Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]] | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._transcripts_dir = transcripts_dir
        self._timeout_s = int(timeout_s)
        self._max_retries = int(max_retries)
        self._http_post_json = http_post_json or self._default_http_post_json

    def _default_http_post_json(self, url: str, body: dict[str, Any], headers: dict[str, str], timeout_s: int) -> dict[str, Any]:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            data = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise RuntimeError(f"http error status={e.code} body={data[:2000]}") from e
        except Exception as e:
            raise RuntimeError(f"http request failed: {e}") from e
        try:
            obj = json.loads(data)
        except Exception as e:
            raise RuntimeError(f"invalid JSON response: {data[:2000]}") from e
        if not isinstance(obj, dict):
            raise RuntimeError("response JSON was not an object")
        return obj

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> MindResult:
        if not self._model.strip():
            raise MindCallError(
                "openai_compatible mind provider requires mind.openai_compatible.model",
                schema_filename=schema_filename,
                tag=tag,
            )
        if not self._api_key.strip():
            raise MindCallError(
                "openai_compatible mind provider requires an API key (env or config)",
                schema_filename=schema_filename,
                tag=tag,
            )

        schema_path = _schema_path(schema_filename)
        schema_text = schema_path.read_text(encoding="utf-8")
        schema_obj = json.loads(schema_text)
        if not isinstance(schema_obj, dict):
            raise ValueError(f"schema {schema_filename} is not an object")

        transcript_path = new_mind_transcript_path(self._transcripts_dir, tag)

        url = self._base_url + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        system = (
            "You are MI (Mind Incarnation). "
            "Output MUST be a single JSON object matching the provided JSON Schema. "
            "No markdown, no code fences, no extra keys, no commentary."
        )
        user = (prompt or "").strip() + "\n\nJSON Schema (verbatim):\n" + schema_text.strip() + "\n"

        base_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        _append_jsonl(
            transcript_path,
            {
                "type": "mi.mind_transcript.header",
                "ts": now_rfc3339(),
                "provider": "openai_compatible",
                "base_url": self._base_url,
                "model": self._model,
                "schema": schema_filename,
            },
        )

        messages = list(base_messages)
        last_text = ""
        last_errors: list[str] = []

        try:
            for attempt in range(self._max_retries + 1):
                body = {
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0,
                }
                _append_jsonl(
                    transcript_path,
                    {
                        "type": "mi.mind_transcript.request",
                        "ts": now_rfc3339(),
                        "attempt": attempt,
                        "url": url,
                        "body": body,
                    },
                )

                t0 = time.time()
                payload = self._http_post_json(url, body, headers, self._timeout_s)
                dt_ms = int((time.time() - t0) * 1000)

                _append_jsonl(
                    transcript_path,
                    {
                        "type": "mi.mind_transcript.response",
                        "ts": now_rfc3339(),
                        "attempt": attempt,
                        "duration_ms": dt_ms,
                        "body": payload,
                    },
                )

                text = _extract_text_from_openai_like(payload).strip()
                last_text = text

                if not text:
                    last_errors = ["unsupported_response_shape: expected choices[0].message.content"]
                    obj = None
                else:
                    try:
                        obj = _extract_json(text)
                    except Exception as e:
                        last_errors = [f"json_parse: {e}"]
                        obj = None

                if isinstance(obj, dict):
                    errs = validate_json_schema(obj, schema_obj)
                    if not errs:
                        return MindResult(obj=obj, transcript_path=transcript_path)
                    last_errors = errs
                else:
                    if not last_errors:
                        last_errors = ["output was not a JSON object"]

                if attempt >= self._max_retries:
                    break

                # Ask the model to repair its output using explicit error messages.
                repair = (
                    "Your previous output did NOT match the JSON Schema.\n"
                    "Fix it and output ONLY the corrected JSON object.\n"
                    "Validation errors:\n- "
                    + "\n- ".join(last_errors[:20])
                    + "\n\nPrevious output:\n"
                    + (last_text[:4000] if last_text else "(empty)")
                )
                messages = list(base_messages) + [{"role": "user", "content": repair}]
        except MindCallError:
            raise
        except Exception as e:
            raise MindCallError(
                f"openai_compatible mind call failed: {e}",
                schema_filename=schema_filename,
                tag=tag,
                transcript_path=transcript_path,
                cause=e,
            )

        raise MindCallError(
            "mind provider output failed schema validation: " + "; ".join(last_errors[:10]),
            schema_filename=schema_filename,
            tag=tag,
            transcript_path=transcript_path,
        )
