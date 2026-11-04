"""Typed axial mechanical assemblies, vendor provenance and precision proposals.

All lengths are millimetres. Tolerances and geometric controls describe the
requested manufacturing result; nominal CAD does not certify a manufactured fit.
"""
import math
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .spec import Record
from .assembly_mass import PartMassProperties
from .assembly_kinematics import (
    AxisReference, CoaxialConstraint, TransmissionRelation, ClosedLoopConstraint,
    validate_kinematics,
)


class AxialSegment(Record):
    z0: float
    z1: float
    outer_diameter: float = Field(gt=0, le=2000)
    inner_diameter: float = Field(default=0, ge=0, le=2000)
    outer_diameter_end: float | None = Field(default=None, gt=0, le=2000)
    inner_diameter_end: float | None = Field(default=None, ge=0, le=2000)

    @model_validator(mode="after")
    def usable_annulus(self):
        if self.z1 <= self.z0 or self.inner_diameter >= self.outer_diameter:
            raise ValueError("Segment requires z1 > z0 and inner diameter < outer diameter")
        end_outer = self.outer_diameter if self.outer_diameter_end is None else self.outer_diameter_end
        end_inner = self.inner_diameter if self.inner_diameter_end is None else self.inner_diameter_end
        if end_inner >= end_outer:
            raise ValueError("Tapered segment requires inner diameter < outer diameter at both ends")
        return self


class BoxSegment(Record):
    """A rectangular envelope for a motor or a prismatic machined adapter."""
    z0: float
    z1: float
    length: float = Field(gt=0, le=2000)
    width: float = Field(gt=0, le=2000)
    center_xy: tuple[float, float] = (0, 0)
    angle_deg: float = 0

    @model_validator(mode="after")
    def positive_depth(self):
        if self.z1 <= self.z0:
            raise ValueError("Box segment requires z1 > z0")
        return self


class ExtrudedProfile(Record):
    """A simple closed XY polygon, added to stock or cut as a pocket.

    Vertices are actual local coordinates. Self-crossing and zero-area wires
    are rejected before OCCT; holes in a profile use separate cut polygons.
    """
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    points_xy: list[tuple[float, float]] = Field(min_length=3, max_length=256)
    z0: float
    z1: float
    operation: Literal["add", "cut"] = "add"

    @model_validator(mode="after")
    def usable_polygon(self):
        from .assembly_primitives import validate_polygon
        if self.z1 <= self.z0:
            raise ValueError("Extruded profile requires z1 > z0")
        validate_polygon(self.points_xy)
        return self


class EdgeTreatment(Record):
    """Explicit final edge finish; selectors refer to the local part frame."""
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["chamfer", "fillet"]
    size_mm: float = Field(gt=0, le=100)
    selector: Literal["all", "vertical", "top", "bottom"] = "all"


class LocalMirror(Record):
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    offset_mm: float = 0


class HolePattern(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    pcd: float = Field(default=0, ge=0, le=2000)
    count: int = Field(default=1, ge=1, le=128)
    angle_deg: float = 0
    origin_xy: tuple[float, float] = (0, 0)
    centers_xy: list[tuple[float, float]] | None = Field(default=None, min_length=1, max_length=128)
    linear_step_xy: tuple[float, float] | None = None
    diameter: float = Field(gt=0, le=200)
    z0: float
    z1: float
    kind: Literal["clearance", "tap"] = "clearance"
    operation: Literal["drill", "mill"] = "drill"
    thread: str | None = Field(default=None, max_length=80)
    counterbore_diameter: float | None = Field(default=None, gt=0, le=300)
    counterbore_depth: float | None = Field(default=None, gt=0)
    counterbore_side: Literal["top", "bottom"] = "top"
    countersink_diameter: float | None = Field(default=None, gt=0, le=300)
    countersink_angle_deg: float = Field(default=90, gt=0, lt=180)
    countersink_side: Literal["top", "bottom"] = "top"

    @model_validator(mode="after")
    def machining_definition(self):
        if self.z1 <= self.z0:
            raise ValueError("Hole requires z1 > z0")
        if self.centers_xy is not None:
            if self.pcd or self.linear_step_xy is not None or self.origin_xy != (0, 0) or self.angle_deg:
                raise ValueError("Explicit XY hole centres cannot also define a PCD or linear pattern")
            if "count" in self.model_fields_set and self.count != len(self.centers_xy):
                raise ValueError("Hole count must match explicit XY centres")
            self.count = len(self.centers_xy)
        elif self.linear_step_xy is not None:
            if self.pcd or self.angle_deg:
                raise ValueError("A linear hole pattern cannot also define a PCD or angle")
            if math.hypot(*self.linear_step_xy) < 1e-9:
                raise ValueError("Linear hole step cannot be zero")
        elif self.pcd == 0 and self.count != 1:
            raise ValueError("A central hole has count=1")
        centers = self.centers()
        diameter = self.counterbore_diameter or self.countersink_diameter or self.diameter
        if any(math.dist(a, b) <= diameter for i, a in enumerate(centers) for b in centers[i + 1:]):
            raise ValueError("Holes/counterbores in pattern overlap")
        if self.kind == "tap" and not (self.thread and self.thread.strip()):
            raise ValueError("Tap holes require an explicit thread callout")
        if self.kind == "clearance" and self.thread:
            raise ValueError("Clearance holes do not have a thread callout")
        if (self.counterbore_diameter is None) != (self.counterbore_depth is None):
            raise ValueError("Counterbore diameter and depth must be supplied together")
        if self.counterbore_diameter is not None:
            if self.counterbore_diameter <= self.diameter:
                raise ValueError("Counterbore must be wider than the drilled hole")
            if self.counterbore_depth > self.z1 - self.z0:
                raise ValueError("Counterbore depth exceeds hole interval")
        if self.countersink_diameter is not None:
            if self.counterbore_diameter is not None:
                raise ValueError("A hole cannot have both counterbore and countersink")
            if self.countersink_diameter <= self.diameter:
                raise ValueError("Countersink must be wider than the drilled hole")
        return self

    def countersink_depth(self):
        if self.countersink_diameter is None:
            return None
        return ((self.countersink_diameter - self.diameter) /
                (2 * math.tan(math.radians(self.countersink_angle_deg / 2))))

    def centers(self):
        if self.centers_xy is not None:
            return list(self.centers_xy)
        if self.linear_step_xy is not None:
            return [(self.origin_xy[0] + i * self.linear_step_xy[0],
                     self.origin_xy[1] + i * self.linear_step_xy[1]) for i in range(self.count)]
        return [(self.origin_xy[0] + self.pcd / 2 * math.cos(math.radians(self.angle_deg + 360 * i / self.count)),
                 self.origin_xy[1] + self.pcd / 2 * math.sin(math.radians(self.angle_deg + 360 * i / self.count)))
                for i in range(self.count)]

    def layout_description(self):
        if self.centers_xy is not None:
            return f"{self.count} explicit XY centres"
        if self.linear_step_xy is not None:
            return (f"{self.count} linear holes; start XY {self.origin_xy[0]:+.3f},{self.origin_xy[1]:+.3f}; "
                    f"step XY {self.linear_step_xy[0]:+.3f},{self.linear_step_xy[1]:+.3f}")
        return f"{self.count} equally spaced on PCD {self.pcd:.3f}"


class AxialKeyway(Record):
    """Capsule-ended axial keyseat cut with a radial end-mill setup."""
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    width: float = Field(gt=0, le=100)
    shaft_diameter: float = Field(gt=0, le=500)
    depth: float = Field(gt=0, le=100)
    z0: float
    z1: float
    angle_deg: float = 0
    side: Literal["external", "internal"] = "external"
    end_style: Literal["rounded", "square"] = "rounded"
    end_radius: float | None = Field(default=None, gt=0, le=50)

    @model_validator(mode="after")
    def millable_slot(self):
        if self.z1 <= self.z0:
            raise ValueError("Keyway requires z1 > z0")
        if self.end_style == "rounded" and self.z1 - self.z0 < self.width:
            raise ValueError("Keyway overall axial length must be at least cutter diameter/slot width")
        if self.width >= self.shaft_diameter or (self.side == "external" and self.depth >= self.shaft_diameter / 2):
            raise ValueError("Keyway width/depth can sever the shaft")
        if self.end_style == "square":
            if self.end_radius is not None:
                raise ValueError("Square-ended keyway has no end radius")
        elif self.end_radius is None:
            self.end_radius = self.width / 2
        elif abs(self.end_radius - self.width / 2) > 1e-9:
            raise ValueError("End-milled keyway end radius must equal half its width")
        return self


class RadialHole(Record):
    """Radial drill/tap setup; angle is CCW from +X about the Z axis."""
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    angle_deg: float = 0
    z: float
    diameter: float = Field(gt=0, le=200)
    inner_radius: float = Field(ge=0, le=2000)
    outer_radius: float = Field(gt=0, le=2000)
    kind: Literal["clearance", "tap"] = "clearance"
    thread: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def usable_drill(self):
        if self.outer_radius <= self.inner_radius:
            raise ValueError("Radial hole requires outer radius > inner radius")
        if self.kind == "tap" and not (self.thread and self.thread.strip()):
            raise ValueError("Radial tap requires an explicit thread callout")
        if self.kind == "clearance" and self.thread:
            raise ValueError("Radial clearance hole does not have a thread callout")
        return self


class Feature(Record):
    """A numbered manufacturing requirement linked to a local CAD location."""
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=160)
    kind: Literal["diameter", "length", "face", "hole_pattern"]
    nominal: float = Field(ge=0, le=3000)
    lower_tolerance: float | None = Field(default=None, ge=-10, le=10)
    upper_tolerance: float | None = Field(default=None, ge=-10, le=10)
    fit: str = Field(default="", max_length=40)
    surface: str = Field(default="", max_length=200)
    datum: str = Field(default="", max_length=80)
    gdt_mm: float | None = Field(default=None, gt=0, le=10)
    gdt_type: str = Field(default="", max_length=80)
    roughness_ra_um: float | None = Field(default=None, gt=0, le=100)
    anchor_local: tuple[float, float, float] = (0, 0, 0)
    view_hint: Literal["auto", "top", "front", "section"] = "auto"
    notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def explicit_limits(self):
        if (self.lower_tolerance is None) != (self.upper_tolerance is None):
            raise ValueError("Supply both lower and upper tolerance deviations")
        if self.lower_tolerance is not None:
            if self.lower_tolerance > self.upper_tolerance or self.nominal + self.lower_tolerance < 0:
                raise ValueError("Feature tolerance interval is invalid")
        if self.kind in ("diameter", "length") and self.nominal <= 0:
            raise ValueError("Diameter and length features must be positive")
        if self.fit and (self.kind != "diameter" or self.lower_tolerance is None):
            raise ValueError("A fit callout requires a diameter and explicit numeric deviations")
        if (self.gdt_mm is None) != (not bool(self.gdt_type)):
            raise ValueError("Geometric control requires both type and numeric value")
        return self

    def limits(self):
        if self.lower_tolerance is None:
            raise ValueError("Feature has no explicit numeric size limits: " + self.id)
        return self.nominal + self.lower_tolerance, self.nominal + self.upper_tolerance


class AxialPart(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    material: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["machined", "purchased", "fastener"] = "machined"
    sources: list[str] = Field(default_factory=list, max_length=20)
    model_provenance: Literal["manufacturer_step", "drawing_reconstruction", "custom_design"] = "custom_design"
    step: str | None = None
    step_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    step_solid_indices: list[int] | None = Field(default=None, min_length=1, max_length=500)
    segments: list[AxialSegment] = Field(default_factory=list, max_length=40)
    boxes: list[BoxSegment] = Field(default_factory=list, max_length=20)
    profiles: list[ExtrudedProfile] = Field(default_factory=list, max_length=40)
    edge_treatments: list[EdgeTreatment] = Field(default_factory=list, max_length=20)
    holes: list[HolePattern] = Field(default_factory=list, max_length=40)
    radial_holes: list[RadialHole] = Field(default_factory=list, max_length=40)
    keyways: list[AxialKeyway] = Field(default_factory=list, max_length=20)
    position: tuple[float, float, float] = (0, 0, 0)
    rotation_deg: tuple[float, float, float] = (0, 0, 0)
    rotation_axis: tuple[float, float, float] = (0, 0, 1)
    rotation_axis_angle_deg: float = 0
    mirror_of: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    mirror_plane: Literal["XY", "XZ", "YZ"] = "XY"
    mirror_offset_mm: float = 0
    step_mirrors: list[LocalMirror] = Field(default_factory=list, max_length=16)
    color: tuple[Annotated[float, Field(ge=0, le=1)],
                 Annotated[float, Field(ge=0, le=1)],
                 Annotated[float, Field(ge=0, le=1)]] | None = None
    mass_properties: PartMassProperties | None = None
    engineering_features: list[Feature] = Field(default_factory=list, max_length=80)
    notes: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def geometry_and_origin(self):
        if math.sqrt(sum(value * value for value in self.rotation_axis)) < 1e-9:
            raise ValueError("Part arbitrary rotation axis cannot be zero")
        if not self.mirror_of and self.material is None:
            raise ValueError("A part requires material")
        for url in self.sources:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("Supplier sources must be absolute HTTP(S) URLs")
        if self.step:
            if self.segments or self.boxes or self.profiles or self.holes or self.radial_holes or self.keyways or self.edge_treatments:
                raise ValueError("STEP geometry cannot also supply reconstruction primitives")
            if not self.step.lower().endswith((".step", ".stp")):
                raise ValueError("Imported geometry must be STEP/STP")
        elif not self.mirror_of and not (self.segments or self.boxes or any(p.operation == "add" for p in self.profiles)):
            raise ValueError("A part needs STEP geometry or reconstruction primitives")
        if self.step_mirrors and not self.step:
            raise ValueError("STEP mirrors require imported STEP geometry")
        if self.step_sha256 and not self.step:
            raise ValueError("STEP hash requires a STEP file")
        if self.step_solid_indices is not None:
            if not self.step or any(i < 0 for i in self.step_solid_indices):
                raise ValueError("STEP solid selection requires a file and non-negative indices")
            if len(self.step_solid_indices) != len(set(self.step_solid_indices)):
                raise ValueError("STEP solid indices must be unique")
        if not self.mirror_of and self.model_provenance == "manufacturer_step" and not (self.step and self.sources):
            raise ValueError("Manufacturer STEP provenance requires a file and source URL")
        if not self.mirror_of and self.model_provenance == "drawing_reconstruction" and not self.sources:
            raise ValueError("Drawing reconstruction requires its dimensional source URL")
        if not self.mirror_of and self.role == "purchased" and not self.sources:
            raise ValueError("Purchased parts require a supplier source URL")
        for values, label in (([h.name for h in self.holes], "hole pattern"),
                              ([h.name for h in self.radial_holes], "radial hole"),
                              ([k.name for k in self.keyways], "keyway"),
                              ([p.name for p in self.profiles], "profile"),
                              ([e.name for e in self.edge_treatments], "edge treatment"),
                              ([f.id for f in self.engineering_features], "feature")):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate " + label + " in part " + self.name)
        return self


class FitCheck(Record):
    """Proposed diametric fit interval; negative clearance denotes interference."""
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    shaft_part: str
    shaft_feature: str
    hole_part: str
    hole_feature: str
    min_clearance_mm: float = Field(default=0, ge=-10, le=10)
    max_clearance_mm: float | None = Field(default=None, ge=-10, le=10)

    @model_validator(mode="after")
    def ordered_clearance(self):
        if self.max_clearance_mm is not None and self.max_clearance_mm < self.min_clearance_mm:
            raise ValueError("Maximum fit clearance is below minimum")
        return self


class MotionGroup(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    parts: list[str] = Field(min_length=1, max_length=128)
    angles_deg: list[float] = Field(min_length=1, max_length=73)
    axis_point: tuple[float, float, float] = (0, 0, 0)
    axis_direction: tuple[float, float, float] = (0, 0, 1)
    axis_reference: AxisReference | None = None
    axis_tolerance_mm: float = Field(default=0.01, gt=0, le=10)

    @model_validator(mode="after")
    def usable_motion(self):
        if sum(c * c for c in self.axis_direction) < 1e-20:
            raise ValueError("Motion axis direction cannot be zero")
        if len(self.parts) != len(set(self.parts)) or len(self.angles_deg) != len(set(self.angles_deg)):
            raise ValueError("Motion parts and sample angles must be unique")
        return self


class InsertionCheck(Record):
    """Rigid subassembly approaching its final pose along a straight path.

    Only parts installed at this stage are stationary. Sampled nominal solids
    do not certify continuous sweeps, tool access or interference-fit forces.
    """
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    moving_parts: list[str] = Field(min_length=1, max_length=128)
    stationary_parts: list[str] = Field(min_length=1, max_length=128)
    approach_vector_mm: tuple[float, float, float]
    sample_count: int = Field(default=21, ge=3, le=101)
    sample_fractions: list[Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def usable_path(self):
        if sum(c * c for c in self.approach_vector_mm) < 1e-12:
            raise ValueError("Insertion approach vector cannot be zero")
        for group in (self.moving_parts, self.stationary_parts):
            if len(group) != len(set(group)):
                raise ValueError("Insertion part lists must be unique")
        if set(self.moving_parts) & set(self.stationary_parts):
            raise ValueError("Insertion moving and stationary parts must be disjoint")
        return self


class AssemblyDesign(Record):
    schema_version: Literal[1] = 1
    kind: Literal["rotary_assembly"] = "rotary_assembly"
    name: str = Field(default="rotary-joint", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    target_output_torque_nm: float = Field(gt=0, le=10000)
    ratio: float = Field(gt=0, le=1000)
    efficiency_assumption: float = Field(gt=0, le=1)
    motor_continuous_torque_nm: float = Field(gt=0, le=1000)
    reducer_continuous_torque_nm: float = Field(gt=0, le=10000)
    nominal_output_rpm: float = Field(default=20, ge=0, le=10000)
    reducer_rated_input_rpm: float | None = Field(default=None, gt=0, le=100000)
    torque_service_factor: float = Field(default=1, ge=1, le=10)
    general_tolerance_mm: float = Field(default=0.1, gt=0, le=1)
    parts: list[AxialPart] = Field(min_length=1, max_length=128)
    fits: list[FitCheck] = Field(default_factory=list, max_length=40)
    moving_groups: list[MotionGroup] = Field(default_factory=list, max_length=10)
    insertion_checks: list[InsertionCheck] = Field(default_factory=list, max_length=30)
    coaxial_constraints: list[CoaxialConstraint] = Field(default_factory=list, max_length=40)
    transmissions: list[TransmissionRelation] = Field(default_factory=list, max_length=40)
    closed_loops: list[ClosedLoopConstraint] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(default_factory=list, max_length=40)
    notes: list[str] = Field(default_factory=list, max_length=40)
    evidence_files: list[str] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def references(self):
        parts = {p.name: p for p in self.parts}
        if len(parts) != len(self.parts):
            raise ValueError("Assembly part names must be unique")
        from .assembly_primitives import resolve_mirrors
        self.parts = resolve_mirrors(self.parts)
        parts = {p.name: p for p in self.parts}
        for values, label in (([f.name for f in self.fits], "fit"),
                              ([g.name for g in self.moving_groups], "motion group"),
                              ([g.name for g in self.insertion_checks], "insertion check")):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate assembly " + label)
        for fit in self.fits:
            for part_name, feature_name in ((fit.shaft_part, fit.shaft_feature),
                                            (fit.hole_part, fit.hole_feature)):
                if part_name not in parts:
                    raise ValueError("Fit references an unknown part: " + part_name)
                matches = [f for f in parts[part_name].engineering_features if f.id == feature_name]
                if not matches or matches[0].kind != "diameter":
                    raise ValueError("Fit must reference an existing diameter feature")
                matches[0].limits()
            if fit.shaft_part == fit.hole_part:
                raise ValueError("Fit shaft and hole must be different parts")
        for group in self.moving_groups:
            if any(name not in parts for name in group.parts):
                raise ValueError("Motion group references an unknown part")
        for path in self.insertion_checks:
            if any(name not in parts for name in path.moving_parts + path.stationary_parts):
                raise ValueError("Insertion check references an unknown part")
        validate_kinematics(self)
        return self

    def resolved_parts(self):
        """Geometry aliases are expanded and checked during model validation."""
        return self.parts
