"""Explicit planning intent; reports never change a board or its rules."""
from pydantic import Field, model_validator
from .schemas import StrictModel, BoardCoordinate, CopperLayer, PadEscapeTarget

Point = tuple[BoardCoordinate, BoardCoordinate]


class PlanningRegion(StrictModel):
    origin: Point
    width: float = Field(gt=0, le=1000, allow_inf_nan=False)
    height: float = Field(gt=0, le=1000, allow_inf_nan=False)


class FunctionalGroup(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    references: list[str] = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=1000)
    region: PlanningRegion | None = None
    anchorReference: str | None = None

    @model_validator(mode='after')
    def membership(self):
        if len(set(self.references)) != len(self.references):raise ValueError('功能组内位号不能重复。')
        if self.anchorReference and self.anchorReference not in self.references:raise ValueError('锚点必须属于功能组。')
        return self


class RoutingCorridor(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    net: str = Field(min_length=1, max_length=300)
    points: list[Point] = Field(min_length=2, max_length=8)
    width: float = Field(ge=.1, le=10, allow_inf_nan=False)
    clearance: float = Field(ge=.1, le=10, allow_inf_nan=False)
    layers: list[CopperLayer] = Field(min_length=1, max_length=4)
    source: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def path(self):
        if len(set(self.layers))!=len(self.layers):raise ValueError('通道铜层不能重复。')
        if any(a==b for a,b in zip(self.points,self.points[1:])):raise ValueError('通道不能包含零长度段。')
        return self


class PlanPcb(StrictModel):
    revisionId: str
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    groups: list[FunctionalGroup] = Field(default_factory=list, max_length=32)
    corridors: list[RoutingCorridor] = Field(default_factory=list, max_length=8)
    escapeTargets: list[PadEscapeTarget] = Field(default_factory=list, max_length=16)
    routeJobId: str | None = None
    probeInterfaces: bool | None = Field(default=None, description='Omit to skip interface trials for group-only requests; other requests keep interface diagnosis. Explicit true includes interface trials.')

    @model_validator(mode='after')
    def distinct(self):
        refs=[r for g in self.groups for r in g.references]
        if len(set(refs))!=len(refs):raise ValueError('一个器件不能同时属于多个功能组。')
        for rows in (self.groups,self.corridors):
            if len({r.name for r in rows})!=len(rows):raise ValueError('规划名称不能重复。')
        return self
