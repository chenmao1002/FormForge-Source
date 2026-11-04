"""Read-only migration from PCB_mode; see docs/migrations/M4-routing-kernels.json.
Local reuse authorized by the user; no distribution license inferred.
"""
"""Shared conservative octilinear raster predicate for search and smoothing."""
import numpy as np


def segment_clear(mask, a, b):
    """Check every centre AND the two flank cells of each diagonal step.

    A lattice mask represents a conservative corridor, not point obstacles.
    Sampling just the centreline during simplification used to admit legs
    that the search itself would reject. Off-lattice/non-octilinear legs are
    rejected here; native off-grid routing uses its own geometric predicate.
    """
    if any(value != int(value) for value in (*a, *b)):
        return False
    y, x = map(int, a)
    ny, nx = map(int, b)
    h, w = mask.shape
    if not (0 <= y < h and 0 <= ny < h and 0 <= x < w and 0 <= nx < w):
        return False
    dy, dx = ny-y, nx-x
    if dy and dx and abs(dy) != abs(dx):
        return False
    count = max(abs(dy), abs(dx))
    if count <= 1:
        return not (mask[y, x] or mask[ny, nx] or
                    (dy and dx and (mask[y, nx] or mask[ny, x])))
    step_y = (dy > 0)-(dy < 0)
    step_x = (dx > 0)-(dx < 0)
    steps = np.arange(count+1)
    ys, xs = y+steps*step_y, x+steps*step_x
    if mask[ys, xs].any():
        return False
    return not (dy and dx and (mask[ys[:-1], xs[1:]].any() or mask[ys[1:], xs[:-1]].any()))
