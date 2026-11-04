"""Geometry primitives extracted from the PCB project's enclosure worker.

Source and SHA256 are recorded in provenance/copied-files.json.
"""
import cadquery as cq


def box_between(x0, x1, y0, y1, z0, z1):
    return cq.Workplane("XY").box(x1-x0, y1-y0, z1-z0).translate(
        ((x0+x1)/2, (y0+y1)/2, (z0+z1)/2))


def bounds_overlap(a, b):
    return all(hi > lo + 1e-5 for lo, hi in (
        (max(a.xmin, b.xmin), min(a.xmax, b.xmax)),
        (max(a.ymin, b.ymin), min(a.ymax, b.ymax)),
        (max(a.zmin, b.zmin), min(a.zmax, b.zmax))))


def rounded_box(x0, x1, y0, y1, z0, z1, radius):
    result = box_between(x0, x1, y0, y1, z0, z1)
    return result.edges("|Z").fillet(radius) if radius else result


def collision(a, b):
    if not bounds_overlap(a.BoundingBox(), b.BoundingBox()):
        return 0.0
    return a.intersect(b).Volume()
