"""Mass and centre of mass from explicit, traceable engineering inputs only."""
from pydantic import Field, model_validator

from .spec import Record


class PartMassProperties(Record):
    density_kg_m3: float | None = Field(default=None, gt=0, le=100000)
    mass_kg: float | None = Field(default=None, gt=0, le=1000000)
    center_of_mass_local_mm: tuple[float, float, float] | None = None
    source: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def explicit_basis(self):
        if (self.density_kg_m3 is None) == (self.mass_kg is None):
            raise ValueError("Mass properties require exactly one explicit density_kg_m3 or mass_kg")
        if not self.source.strip():
            raise ValueError("Mass properties require a traceable source")
        if self.density_kg_m3 is not None and self.center_of_mass_local_mm is not None:
            raise ValueError("Uniform density uses the CAD volume centroid; an explicit centre requires mass_kg")
        return self


def record_mass_properties(design, model):
    from .assembly_primitives import place_point
    entries, unknown_mass, unknown_center = [], [], []
    known_mass = center_mass = 0.0
    moment = [0.0, 0.0, 0.0]
    for part in design.parts:
        basis = part.mass_properties
        shape = model.local_parts[part.name]
        entry = {"part": part.name, "cad_volume_mm3": shape.Volume(), "mass_kg": None,
                 "center_of_mass_mm": None, "source": None, "status": "unknown"}
        if basis is None:
            unknown_mass.append(part.name)
            unknown_center.append(part.name)
        else:
            entry["source"] = basis.source
            if basis.density_kg_m3 is not None:
                mass = shape.Volume() * basis.density_kg_m3 * 1e-9
                local_center = shape.Center().toTuple()
                entry["basis"] = "explicit uniform density times CAD volume; CAD volume centroid"
                entry["density_kg_m3"] = basis.density_kg_m3
            else:
                mass, local_center = basis.mass_kg, basis.center_of_mass_local_mm
                entry["basis"] = "explicit mass and optional measured/vendor local centre"
            entry["mass_kg"] = mass
            known_mass += mass
            if local_center is None:
                unknown_center.append(part.name)
                entry["status"] = "mass_only"
            else:
                center = place_point(part, local_center)
                entry["center_of_mass_mm"] = center
                entry["status"] = "complete"
                center_mass += mass
                for index, value in enumerate(center):
                    moment[index] += mass * value
        entries.append(entry)
        model.measures[part.name]["mass_properties"] = entry
    partial_center = [value / center_mass for value in moment] if center_mass else None
    complete = not unknown_mass and not unknown_center
    model.measures["mass_properties"] = {
        "status": "complete" if complete else ("partial" if known_mass else "unknown"),
        "parts": entries, "known_mass_kg": known_mass, "known_center_mass_kg": center_mass,
        "total_mass_kg": known_mass if not unknown_mass else None,
        "center_of_mass_mm": partial_center if complete else None,
        "partial_center_of_mass_mm": partial_center,
        "unknown_mass_parts": unknown_mass, "unknown_center_parts": unknown_center,
        "basis": "explicit sources only; densities are never inferred from material names; partial centre covers only parts with known mass and centre",
    }
