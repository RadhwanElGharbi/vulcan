from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA = "zeus.acquisition/2.0"
POLICY = "complete-extract/1.0"


def canonical(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def scientific_recipe(recipe: dict) -> dict:
    """Keep AOI file/container identity in provenance, outside content identity."""
    value = dict(recipe)
    if value.get('aoi_normalization'):
        value['aoi_normalization'] = {key: item for key, item in value['aoi_normalization'].items()
                                      if key not in ('inputs', 'entrypoint')}
    return value


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal['zeus.acquisition/2.0'] = SCHEMA


class Finding(Contract):
    id: str
    rule: str
    severity: Literal["block", "acknowledgement", "information"]
    message: str
    basis: str = "ZEUS policy: " + POLICY
    evidence: dict[str, Any] = Field(default_factory=dict)


class ProviderAssessment(Contract):
    disposition: Literal["qualified", "requires_acknowledgement", "unavailable", "excluded"]
    findings: list[Finding] = Field(default_factory=list)
    documentation: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=lambda: {"status": "pending", "verified_at": None})


class Product(Contract):
    id: str
    category: str
    name: str
    publisher: str
    version: str
    adapter: str
    endpoint: str
    kind: Literal["raster", "vector", "imagery", "climate", "population"]
    countries: list[str] = Field(default_factory=lambda: ["WLD"])
    units: str
    native_spacing: dict[str, Any] | None = None
    observation_period: dict[str, Any] | None = None
    release_date: str | None = None
    accuracy: dict[str, Any] | None = None
    license: str | None = None
    attribution: str
    semantics: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    credentials: list[str] = Field(default_factory=list)
    assessment: ProviderAssessment


class Selection(Contract):
    product_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class PlanRequest(Contract):
    selections: list[Selection] = Field(min_length=1, max_length=32)
    as_of: datetime | None = None


class Asset(Contract):
    id: str
    url: str
    role: str = "data"
    filename: str
    size: int | None = None
    expected_hash: str | None = None
    hash_algorithm: str = "sha256"
    etag: str | None = None
    last_modified: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannedSelection(Contract):
    id: str
    product: Product
    parameters: dict[str, Any]
    assets: list[Asset]
    discovery: list[dict[str, Any]] = Field(default_factory=list)
    recipe: dict[str, Any]
    findings: list[Finding] = Field(default_factory=list)


class FetchPlan(Contract):
    plan_id: str
    plan_hash: str
    project: str
    aoi: dict[str, Any]
    aoi_hash: str
    target_crs: str
    as_of: str
    runtime: dict[str, Any]
    policy: str = POLICY
    selections: list[PlannedSelection]
    findings: list[Finding] = Field(default_factory=list)
    estimates: dict[str, Any]


class ExecuteRequest(Contract):
    plan_id: str
    plan_hash: str
    acknowledged_findings: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AcceptRequest(Contract):
    report_hash: str
    acknowledged_findings: list[str]


class AcquisitionReceipt(Contract):
    asset_id: str
    source_url: str
    sha256: str
    size: int
    acquired_at: str
    headers: dict[str, str]
    blob: str


class ValidationReport(Contract):
    policy: str = POLICY
    results: list[dict[str, Any]]
    findings: list[Finding]
    report_hash: str


class DatasetManifest(Contract):
    generation: str
    project: str
    plan_hash: str
    report_hash: str
    datasets: list[dict[str, Any]]
    receipts: dict[str, list[AcquisitionReceipt]]
    approvals: list[dict[str, Any]]


def validate_acknowledgements(findings: list[Finding], acknowledged: list[str]) -> None:
    blockers = [f.message for f in findings if f.severity == "block"]
    if blockers:
        raise ValueError("Blocked: " + "; ".join(blockers))
    required = {f.id for f in findings if f.severity == "acknowledgement"}
    supplied = set(acknowledged)
    if supplied != required:
        raise ValueError(f"Acknowledge exactly the reported findings; missing={sorted(required-supplied)}, unknown={sorted(supplied-required)}")
