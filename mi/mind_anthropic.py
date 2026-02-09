from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .schema_validate import validate_json_schema
from .storage import ensure_dir, now_rfc3339


def _schema_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "schemas" / name


def _extract_text_from_anthropic(payload: dict[str, Any]) -> str:
    # Messages API style
    try:
        content = payload.get("content")
        if isinstance(content, list) and content:
            parts: list[str] = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text" and isinstance(blk.get("text"), str):
                    parts.append(blk["text"])
            if parts:
                return "\n".join(parts)
    except Exception:
        pass

    # Legacy completions API style (best-effort)
    try:
        if isinstance(payload.get("completion"), str):
            return payload["completion"]
    except Exception:
        pass

    return ""


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model output")
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


@dataclass(frozen=True)
class MindResult:
    obj: dict[str, Any]
    transcript_path: Path


class AnthropicMindProvider:
    """Mind provider using Anthropic's Messages API.

    We rely on prompt + local schema validation (best-effort across model versions).
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
        anthropic_version: str,
        max_tokens: int,
        http_post_json: Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]] | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._transcripts_dir = transcripts_dir
        self._timeout_s = int(timeout_s)
        self._max_retries = int(max_retries)
        self._anthropic_version = str(anthropic_version or "2023-06-01")
        self._max_tokens = int(max_tokens or 2048)
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
            raise ValueError("anthropic mind provider requires mind.anthropic.model")
        if not self._api_key.strip():
            raise ValueError("anthropic mind provider requires an API key (env or config)")

        schema_path = _schema_path(schema_filename)
        schema_text = schema_path.read_text(encoding="utf-8")
        schema_obj = json.loads(schema_text)
        if not isinstance(schema_obj, dict):
            raise ValueError(f"schema {schema_filename} is not an object")

        ts = now_rfc3339().replace(":", "").replace("-", "")
        transcript_path = self._transcripts_dir / "mind" / f"{ts}_{tag}.jsonl"

        url = self._base_url + "/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._anthropic_version,
        }

        system = (
            "You are MI (Mind Incarnation). "
            "Output MUST be a single JSON object matching the provided JSON Schema. "
            "No markdown, no code fences, no extra keys, no commentary."
        )
        user = (prompt or "").strip() + "\n\nJSON Schema (verbatim):\n" + schema_text.strip() + "\n"

        _append_jsonl(
            transcript_path,
            {
                "type": "mi.mind_transcript.header",
                "ts": now_rfc3339(),
                "provider": "anthropic",
                "base_url": self._base_url,
                "model": self._model,
                "schema": schema_filename,
            },
        )

        last_text = ""
        last_errors: list[str] = []

        for attempt in range(self._max_retries + 1):
            if attempt == 0:
                messages = [{"role": "user", "content": user}]
            else:
                repair = (
                    "Your previous output did NOT match the JSON Schema.\n"
                    "Fix it and output ONLY the corrected JSON object.\n"
                    "Validation errors:\n- "
                    + "\n- ".join(last_errors[:20])
                    + "\n\nPrevious output:\n"
                    + (last_text[:4000] if last_text else "(empty)")
                )
                messages = [
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": last_text[:4000] if last_text else ""},
                    {"role": "user", "content": repair},
                ]

            body = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": messages,
                "system": system,
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

            text = _extract_text_from_anthropic(payload).strip()
            last_text = text

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

        raise ValueError("mind provider output failed schema validation: " + "; ".join(last_errors[:10]))

