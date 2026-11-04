"""Portable data-only straight insertion contract. No guessed mating datums."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

Number=Annotated[float,Field(ge=-2000,le=2000,allow_inf_nan=False)]
Vector=tuple[Number,Number,Number]
class Record(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Box(Record):
    center:Vector
    size:tuple[Annotated[float,Field(gt=.01,le=2000)],Annotated[float,Field(gt=.01,le=2000)],Annotated[float,Field(gt=.01,le=2000)]]
class BoxGeometry(Box):kind:Literal['box']
class StepGeometry(Record):
    kind:Literal['step']
    step:str
    step_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
class MovingBody(Record):
    id:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,59}$')
    role:Literal['engaging','external']
    geometry:Annotated[BoxGeometry|StepGeometry,Field(discriminator='kind')]
class InsertionInterface(Record):
    id:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,39}$')
    footprintId:str
    openingIndex:int=Field(ge=0,le=39)
    face:Literal['left','right','front','back','top']
    evidencePath:str
    basis:Literal['engineering-envelope','declared-cad-and-datum']
    datumCADmm:Vector
    rotationDegrees:tuple[Literal[0,90,180,270],Literal[0,90,180,270],Literal[0,90,180,270]]=(0,0,0)
    datumLocalMm:Vector=(0,0,0)
    travel:float=Field(ge=1,le=200,allow_inf_nan=False)
    clearance:float=Field(ge=.05,le=5,allow_inf_nan=False)
    matingZone:Box
    bodies:list[MovingBody]=Field(min_length=1,max_length=12)
    note:str=Field(min_length=1,max_length=2000)
    @model_validator(mode='after')
    def unique(self):
        if len({b.id for b in self.bodies})!=len(self.bodies) or not any(b.role=='engaging' for b in self.bodies):raise ValueError('Unique body IDs and an explicit engaging body are required')
        if self.basis=='declared-cad-and-datum' and any(b.geometry.kind!='step' for b in self.bodies):raise ValueError('CAD basis cannot silently include engineering boxes')
        return self
