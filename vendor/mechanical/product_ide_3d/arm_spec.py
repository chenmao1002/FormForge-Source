"""Strict, parent-aware articulated arm contract; lengths are millimetres.

Purchased geometry is explicitly a dimensional reconstruction or envelope,
never silently presented as manufacturer CAD. Servo stall torque is a screening
input, not a continuous rating.
"""
import math
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .spec import Record


Vector3 = tuple[float, float, float]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


def _unit(vector: Vector3, label: str) -> Vector3:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(magnitude) or magnitude < 1e-12:
        raise ValueError(label + " must be a finite, nonzero vector")
    return tuple(value / magnitude for value in vector)


class BoxPrimitive(Record):
    kind: Literal["box"] = "box"
    size: tuple[Annotated[float, Field(gt=0, le=2000)],
                Annotated[float, Field(gt=0, le=2000)],
                Annotated[float, Field(gt=0, le=2000)]]
    center: Vector3 = (0, 0, 0)


class CylinderPrimitive(Record):
    kind: Literal["cylinder"] = "cylinder"
    diameter: float = Field(gt=0, le=2000)
    length: float = Field(gt=0, le=2000)
    center: Vector3 = (0, 0, 0)
    axis: Vector3 = (0, 1, 0)

    @model_validator(mode="after")
    def normalized_axis(self):
        self.axis = _unit(self.axis, "Cylinder axis")
        return self


Primitive = Annotated[BoxPrimitive | CylinderPrimitive, Field(discriminator="kind")]


class ArmPart(Record):
    name: Name
    label: str = Field(default="", max_length=160)
    role: Literal["printed", "purchased", "fastener"] = "printed"
    material: str = Field(min_length=1, max_length=100)
    link: Name
    solids: list[Primitive] = Field(min_length=1, max_length=80)
    cuts: list[Primitive] = Field(default_factory=list, max_length=80)
    color: tuple[Annotated[float, Field(ge=0, le=1)],
                 Annotated[float, Field(ge=0, le=1)],
                 Annotated[float, Field(ge=0, le=1)]] = (0.75, 0.65, 0.2)
    density_g_cm3: float = Field(default=1.27, gt=0, le=30)
    mass_g: float | None = Field(default=None, gt=0, le=100000)
    sources: list[str] = Field(default_factory=list, max_length=20)
    model_provenance: Literal["custom_design", "drawing_reconstruction", "dimensioned_envelope"] = "custom_design"
    print_rotation_deg: Vector3 = (0, 0, 0)
    notes: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def traceable_geometry(self):
        for source in self.sources:
            parsed = urlsplit(source)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("Part sources must be absolute HTTP(S) URLs")
        if self.role == "purchased" and not self.sources:
            raise ValueError("Purchased parts require a supplier source URL")
        if self.model_provenance != "custom_design" and not self.sources:
            raise ValueError("Reconstructed/envelope geometry requires its source URL")
        if self.role == "printed" and self.mass_g is not None:
            raise ValueError("Printed mass is derived from actual solid volume; mass_g override is for purchased/fastener parts")
        return self


class ArmLink(Record):
    name: Name
    parent: Name | None = None
    translation: Vector3 = (0, 0, 0)
    axis: Vector3 = (0, 1, 0)
    angle_deg: float = Field(default=0, ge=-360, le=360)
    min_angle_deg: float = Field(default=-180, ge=-360, le=360)
    max_angle_deg: float = Field(default=180, ge=-360, le=360)
    servo_part: Name | None = None
    stall_torque_nm: float | None = Field(default=None, gt=0, le=10000)
    planning_torque_fraction: float = Field(default=0.25, gt=0, le=1)

    @model_validator(mode="after")
    def usable_joint(self):
        self.axis = _unit(self.axis, "Joint axis")
        if self.min_angle_deg > self.max_angle_deg:
            raise ValueError("Joint minimum angle exceeds maximum")
        if not self.min_angle_deg <= self.angle_deg <= self.max_angle_deg:
            raise ValueError("Default joint angle is outside joint limits")
        if (self.servo_part is None) != (self.stall_torque_nm is None):
            raise ValueError("Servo reference and stall torque must be provided together")
        if self.parent is None and (self.angle_deg != 0 or self.servo_part is not None):
            raise ValueError("Root link is static: no joint angle or servo")
        return self


class ArmPose(Record):
    name: Name
    angles_deg: dict[Name, float] = Field(default_factory=dict, max_length=64)


class ArmDesign(Record):
    schema_version: Literal[1] = 1
    kind: Literal["articulated_arm"] = "articulated_arm"
    name: Name = "compact-arm"
    parts: list[ArmPart] = Field(min_length=1, max_length=128)
    links: list[ArmLink] = Field(min_length=1, max_length=64)
    poses: list[ArmPose] = Field(default_factory=list, max_length=100)
    payload_g: float = Field(default=10, ge=0, le=100000)
    tool_link: Name
    tool_point_mm: Vector3 = (0, 0, 0)
    gravity_direction: Vector3 = (0, 0, -1)
    assumptions: list[str] = Field(default_factory=list, max_length=80)
    notes: list[str] = Field(default_factory=list, max_length=80)
    evidence_files: list[str] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def references(self):
        self.gravity_direction = _unit(self.gravity_direction, "Gravity direction")
        links = {link.name: link for link in self.links}
        parts = {part.name: part for part in self.parts}
        if len(links) != len(self.links) or len(parts) != len(self.parts):
            raise ValueError("Arm link and part names must be unique")
        if self.tool_link not in links:
            raise ValueError("Tool references an unknown link")
        for link in self.links:
            if link.parent is not None and link.parent not in links:
                raise ValueError("Joint references an unknown parent link")
            seen = set()
            cursor = link
            while cursor is not None:
                if cursor.name in seen:
                    raise ValueError("Arm link parent cycle is forbidden")
                seen.add(cursor.name)
                cursor = links.get(cursor.parent)
            if link.servo_part is not None:
                servo = parts.get(link.servo_part)
                if servo is None or servo.role != "purchased":
                    raise ValueError("Joint servo must reference a purchased part")
                if servo.link != link.parent:
                    raise ValueError("Servo body must belong to its joint's parent link")
        if sum(link.parent is None for link in self.links) != 1:
            raise ValueError("An articulated arm requires exactly one static root link")
        for part in self.parts:
            if part.link not in links:
                raise ValueError("Part references an unknown arm link")
        servo_names = [link.servo_part for link in self.links if link.servo_part]
        if len(servo_names) != len(set(servo_names)):
            raise ValueError("Each actuated joint requires its own servo part")
        pose_names = [pose.name for pose in self.poses]
        if len(pose_names) != len(set(pose_names)) or "default" in pose_names:
            raise ValueError("Pose names must be unique; default is reserved")
        for pose in self.poses:
            validate_angles(self, pose.angles_deg)
        return self


def validate_angles(design: ArmDesign, angles_deg: dict[str, float]) -> None:
    """Validate programmatic overrides as strictly as declared pose data."""
    links = {link.name: link for link in design.links}
    for name, angle in angles_deg.items():
        if name not in links:
            raise ValueError("Pose references an unknown link: " + name)
        link = links[name]
        if not math.isfinite(angle) or not link.min_angle_deg <= angle <= link.max_angle_deg:
            raise ValueError("Pose angle is outside joint limits: " + name)
        if link.parent is None and angle != 0:
            raise ValueError("Pose cannot rotate the static root link")
