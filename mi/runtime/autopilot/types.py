from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AutopilotResult:
    status: str  # done|not_done|blocked
    thread_id: str
    project_dir: Path
    evidence_log_path: Path
    transcripts_dir: Path
    batches: int
    notes: str

    def render_text(self) -> str:
        lines = [
            f"status={self.status} batches={self.batches} thread_id={self.thread_id}",
            f"project_dir={self.project_dir}",
            f"evidence_log={self.evidence_log_path}",
            f"transcripts_dir={self.transcripts_dir}",
        ]
        if self.notes:
            lines.append(f"notes={self.notes}")
        return "\n".join(lines)
