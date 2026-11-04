"""Source-bound material decisions; supplier facts are declared, never certified."""
from decimal import Decimal
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class SourceRef(Strict):
    sourceId:str=Field(pattern=r'^s_[0-9a-f]{16}$')
    expectedSha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    expectedProvenanceSha256:str=Field(pattern=r'^[0-9a-f]{64}$')


class Citation(Strict):
    sourceId:str=Field(pattern=r'^s_[0-9a-f]{16}$')
    locator:str=Field(min_length=1,max_length=1000)


class PriceBreak(Strict):
    minimumQuantity:StrictInt=Field(ge=1,le=1000000000)
    unitPrice:Decimal=Field(ge=0,max_digits=20,decimal_places=8,allow_inf_nan=False)


class Offer(Strict):
    observedAt:AwareDatetime
    validUntil:AwareDatetime|None=None
    currency:str|None=Field(default=None,pattern=r'^[A-Z]{3}$')
    stockQuantity:StrictInt|None=Field(default=None,ge=0,le=1000000000)
    minimumOrderQuantity:StrictInt|None=Field(default=None,ge=1,le=1000000000)
    orderMultiple:StrictInt|None=Field(default=None,ge=1,le=1000000000)
    prices:list[PriceBreak]=Field(default_factory=list,max_length=100)
    evidence:Citation

    @model_validator(mode='after')
    def order(self):
        qs=[p.minimumQuantity for p in self.prices]
        if qs and self.currency is None:raise ValueError('有价格阶梯时须明确币种，不能推断。')
        if qs!=sorted(set(qs)):raise ValueError('报价阶梯起量必须严格递增。')
        if self.validUntil is not None and self.validUntil<self.observedAt:raise ValueError('报价有效期不能早于观察时间。')
        return self


class ElectricalFacts(Strict):
    resistanceOhm:Decimal|None=Field(default=None,ge=0,max_digits=25,decimal_places=18,allow_inf_nan=False)
    capacitanceF:Decimal|None=Field(default=None,gt=0,max_digits=25,decimal_places=18,allow_inf_nan=False)
    tolerancePercent:Decimal|None=Field(default=None,gt=0,le=100,max_digits=20,decimal_places=8,allow_inf_nan=False)
    ratedPowerW:Decimal|None=Field(default=None,gt=0,max_digits=20,decimal_places=8,allow_inf_nan=False)
    ratedVoltageV:Decimal|None=Field(default=None,gt=0,max_digits=20,decimal_places=8,allow_inf_nan=False)
    dielectric:str|None=Field(default=None,min_length=1,max_length=40)


class PadBinding(Strict):
    boardPad:str=Field(min_length=1,max_length=80)
    partTerminals:list[str]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def terminals(self):
        if any(not p or len(p)>80 or p!=p.strip() for p in self.partTerminals) or len(set(self.partTerminals))!=len(self.partTerminals):
            raise ValueError('器件端子需独立且不为空。')
        return self


class CataloguePart(Strict):
    schemaVersion:Literal[1]
    kind:Literal['procurement-part']
    category:Literal['resistor','capacitor','exact-device']
    manufacturer:str=Field(min_length=1,max_length=200)
    mpn:str=Field(min_length=1,max_length=200)
    supplier:str=Field(min_length=1,max_length=200)
    supplierPartNumber:str|None=Field(default=None,min_length=1,max_length=120)
    packageFootprint:str=Field(min_length=1,max_length=300)
    packageName:str=Field(min_length=1,max_length=120)
    designValues:list[str]=Field(default_factory=list,max_length=100)
    padBindings:list[PadBinding]=Field(min_length=1,max_length=1000)
    facts:ElectricalFacts=Field(default_factory=ElectricalFacts)
    documents:list[Citation]=Field(min_length=1,max_length=30)
    offer:Offer|None=None
    note:str=Field(min_length=1,max_length=4000)

    @model_validator(mode='after')
    def identity(self):
        pads=[p.boardPad for p in self.padBindings];terminals=[t for p in self.padBindings for t in p.partTerminals]
        if len(pads)!=len(set(pads)) or len(terminals)!=len(set(terminals)):raise ValueError('焊盘或器件端子不能重复映射。')
        if self.supplier.lower() in {'jlcpcb','lcsc'}:
            import re
            if self.supplierPartNumber and not re.fullmatch(r'C[0-9]{1,12}',self.supplierPartNumber):raise ValueError('JLCPCB/LCSC料号须为明确C编码。')
        return self


class ProcurementLine(Strict):
    references:list[str]=Field(min_length=1,max_length=1000)
    disposition:Literal['selected','customer-supplied','unresolved']
    partSourceId:str|None=Field(default=None,pattern=r'^s_[0-9a-f]{16}$')
    extraUnits:StrictInt=Field(default=0,ge=0,le=1000000000)
    reason:str=Field(min_length=1,max_length=4000)

    @model_validator(mode='after')
    def state(self):
        if any(not r or len(r)>80 or r!=r.strip() for r in self.references) or len(set(self.references))!=len(self.references):raise ValueError('位号需独立且不为空。')
        if (self.disposition=='unresolved')!=(self.partSourceId is None):raise ValueError('未确定项不带料号资料；选择/客供项须明确资料ID。')
        if self.disposition=='unresolved' and self.extraUnits:raise ValueError('未确定项不接受备料决定。')
        return self


class PlacementReview(Strict):
    reference:str=Field(min_length=1,max_length=80)
    deltaXmm:Decimal=Field(ge=-1000,le=1000,max_digits=15,decimal_places=6,allow_inf_nan=False)
    deltaYmm:Decimal=Field(ge=-1000,le=1000,max_digits=15,decimal_places=6,allow_inf_nan=False)
    rotationAdjustmentDegrees:Decimal=Field(ge=-360,le=360,max_digits=12,decimal_places=6,allow_inf_nan=False)
    evidence:Citation
    note:str=Field(min_length=1,max_length=2000)


class PlanProcurement(Strict):
    expectedProjectVersion:StrictInt=Field(ge=1)
    expectedManufacturingRevisionId:str|None=Field(max_length=80)
    manufacturingJobId:str=Field(min_length=1,max_length=80)
    expectedPackageSha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    mode:Literal['draft','reviewed']
    targetFormat:Literal['review-only','jlcpcb','jlc-smt-matching']
    boardQuantity:StrictInt|None=Field(default=None,ge=1,le=1000000)
    sources:list[SourceRef]=Field(default_factory=list,max_length=256)
    lines:list[ProcurementLine]=Field(min_length=1,max_length=1000)
    placements:list[PlacementReview]=Field(default_factory=list,max_length=10000)
    maxOfferAgeSeconds:StrictInt=Field(default=3600,ge=1,le=604800)
    reviewStatement:str=Field(min_length=1,max_length=4000)
    message:str=Field(min_length=1,max_length=2000)
    requestId:str=Field(min_length=1,max_length=160)
    timeoutSeconds:StrictInt=Field(default=120,ge=10,le=600)

    @model_validator(mode='after')
    def unique(self):
        ids=[s.sourceId for s in self.sources]
        if len(ids)!=len(set(ids)):raise ValueError('采购资料ID不能重复。')
        refs=[r for line in self.lines for r in line.references]
        if len(refs)!=len(set(refs)) or len(refs)>10000:raise ValueError('采购位号重复或超过10000项。')
        places=[p.reference for p in self.placements]
        if len(places)!=len(set(places)):raise ValueError('位号质心/方向审核不能重复。')
        return self
