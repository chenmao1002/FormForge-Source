"""Bounded local CAD callouts, independent of any PDF/SVG backend.

One annotation occupies one geometry panel.  Labels are placed near their
actual projected anchor and never routed to another panel or a remote table.
The panel ID links to full requirements in the adjacent table or schedule.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Rect:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def contains(self, point, margin=0):
        return self.xmin + margin <= point[0] <= self.xmax - margin and self.ymin + margin <= point[1] <= self.ymax - margin

    def contains_rect(self, other):
        return self.contains((other.xmin, other.ymin)) and self.contains((other.xmax, other.ymax))


@dataclass(frozen=True)
class LocalCallout:
    anchor: tuple[float, float]
    target: tuple[float, float]
    label_box: Rect
    text_origin: tuple[float, float]

    @property
    def length(self):
        return math.dist(self.anchor, self.target)


@dataclass(frozen=True)
class CalloutViewFit:
    scale: float
    origin: tuple[float, float]
    callout: LocalCallout


def segment_intersects_rect(first, second, rect):
    """Closed line/rectangle intersection, including tangency and overlap."""
    dx, dy = second[0] - first[0], second[1] - first[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, first[0]-rect.xmin), (dx, rect.xmax-first[0]),
                 (-dy, first[1]-rect.ymin), (dy, rect.ymax-first[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        elif p < 0:
            low = max(low, q/p)
        else:
            high = min(high, q/p)
        if low > high:
            return False
    return True


def local_callout(anchor, bounds, *, label_width=9, label_height=4,
                  geometry_segments=(), max_length=23):
    """Find a short label inside this panel, clear of projected CAD edges.

    Candidates use separate label and leader regions.  An invalid anchor or a
    completely crowded panel is rejected; silently moving the anchor onto a
    different hole is never an option.
    """
    if not bounds.contains(anchor):
        raise ValueError("Annotation anchor lies outside its geometry panel")
    segments = tuple(geometry_segments)
    for distance in (6.0,8.0,10.0,12.0,15.0,18.0,20.0,23.0):
        for angle in (0,180,45,135,-45,-135,90,-90,30,60,75,105,120,150,-30,-60,-75,-105,-120,-150):
            radians = math.radians(angle)
            target = (anchor[0]+distance*math.cos(radians), anchor[1]+distance*math.sin(radians))
            # Label lies away from the anchor; the arrow stops at its boundary.
            x = target[0] if math.cos(radians) >= -1e-9 else target[0]-label_width
            y = target[1]-label_height/2
            label = Rect(x, y, x+label_width, y+label_height)
            clear = Rect(label.xmin-.8, label.ymin-.6, label.xmax+.8, label.ymax+.6)
            if distance > max_length or not bounds.contains_rect(clear):
                continue
            if any(segment_intersects_rect(first, second, clear) for first, second in segments):
                continue
            return LocalCallout(anchor, target, label, (x+.5, y+.9))
    raise ValueError("No clear local callout fits this panel; enlarge or split the view")


def fit_callout_view(lines, center, width, height, anchor, bounds, *,
                     label_width=9, scale_limit=6, max_length=23):
    """Reserve blank space for a real callout before drawing any geometry.

    Dense concentric end views can surround a center-hole anchor completely.
    In that case the whole exact view is reduced, rather than covering an edge,
    extending the leader into another panel, or changing the anchor.
    """
    points=[point for _,line in lines for point in line]
    if not points:
        raise ValueError("Annotation view has no projected CAD edges")
    xmin,xmax=min(x for x,y in points),max(x for x,y in points)
    ymin,ymax=min(y for x,y in points),max(y for x,y in points)
    initial=min(width/max(xmax-xmin,1e-6),height/max(ymax-ymin,1e-6),scale_limit)
    for reduction in (1.,.85,.7,.55,.4,.3,.2):
        scale=initial*reduction
        origin=(center[0]-(xmin+xmax)*scale/2,center[1]-(ymin+ymax)*scale/2)
        def paper(point):
            return origin[0]+point[0]*scale,origin[1]+point[1]*scale
        edges=[(paper(a),paper(b)) for _,line in lines for a,b in zip(line,line[1:])]
        try:
            callout=local_callout(paper(anchor),bounds,label_width=label_width,
                                  geometry_segments=edges,max_length=max_length)
            return CalloutViewFit(scale,origin,callout)
        except ValueError:
            continue
    raise ValueError("No clear local callout fits this panel after adaptive view scaling; enlarge or split the view")
