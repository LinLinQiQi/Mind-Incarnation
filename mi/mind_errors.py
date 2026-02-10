from __future__ import annotations

from pathlib import Path


class MindCallError(RuntimeError):
    """Raised when a Mind provider call fails.

    We attach best-effort metadata so callers can log failures with pointers to
    any persisted mind transcript.
    """

    def __init__(
        self,
        message: str,
        *,
        schema_filename: str,
        tag: str,
        transcript_path: Path | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.schema_filename = schema_filename
        self.tag = tag
        self.transcript_path = transcript_path
        if cause is not None:
            self.__cause__ = cause

