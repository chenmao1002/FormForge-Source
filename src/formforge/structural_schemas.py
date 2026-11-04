"""Explicit source-bound structural part dispositions; no provider order request."""
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from .source_schemas import Strict


class StructuralPart(Strict):
    partId: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    expectedPartHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    disposition: Literal['fabricate','provided','exclude']
    quantity: StrictInt | None = Field(ge=1,le=1000000)
    expectedStepSha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def explicit_counts(self):
        if (self.disposition=='exclude')!=(self.quantity is None):raise ValueError('制作/已有件须明确数量；排除件数量必须为null。')
        if (self.disposition=='fabricate')!=(self.expectedStepSha256 is not None):raise ValueError('仅制作件须明确原始STEP摘要。')
        return self


class PrepareStructural(Strict):
    expectedProjectVersion: StrictInt = Field(ge=1)
    revisionId: str = Field(pattern=r'^r_[a-f0-9]{16}$')
    expectedDesignSha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    dependencyPolicy: Literal['require-current','historical-quote']
    dependencyReason: str = Field(min_length=1,max_length=1000)
    parts: list[StructuralPart] = Field(min_length=1,max_length=128)
    process: Literal['3d-print']
    material: str | None = Field(min_length=1,max_length=120)
    requirements: str = Field(min_length=1,max_length=2000)
    message: str = Field(min_length=1,max_length=2000)
    timeoutSeconds: StrictInt = Field(default=180,ge=30,le=600)
    requestId: str = Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def unique_parts(self):
        if len({p.partId for p in self.parts})!=len(self.parts):raise ValueError('每个CAD零件须且仅须处置一次。')
        if not any(p.disposition=='fabricate' for p in self.parts):raise ValueError('至少选择一个实际制作件。')
        return self
