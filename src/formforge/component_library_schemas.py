from typing import Literal
from pydantic import Field,model_validator
from .source_schemas import Strict


class LocalLibraryProbe(Strict):
    symbolId: str = Field(min_length=3,max_length=250)
    footprintId: str = Field(min_length=3,max_length=250)
    includeModels: bool = True


class PinPad(Strict):
    pin: str = Field(min_length=1,max_length=40)
    pad: str = Field(min_length=1,max_length=40)


class SupplyBinding(Strict):
    jobId: str = Field(pattern=r'^j_[0-9a-f]{16}$')
    expectedSnapshotSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    componentCode: str = Field(pattern=r'^C[1-9][0-9]{0,11}$')
    expectedMpn: str = Field(min_length=1,max_length=500)
    expectedManufacturer: str = Field(min_length=1,max_length=500)


class FreezeLocalLibrary(LocalLibraryProbe):
    expectedProjectVersion: int = Field(strict=True,ge=1)
    expectedInputsSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    pinMap: list[PinPad] = Field(min_length=1,max_length=500)
    modelPurpose: Literal['generic-visual','package-geometry-unverified']
    decisionNote: str = Field(min_length=1,max_length=2000)
    supply: SupplyBinding | None = None
    requestId: str = Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def unique_pins(self):
        if len({p.pin for p in self.pinMap})!=len(self.pinMap):raise ValueError('符号引脚不能重复映射。')
        return self
