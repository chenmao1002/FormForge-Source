"""Typed millimetre design contract. Unknown fields are rejected, never ignored."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Process(Record):
    type: Literal["3d_print", "cnc", "sheet_metal"] = "3d_print"
    material: str = Field(default="PETG", min_length=1, max_length=100)
    tolerance: float = Field(default=0.15, gt=0, le=1)
    min_wall: float = Field(default=1.2, gt=0, le=10)
    tool_diameter: float = Field(default=3, gt=0, le=30)
    sheet_thickness: float = Field(default=1.5, ge=0.5, le=6)
    bend_radius: float = Field(default=1.5, gt=0, le=20)
    k_factor: float = Field(default=0.4, ge=0.2, le=0.5)
    bend_tolerance_deg: float = Field(default=1, gt=0, le=5)


class Hole(Record):
    x: float
    y: float
    diameter: float = Field(ge=0.8, le=30)


class Board(Record):
    # Coordinates are in the imported STEP frame, not inferred from a bounding box.
    length: float = Field(gt=1, le=1000)
    width: float = Field(gt=1, le=1000)
    thickness: float = Field(default=1.6, gt=0, le=10)
    origin: tuple[float, float, float] = (0, 0, 0)
    top_height: float = Field(default=8, ge=0, le=200)
    bottom_height: float = Field(default=0, ge=0, le=100)
    step: str | None = None
    step_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    substrate_solid_index: int | None = Field(default=None, ge=0)
    mounting_holes: list[Hole] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def holes_in_board(self):
        for hole in self.mounting_holes:
            if abs(hole.x) + hole.diameter/2 > self.length/2 or abs(hole.y) + hole.diameter/2 > self.width/2:
                raise ValueError("Mounting hole lies outside the PCB envelope")
        return self


class Opening(Record):
    part: Literal["base", "lid"] = "base"
    face: Literal["left", "right", "front", "back", "top"] = "front"
    u: float = 0
    v: float = 8
    width: float = Field(gt=0, le=500)
    height: float = Field(gt=0, le=500)
    radius: float = Field(default=0.8, gt=0, le=30)
    clearance: float = Field(default=0.5, ge=0, le=10)
    label: str = Field(default="connector", max_length=80)


class Keepout(Record):
    name: str = Field(max_length=80)
    center: tuple[float, float, float]
    size: tuple[float, float, float]

    @model_validator(mode="after")
    def positive_size(self):
        if any(n <= 0 or n > 1000 for n in self.size):
            raise ValueError("Keepout dimensions must be positive and <=1000 mm")
        return self


class Design(Record):
    schema_version: Literal[1] = 1
    name: str = Field(default="mechanical-design", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["enclosure", "panel", "u_bracket"] = "enclosure"
    process: Process = Field(default_factory=Process)
    # Enclosure: INTERNAL cavity. Panel: blank dimensions. Bracket: floor tangent
    # length, width along bend, and flange height above the floor top.
    length: float = Field(default=90, ge=10, le=1000)
    width: float = Field(default=60, ge=10, le=1000)
    height: float = Field(default=22, ge=3, le=300)
    wall: float = Field(default=2.5, ge=0.5, le=20)
    floor: float = Field(default=2.5, ge=0.5, le=20)
    lid_thickness: float = Field(default=2.5, ge=0.5, le=20)
    corner_radius: float = Field(default=5, gt=0, le=100)
    edge_fillet: float = Field(default=0.6, gt=0, le=5)
    chamfer: float = Field(default=0.3, gt=0, le=3)
    fit_gap: float = Field(default=0.4, gt=0, le=5)
    seam_gap: float = Field(default=0.4, gt=0, le=5)
    board_clearance: float = Field(default=1, gt=0, le=30)
    standoff_height: float = Field(default=4, gt=0, le=30)
    screw_diameter: float = Field(default=3, ge=1.6, le=6)
    screw_pilot: float = Field(default=2.5, gt=0, le=6)
    board: Board | None = None
    openings: list[Opening] = Field(default_factory=list, max_length=40)
    holes: list[Hole] = Field(default_factory=list, max_length=100)
    keepouts: list[Keepout] = Field(default_factory=list, max_length=40)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    notes: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def engineering_constraints(self):
        p = self.process
        if self.kind == "enclosure" and (self.wall < p.min_wall or self.floor < p.min_wall or self.lid_thickness < p.min_wall):
            raise ValueError("wall/floor/lid below process min_wall")
        if self.kind == "panel" and p.type != "sheet_metal" and self.floor < p.min_wall:
            raise ValueError("Panel thickness below process min_wall")
        if self.kind == "enclosure" and (self.fit_gap <= 2*p.tolerance or self.seam_gap <= 2*p.tolerance):
            raise ValueError("fit_gap and seam_gap must exceed 2*tolerance for worst-case assembly")
        if self.screw_pilot >= self.screw_diameter:
            raise ValueError("screw_pilot must be smaller than screw_diameter")
        minimum_thickness = min(self.wall, self.floor, self.lid_thickness) if self.kind == "enclosure" else self.floor
        if p.type != "sheet_metal" and self.chamfer >= minimum_thickness/2:
            raise ValueError("Chamfer consumes more than half the part thickness")
        if p.type != "sheet_metal" and self.edge_fillet >= (min(self.floor, self.lid_thickness) if self.kind == "enclosure" else self.floor)/2:
            raise ValueError("Edge fillet too large for floor/lid")
        if self.corner_radius >= min(self.length, self.width)/3:
            raise ValueError("Corner radius too large for envelope")
        if p.type == "sheet_metal" and self.kind == "enclosure":
            raise ValueError("Sheet-metal supports panel/u_bracket; closed folded enclosure is not yet supported")
        if self.kind == "u_bracket" and p.type != "sheet_metal":
            raise ValueError("u_bracket requires sheet_metal process")
        if p.type == "sheet_metal":
            if p.sheet_thickness < p.min_wall or p.bend_radius < p.sheet_thickness:
                raise ValueError("Sheet thickness below min_wall or bend radius below thickness")
            if self.height <= p.bend_radius + 2*p.sheet_thickness:
                raise ValueError("Flange too short for bend tooling")
        if p.type == "cnc" and self.kind == "enclosure":
            if max(0.5, self.corner_radius-self.wall) < p.tool_diameter/2:
                raise ValueError("Internal cavity radius smaller than CNC cutter radius")
            if self.openings:
                self.notes = list(dict.fromkeys(self.notes + ["Side openings require additional CNC setups."]))
        if self.kind != "enclosure" and (self.board or self.openings or self.keepouts):
            raise ValueError("board/openings/keepouts are enclosure features")
        if self.kind == "enclosure" and self.holes:
            raise ValueError("Use board.mounting_holes/openings for enclosures")
        if self.board:
            b = self.board
            if self.length < b.length + 2*self.board_clearance or self.width < b.width + 2*self.board_clearance:
                raise ValueError("PCB exceeds cavity side clearance")
            if self.standoff_height < b.bottom_height + self.board_clearance:
                raise ValueError("Insufficient underside component clearance")
            if self.height < self.standoff_height+b.thickness+b.top_height+self.board_clearance+2:
                raise ValueError("PCB components exceed cavity/lid locating lip")
        for h in self.holes:
            margin = h.diameter/2 + max(p.sheet_thickness if p.type == "sheet_metal" else self.wall, p.tolerance)
            if abs(h.x)+margin >= self.length/2 or abs(h.y)+margin >= self.width/2:
                raise ValueError("Hole violates edge/bend clearance")
        for opening in self.openings:
            if (opening.part == "lid") != (opening.face == "top"):
                raise ValueError("Lid openings use top; base openings use side faces")
            w, h = opening.width+2*opening.clearance, opening.height+2*opening.clearance
            if opening.radius >= min(w, h)/2:
                raise ValueError("Opening fillet exceeds half its size")
            if p.type == "cnc" and opening.radius < p.tool_diameter/2:
                raise ValueError("Opening radius smaller than CNC cutter radius")
            if opening.face == "top":
                if abs(opening.u)+w/2 >= self.length/2-3 or abs(opening.v)+h/2 >= self.width/2-3:
                    raise ValueError("Top opening cuts locating lip")
            else:
                span = self.width if opening.face in ("left", "right") else self.length
                if abs(opening.u)+w/2 >= span/2-self.corner_radius or opening.v-h/2 <= self.floor+self.chamfer or opening.v+h/2 >= self.floor+self.height-2:
                    raise ValueError("Side opening violates floor/corner/seam clearance")
        return self
