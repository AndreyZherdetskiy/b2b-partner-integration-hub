"""Shared API schema helpers."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class AdminErrorResponse(BaseModel):
    """Standard admin API error payload."""

    detail: str = Field(..., description="Human-readable error message.")


class PaginationParams(BaseModel):
    """Offset pagination query parameters."""

    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of items to return (default 50, max 200).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip before returning results.",
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list wrapper."""

    items: list[T] = Field(..., description="Page of result items.")
    total: int = Field(..., description="Total number of matching items.")
    limit: int = Field(..., description="Applied page size limit.")
    offset: int = Field(..., description="Applied offset.")
