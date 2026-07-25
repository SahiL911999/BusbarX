"""
Pydantic v2 response models — strict mirror of the step-v2 JSON schema.
No raw `dict` leaks out of the API boundary.
"""
from __future__ import annotations

import base64
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field


# ── step-v2 leaf models ────────────────────────────────────────────────────────

class CoordinateSystem(BaseModel):
    origin: str
    x_axis: str
    y_axis: str
    angle_reference: str
    basis: str


class BendParameters(BaseModel):
    method: str
    value: Optional[Union[float, dict]] = None
    profile: Optional[str] = None
    configurable: bool = True


class FlatPattern(BaseModel):
    length_mm: Optional[float]
    width_mm: Optional[float]
    thickness_mm: Optional[float]


class FormedFootprint(BaseModel):
    length_mm: Optional[float]
    width_mm: Optional[float]


class PartInfo(BaseModel):
    is_bent: bool
    bend_count: int
    flat_pattern_status: str        # "computed" | "fallback"
    flat_pattern: FlatPattern
    formed_footprint: FormedFootprint


class SizeRound(BaseModel):
    diameter: float


class SizeRect(BaseModel):
    length: float
    width: float


class Feature(BaseModel):
    id: int
    type: str                       # round | obround | rectangle | square | irregular
    x_mm: float
    y_mm: float
    size_mm: Union[SizeRound, SizeRect]
    orientation_deg: Optional[float]
    source: str
    confidence: float
    in_bounds: bool


class Bend(BaseModel):
    id: int
    line_start_mm: List[float]
    line_end_mm: List[float]
    angle_deg: float
    radius_mm: float
    direction: Optional[str]        # "up" | "down" | None (fallback)
    affected_segment: Optional[str]
    source: str


class Validation(BaseModel):
    status: str                     # "pass" | "flagged"
    source: str
    features_total: int
    bends_total: int
    out_of_bounds_ids: List[int]
    flat_pattern_status: str
    notes: Optional[str]


class Metadata(BaseModel):
    material: Optional[str]
    plating: Optional[str]
    edge_condition: Optional[str]
    revision: Optional[str]
    manufacturing: Dict[str, Any]


class StepV2Result(BaseModel):
    """Full step-v2 extraction result — mirrors extract.to_json() output exactly."""
    part_number: str
    schema_version: str
    source: str
    units: str
    metadata: Metadata
    coordinate_system: CoordinateSystem
    bend_parameters: BendParameters
    part: PartInfo
    features: List[Feature]
    bends: List[Bend]
    validation: Validation


# ── job / response wrappers ────────────────────────────────────────────────────

class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ExtractionProgress(BaseModel):
    done: int
    total: int


class SingleExtractionResponse(BaseModel):
    job_id: str = Field(..., description="UUID for this extraction")
    status: JobStatus
    result: Optional[StepV2Result] = None
    visualization_b64: Optional[str] = Field(
        default=None,
        description="Base-64 encoded PNG of the flat-pattern visualization"
    )
    elapsed_s: Optional[float] = None
    error: Optional[str] = None


class PartResult(BaseModel):
    part: str
    ok: bool
    result: Optional[StepV2Result] = None
    visualization_b64: Optional[str] = None
    error: Optional[str] = None


class BatchSubmitResponse(BaseModel):
    job_id: str
    status: JobStatus
    file_count: int
    poll_url: str


class BatchStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: Optional[ExtractionProgress] = None
    results: Optional[List[PartResult]] = None
    elapsed_s: Optional[float] = None
    error: Optional[str] = None


class ProfileInfo(BaseModel):
    name: str
    method: str
    value: Optional[Union[float, dict]]


class ProfilesResponse(BaseModel):
    profiles: List[ProfileInfo]


class ProfileValidateResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
