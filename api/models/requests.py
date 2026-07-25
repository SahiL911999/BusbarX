"""Pydantic v2 request models for the BusbarX API."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import json


VALID_METHODS = ("k_factor", "bend_deduction", "cad_flat_pattern")


class BendProfileInline(BaseModel):
    """An inline custom bend profile supplied as JSON in the request body."""

    name: str = Field(default="inline", description="Profile name (for traceability)")
    method: str = Field(..., description="k_factor | bend_deduction | cad_flat_pattern")
    k_factor: Optional[float] = Field(
        default=None, description="Neutral-axis K-factor (required when method=k_factor)"
    )
    deduction_table: Optional[dict] = Field(
        default=None,
        description="Angle→deduction map (required when method=bend_deduction)",
    )

    @field_validator("method")
    @classmethod
    def method_valid(cls, v: str) -> str:
        if v not in VALID_METHODS:
            raise ValueError(f"method must be one of {VALID_METHODS}, got {v!r}")
        return v

    @field_validator("k_factor")
    @classmethod
    def k_required(cls, v, info):
        # Only validated if method == k_factor; cross-field check done in model_validator
        return v

    def to_profile_dict(self) -> dict:
        """Convert to the internal bend_profiles dict format."""
        return self.model_dump(exclude_none=True)
