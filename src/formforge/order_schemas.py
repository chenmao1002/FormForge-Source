"""Local order association contracts. They never execute a supplier action."""
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .source_schemas import RegisteredSourceRef, Strict


class ProviderOrderIdentity(Strict):
    provider: Literal['jlc-pcb']
    recordId: str = Field(pattern=r'^[0-9a-f]{32}$')
    accessId: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    displayNumber: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


class OrderPrices(Strict):
    currency: Literal['CNY']
    kind: Literal['estimate', 'provider-quote']
    boardAmount: str | None = Field(default=None, pattern=r'^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$')
    shippingAmount: str | None = Field(default=None, pattern=r'^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$')
    totalAmount: str | None = Field(default=None, pattern=r'^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$')
    note: str = Field(min_length=1, max_length=1000)


class OrderObservation(Strict):
    evidenceLevel: Literal['agent-observed-browser-ui']
    observedAt: datetime
    reviewStatus: Literal['unknown', 'submitted', 'under-review', 'returned', 'approved', 'cancelled']
    providerStatusLabel: str = Field(min_length=1, max_length=200)
    paymentStatus: Literal['unknown', 'unpaid', 'paid']
    paymentStatusLabel: str = Field(min_length=1, max_length=200)
    productionStatus: Literal['unknown', 'not-started', 'in-production', 'shipped']
    productionStatusLabel: str = Field(min_length=1, max_length=200)
    awaitingPaymentObserved: bool = False
    manualOrderConfirmation: bool | None = None
    manualProductionProof: bool | None = None
    platformPreviewObserved: bool = False
    fileNameObserved: str | None = Field(default=None, min_length=1, max_length=180)
    prices: OrderPrices | None = None
    evidence: list[RegisteredSourceRef] = Field(min_length=1, max_length=8)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator('observedAt')
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('观察时间必须带时区。')
        return value

    @model_validator(mode='after')
    def coherent_observation(self):
        if self.awaitingPaymentObserved and (self.reviewStatus != 'approved' or self.paymentStatus != 'unpaid'):
            raise ValueError('待付款观察必须同时明确平台审核通过和未支付。')
        if len({x.sourceId for x in self.evidence}) != len(self.evidence):
            raise ValueError('观察证据不得重复。')
        return self


class OrderParameters(Strict):
    boardQuantity: int = Field(strict=True, ge=1, le=1000000)
    copperLayers: int = Field(strict=True, ge=1, le=32)
    widthMm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    heightMm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    thicknessMm: float = Field(gt=0, le=20, allow_inf_nan=False)
    assembly: Literal['none']
    note: str = Field(min_length=1, max_length=2000)


class AdoptExistingOrder(Strict):
    expectedProjectVersion: int = Field(strict=True, ge=1)
    manufacturingJobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    expectedGerberSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    identity: ProviderOrderIdentity
    parameters: OrderParameters
    observation: OrderObservation
    requestId: str = Field(min_length=1, max_length=160)


class AppendOrderObservation(Strict):
    expectedOrderVersion: int = Field(strict=True, ge=1)
    identity: ProviderOrderIdentity
    observation: OrderObservation
    requestId: str = Field(min_length=1, max_length=160)


class SmtOrderIdentity(Strict):
    provider: Literal['jlc-smt']
    customerCode: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    displayNumber: str = Field(pattern=r'^SMT[0-9]{6,30}$')


class SmtOrderPrices(Strict):
    currency: Literal['CNY']
    kind: Literal['estimate', 'provider-quote']
    amount: str = Field(pattern=r'^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$')
    amountScope: str = Field(min_length=1, max_length=300)
    shippingAmount: str | None = Field(default=None, pattern=r'^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$')
    note: str = Field(min_length=1, max_length=1000)


class SmtOrderObservation(OrderObservation):
    # Separate contract preserves normalization/idempotency of existing PCB requests.
    prices: SmtOrderPrices | None = None
    dfmStatus: Literal['unknown', 'pending', 'approved', 'rejected'] = 'unknown'
    directionStatus: Literal['unknown', 'pending', 'approved', 'rejected'] = 'unknown'
    manualDfmConfirmation: bool | None = None
    directionService: Literal['unknown', 'self-reviewed', 'engineer-assistance'] = 'unknown'
    bomMatchingStatus: Literal['unknown', 'partial', 'complete'] = 'unknown'
    matchedReferences: list[str] = Field(default_factory=list, max_length=10000)
    checkedReferences: list[str] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def matching_sets(self):
        for refs in [self.matchedReferences, self.checkedReferences]:
            if len(set(refs)) != len(refs):
                raise ValueError('SMT 位号不能重复。')
        if not set(self.checkedReferences) <= set(self.matchedReferences):
            raise ValueError('勾选位号必须属于已匹配位号。')
        if self.bomMatchingStatus == 'unknown' and self.matchedReferences:
            raise ValueError('提供已匹配位号时需要明确匹配状态。')
        return self


class SmtOrderParameters(Strict):
    assemblyQuantity: int = Field(strict=True, ge=1, le=1000000)
    assembledSides: list[Literal['top', 'bottom']] = Field(min_length=1, max_length=2)
    productType: Literal['standard', 'economy', 'unknown']
    note: str = Field(min_length=1, max_length=2000)

    @field_validator('assembledSides')
    @classmethod
    def unique_sides(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('贴装面不能重复。')
        return sorted(value)


class AdoptExistingSmtOrder(Strict):
    expectedProjectVersion: int = Field(strict=True, ge=1)
    manufacturingJobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    expectedGerberSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    procurementRevisionId: str = Field(pattern=r'^r_[0-9a-f]{16}$')
    expectedBomSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expectedCplSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    identity: SmtOrderIdentity
    pcbOrderNumber: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    parameters: SmtOrderParameters
    observation: SmtOrderObservation
    requestId: str = Field(min_length=1, max_length=160)


class AppendSmtOrderObservation(Strict):
    expectedOrderVersion: int = Field(strict=True, ge=1)
    identity: SmtOrderIdentity
    observation: SmtOrderObservation
    requestId: str = Field(min_length=1, max_length=160)
