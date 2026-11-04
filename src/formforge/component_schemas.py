"""Exact channel identity queries; CAD and purchasing remain separate operations."""
from typing import Annotated
from pydantic import Field, field_validator
from .source_schemas import Strict


class ComponentQuery(Strict):
    expectedProjectVersion: int = Field(strict=True, ge=1)
    componentCodes: list[Annotated[str, Field(pattern=r'^C[1-9][0-9]{0,11}$')]] = Field(min_length=1, max_length=50)
    requestId: str = Field(min_length=1, max_length=160)

    @field_validator('componentCodes')
    @classmethod
    def unique_codes(cls, value):
        if len(set(value)) != len(value): raise ValueError('C编号不能重复。')
        return value
