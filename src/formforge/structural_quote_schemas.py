"""Source-bound printing quotations; public input cannot assert API authentication."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import Field, StrictInt, field_validator, model_validator
from .source_schemas import RegisteredSourceRef, Strict

MONEY=r'^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$'


class StructuralQuotePart(Strict):
    partId: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    expectedStepSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    quantity: StrictInt = Field(ge=1,le=1000000)
    material: str | None = Field(min_length=1,max_length=120)
    supplierProcess: str | None = Field(min_length=1,max_length=120)
    color: str | None = Field(min_length=1,max_length=120)
    surfaceFinish: str | None = Field(min_length=1,max_length=120)


class StructuralQuotePrices(Strict):
    currency: Literal['CNY']
    kind: Literal['estimate','provider-quote']
    partsAmount: str | None = Field(pattern=MONEY)
    shippingAmount: str | None = Field(pattern=MONEY)
    taxAmount: str | None = Field(pattern=MONEY)
    discountAmount: str | None = Field(pattern=MONEY)
    totalAmount: str | None = Field(pattern=MONEY)
    note: str = Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def sum_if_complete(self):
        values=[self.partsAmount,self.shippingAmount,self.taxAmount,self.discountAmount,self.totalAmount]
        if all(v is not None for v in values):
            parts,shipping,tax,discount,total=map(Decimal,values)
            if parts+shipping+tax-discount!=total:raise ValueError('完整金额组成与总额不一致。')
        return self


class ObservedStructuralQuotePart(StructuralQuotePart):
    fileNameObserved: str | None = Field(min_length=1,max_length=180)
    lineAmount: str | None = Field(pattern=MONEY)


class StructuralOrderIdentity(Strict):
    recordId: str = Field(pattern=r'^[A-Za-z0-9_-]{1,160}$')
    displayNumber: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')


class StructuralQuoteObservation(Strict):
    evidenceLevel: Literal['agent-observed-browser-ui']
    observedAt: datetime
    phase: Literal['status-only','uploaded','quoted','submitted','under-review','returned','cancelled']
    providerStatusLabel: str = Field(min_length=1,max_length=200)
    reviewStatus: Literal['unknown','submitted','under-review','returned','approved','cancelled']
    paymentStatus: Literal['unknown','unpaid','paid']
    paymentStatusLabel: str = Field(min_length=1,max_length=200)
    productionStatus: Literal['unknown','not-started','in-production','shipped']
    productionStatusLabel: str = Field(min_length=1,max_length=200)
    awaitingPaymentObserved: bool = False
    orderIdentity: StructuralOrderIdentity | None = None
    parts: list[ObservedStructuralQuotePart] = Field(min_length=1,max_length=128)
    prices: StructuralQuotePrices | None = None
    evidence: list[RegisteredSourceRef] = Field(min_length=1,max_length=8)
    note: str = Field(min_length=1,max_length=2000)

    @field_validator('observedAt')
    @classmethod
    def timezone_required(cls,value):
        if value.tzinfo is None or value.utcoffset() is None:raise ValueError('观察时间须带时区。')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if len({p.partId for p in self.parts})!=len(self.parts):raise ValueError('观察零件不得重复。')
        if len({p.sourceId for p in self.evidence})!=len(self.evidence):raise ValueError('观察证据不得重复。')
        if self.awaitingPaymentObserved and (not self.orderIdentity or self.reviewStatus!='approved' or self.paymentStatus!='unpaid'):
            raise ValueError('待付款须有真实订单身份、审核通过和未支付观察。')
        if self.phase in {'submitted','under-review','returned','cancelled'} and (not self.orderIdentity or self.reviewStatus!=self.phase):
            raise ValueError('订单阶段须有平台订单身份及相同审核状态。')
        if self.phase=='quoted':
            if not self.prices or all(getattr(self.prices,k) is None for k in ['partsAmount','shippingAmount','taxAmount','discountAmount','totalAmount']):
                raise ValueError('报价观察须有实际可见金额。')
            if any(p.material is None or p.supplierProcess is None or p.fileNameObserved is None for p in self.parts):raise ValueError('报价观察须明确逐件材料、供应商工艺及平台文件名。')
        if self.phase=='uploaded' and any(p.fileNameObserved is None for p in self.parts):raise ValueError('已上传观察须明确逐件平台文件名。')
        if self.prices and self.prices.partsAmount is not None and all(p.lineAmount is not None for p in self.parts):
            if sum(Decimal(p.lineAmount) for p in self.parts)!=Decimal(self.prices.partsAmount):raise ValueError('逐件金额与打印小计不一致。')
        return self


class CreateStructuralQuote(Strict):
    expectedProjectVersion: StrictInt = Field(ge=1)
    structuralJobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    expectedArchiveSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    dependencyPolicy: Literal['require-current','historical-quote']
    dependencyReason: str = Field(min_length=1,max_length=1000)
    provider: Literal['jlc-3dp']
    parts: list[StructuralQuotePart] = Field(min_length=1,max_length=128)
    note: str = Field(min_length=1,max_length=2000)
    requestId: str = Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def unique(self):
        if len({p.partId for p in self.parts})!=len(self.parts):raise ValueError('报价制作件不得重复。')
        return self


class AppendStructuralQuoteObservation(Strict):
    expectedQuoteVersion: StrictInt = Field(ge=1)
    observation: StructuralQuoteObservation
    requestId: str = Field(min_length=1,max_length=160)
