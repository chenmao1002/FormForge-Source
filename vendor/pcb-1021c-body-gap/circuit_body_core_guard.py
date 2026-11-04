"""Read-only, finite-width SMD body-gap guard shared by candidate producers.

Uses the existing native Fab/pad-derived ``cores`` contract, not the whole
footprint body or courtyard. Own-net copper is intentionally NOT exempt.
No board keepout is added; unknown packages remain outside this limited check.
"""
import math
import pcbnew as pcb
from circuit_component_keepouts import cores


def _point(xy):
    return pcb.VECTOR2I(*(round(v * 1_000_000) for v in xy))


class BodyCoreGuard:
    def __init__(self, board):
        self.rows = []
        self.buckets = {}
        for reference, layer, rect in cores(board):
            shape = pcb.SHAPE_POLY_SET()
            shape.NewOutline()
            x0, y0, x1, y1 = rect
            for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                shape.Append(pcb.VECTOR2I(x, y))
            row = (reference, layer, tuple(rect), shape)
            self.rows.append(row)
            for key in self._keys(tuple(v / 1_000_000 for v in rect)):
                self.buckets.setdefault((layer, *key), []).append(row)

    @staticmethod
    def _keys(box):
        return ((x, y) for x in range(math.floor(box[0] / 2), math.floor(box[2] / 2) + 1)
                for y in range(math.floor(box[1] / 2), math.floor(box[3] / 2) + 1))

    def blocker(self, a, b, width_mm, layer):
        if not math.isfinite(width_mm) or width_mm <= 0:
            raise ValueError('Body-core check requires a positive finite width')
        if not all(math.isfinite(x) for p in (a, b) for x in p):
            raise ValueError('Body-core check requires finite coordinates')
        radius = width_mm / 2
        bounds = (min(a[0], b[0]) - radius, min(a[1], b[1]) - radius,
                  max(a[0], b[0]) + radius, max(a[1], b[1]) + radius)
        segment = None
        seen = set()
        for key in self._keys(bounds):
            for reference, _, _, shape in self.buckets.get((layer, *key), ()):
                if id(shape) in seen:
                    continue
                seen.add(id(shape))
                if segment is None:
                    segment = (pcb.SHAPE_CIRCLE(_point(a), math.ceil(radius * 1_000_000))
                               if a == b else pcb.SHAPE_SEGMENT(
                                   _point(a), _point(b), math.ceil(width_mm * 1_000_000)))
                if shape.Collide(segment, 0):
                    return 'body-core:' + reference
        return None

    def track_legal(self, a, b, width_mm, layer):
        return self.blocker(a, b, width_mm, layer) is None

    def via_legal(self, position, diameter_mm, layers):
        return all(self.track_legal(position, position, diameter_mm, layer) for layer in layers)

    def protect_spaces(self, spaces, widths, layers, via_space=None, diameter_mm=None, via_layers=None):
        """Attach the exact same predicate to bridge's real Space entrypoints."""
        if not (len(spaces) == len(widths) == len(layers)):
            raise ValueError('Body-core Space layers/widths mismatch')
        for space, width, layer in zip(spaces, widths, layers):
            previous = space.blocker
            space.blocker = lambda a, b, previous=previous, width=width, layer=layer: (
                previous(a, b) or self.blocker(a, b, width, layer))
        if via_space is not None:
            if diameter_mm is None:
                raise ValueError('Body-core via diameter missing')
            via_layers = tuple(layers if via_layers is None else via_layers)
            previous = via_space.blocker
            via_space.blocker = lambda a, b, previous=previous: (
                previous(a, b) or next((self.blocker(a, b, diameter_mm, layer)
                                      for layer in via_layers
                                      if not self.track_legal(a, b, diameter_mm, layer)), None))

    def report(self):
        return [{'reference': ref, 'layer': layer, 'rectIU': list(rect)}
                for ref, layer, rect, _ in self.rows]
