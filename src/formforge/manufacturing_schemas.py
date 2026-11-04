"""Fixed-version diagnostic manufacturing data, separate from ordering."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class AssemblyExclusion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)


class FabricationChoices(BaseModel):
    model_config = ConfigDict(extra='forbid')
    material: str | None = Field(default=None, min_length=1, max_length=100)
    thicknessMm: float | None = Field(default=None, gt=0, le=10, allow_inf_nan=False)
    finish: str | None = Field(default=None, min_length=1, max_length=100)
    solderMask: str | None = Field(default=None, min_length=1, max_length=100)
    note: str = Field(default='', max_length=2000)


class PrepareManufacturing(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expectedProjectVersion: StrictInt = Field(ge=1)
    revisionId: str = Field(min_length=1, max_length=80)
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    inputScope: Literal['formforge', 'complete-native-import'] = Field(default='formforge',
        exclude_if=lambda value: value == 'formforge',
        description='Explicit complete native import uses the same-version schematic/board and all normal native/file checks; no design-review or supplier approval is inferred.')
    assemblyScope: Literal['pcb-only', 'smd-only', 'all-electrical']
    assembledSides: list[Literal['F.Cu', 'B.Cu']] = Field(max_length=2)
    exclusions: list[AssemblyExclusion] = Field(default_factory=list, max_length=1000)
    boardQuantity: StrictInt | None = Field(default=None, ge=1, le=1000000)
    fabrication: FabricationChoices = Field(default_factory=FabricationChoices)
    message: str = Field(min_length=1, max_length=2000)
    requestId: str = Field(min_length=1, max_length=160)
    timeoutSeconds: StrictInt = Field(default=180, ge=30, le=600)

    @model_validator(mode='after')
    def assembly(self):
        if len(set(self.assembledSides)) != len(self.assembledSides):
            raise ValueError('装配面不能重复。')
        if bool(self.assembledSides) != (self.assemblyScope != 'pcb-only'):
            raise ValueError('仅PCB不选装配面；装配资料须明确至少一个面。')
        if len({e.reference for e in self.exclusions}) != len(self.exclusions):
            raise ValueError('不装件位号不能重复。')
        return self
