"""Business-only browser commands. Credentials and arbitrary selectors are never input."""
from typing import Literal
from pydantic import Field, model_validator
from .source_schemas import Strict


class SupplierSessionRequest(Strict):
    action: Literal['open-login', 'check', 'save', 'close', 'forget']
    requestId: str = Field(min_length=1, max_length=160)


class SupplierMcpSessionRequest(SupplierSessionRequest):
    action: Literal['open-login', 'check', 'save', 'close']


class PcbExecutionParameters(Strict):
    boardQuantity: int = Field(strict=True, ge=1, le=1000000)
    copperLayers: int = Field(strict=True, ge=1, le=32)
    widthMm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    heightMm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    thicknessMm: float = Field(gt=0, le=20, allow_inf_nan=False)
    assembly: Literal['none', 'smt']
    note: str = Field(min_length=1, max_length=2000)


class SmtExecutionParameters(Strict):
    assemblyQuantity: int = Field(strict=True, ge=1, le=1000000)
    assembledSides: list[Literal['top', 'bottom']] = Field(min_length=1, max_length=2)
    productType: Literal['standard', 'economy', 'unknown']
    pcbOrderNumber: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    note: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def sides(self):
        if len(set(self.assembledSides)) != len(self.assembledSides):
            raise ValueError('贴装面不能重复。')
        self.assembledSides.sort()
        return self


class SupplierAttemptRequest(Strict):
    expectedProjectVersion: int = Field(strict=True, ge=1)
    manufacturingJobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    expectedGerberSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    procurementRevisionId: str | None = Field(default=None, pattern=r'^r_[0-9a-f]{16}$')
    expectedBomSha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    expectedCplSha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    provider: Literal['jlc-pcb', 'jlc-smt']
    parameters: PcbExecutionParameters | SmtExecutionParameters
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def provider_parameters(self):
        if self.provider=='jlc-pcb' and not isinstance(self.parameters,PcbExecutionParameters):
            raise ValueError('PCB执行需要PCB参数。')
        if self.provider=='jlc-smt' and (not isinstance(self.parameters,SmtExecutionParameters) or not all([
                self.procurementRevisionId,self.expectedBomSha256,self.expectedCplSha256])):
            raise ValueError('SMT执行需要采购版本、原始BOM/CPL摘要和SMT参数。')
        if self.provider=='jlc-pcb' and any([self.procurementRevisionId,self.expectedBomSha256,self.expectedCplSha256]):
            raise ValueError('PCB执行不接受SMT资料字段。')
        return self


class SupplierStepRequest(Strict):
    expectedAttemptVersion: int = Field(strict=True, ge=1)
    action: Literal['open', 'inspect', 'upload', 'configure', 'match', 'review', 'reconcile', 'submit']
    requestId: str = Field(min_length=1, max_length=160)
