"""Per-job dataset fetch report generation.

Produces a comprehensive JSON report at {project}/logs/dataset_fetch_report_{job_id}.json
with timing, download metrics, validation results, and summary statistics.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import _utc_iso


@dataclass
class CategoryMetrics:
    category: str
    status: str = "pending"
    source: Optional[str] = None
    fetch_method: Optional[str] = None
    started_at: Optional[float] = None
    stage_timings: Dict[str, float] = field(default_factory=dict)
    bytes_downloaded: int = 0
    tiles_downloaded: int = 0
    tiles_skipped: int = 0
    validation_status: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    checks_performed: List[str] = field(default_factory=list)
    raw_file_size: int = 0
    processed_file_size: int = 0
    raw_path: Optional[str] = None
    processed_path: Optional[str] = None
    error: Optional[str] = None

    def mark_stage_start(self, stage: str) -> None:
        self.stage_timings[f"{stage}_start"] = time.monotonic()

    def mark_stage_end(self, stage: str) -> None:
        start_key = f"{stage}_start"
        if start_key in self.stage_timings:
            elapsed = time.monotonic() - self.stage_timings[start_key]
            self.stage_timings[f"{stage}_seconds"] = round(elapsed, 2)

    def to_dict(self) -> Dict[str, Any]:
        timing = {}
        total = 0.0
        for key, val in self.stage_timings.items():
            if key.endswith("_seconds"):
                stage = key.replace("_seconds", "")
                timing[f"{stage}_seconds"] = val
                total += val
        timing["total_seconds"] = round(total, 2)

        result: Dict[str, Any] = {
            "status": self.status,
            "source": self.source,
            "fetch_method": self.fetch_method,
            "timing": timing,
            "download_metrics": {
                "bytes_downloaded": self.bytes_downloaded,
                "tiles_downloaded": self.tiles_downloaded,
                "tiles_skipped": self.tiles_skipped,
            },
            "validation": {
                "status": self.validation_status or "not_run",
                "errors": self.validation_errors,
                "warnings": self.validation_warnings,
                "checks_performed": self.checks_performed,
            },
            "output_files": {
                "raw": {"path": self.raw_path, "size_bytes": self.raw_file_size},
                "processed": {"path": self.processed_path, "size_bytes": self.processed_file_size},
            },
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class JobReport:
    job_id: str
    project: str
    protocol_version: str = "1.0"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    _start_mono: float = field(default_factory=time.monotonic, repr=False)
    categories: Dict[str, CategoryMetrics] = field(default_factory=dict)

    def get_or_create(self, category: str) -> CategoryMetrics:
        if category not in self.categories:
            self.categories[category] = CategoryMetrics(category=category)
        return self.categories[category]

    def finalize(self) -> Dict[str, Any]:
        duration = round(time.monotonic() - self._start_mono, 2)
        cat_dicts = {k: v.to_dict() for k, v in self.categories.items()}

        succeeded = sum(1 for v in self.categories.values() if v.status == "succeeded")
        failed = sum(1 for v in self.categories.values() if v.status == "failed")
        skipped = sum(1 for v in self.categories.values() if v.status == "skipped")
        total_bytes = sum(v.bytes_downloaded for v in self.categories.values())

        return {
            "job_id": self.job_id,
            "project": self.project,
            "protocol_version": self.protocol_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at or _utc_iso(),
            "duration_seconds": duration,
            "categories": cat_dicts,
            "summary": {
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "total_bytes_downloaded": total_bytes,
                "total_duration_seconds": duration,
            },
        }

    def write_to_project(self, project_path: Path) -> Path:
        logs_dir = project_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        report_path = logs_dir / f"dataset_fetch_report_{self.job_id}.json"
        payload = self.finalize()
        report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return report_path

