"""Bounded, explicit local wire presentation. Native netlist equality is mandatory.

No net-name role inference, symbol movement, cross-sheet routing or label deletion.
The small candidate set is a readability aid, not a routability proof.
"""
import math
import uuid
from collections import defaultdict

from .schematic import location, object_id, properties, wire_points, number, fail
from .schematic_fields import symbol_box, text_box, crosses
from .topology import symbol_pins
from ..store import DomainError

POLICY = 'selected-local-label-wires-v2'


def on_segment(p, a, b):
    return (abs((p[0]-a[0])*(b[1]-a[1])-(p[1]-a[1])*(b[0]-a[0])) < 1e-7
            and min(a[0], b[0])-1e-7 <= p[0] <= max(a[0], b[0])+1e-7
            and min(a[1], b[1])-1e-7 <= p[1] <= max(a[1], b[1])+1e-7)


def contact(a, b, c, d):
    """Reject crossings/overlaps; permit only a single shared endpoint."""
    shared = set((a, b)) & set((c, d))
    if shared:
        p = next(iter(shared))
        q = b if a == p else a
        r = d if c == p else c
        return on_segment(q, p, r) or on_segment(r, p, q)
    # Liang-Barsky also handles diagonal existing wires conservatively.
    if a[0] == b[0]:
        return crosses((a[0], min(a[1], b[1]), a[0], max(a[1], b[1])), c, d)
    return crosses((min(a[0], b[0]), a[1], max(a[0], b[0]), a[1]), c, d)


def candidates(a, b):
    paths = [[a, b]] if a[0] == b[0] or a[1] == b[1] else [
        [a, (a[0], b[1]), b], [a, (b[0], a[1]), b],
        [a, ((a[0]+b[0])/2, a[1]), ((a[0]+b[0])/2, b[1]), b],
        [a, (a[0], (a[1]+b[1])/2), (b[0], (a[1]+b[1])/2), b]]
    # Small, deterministic detours also cover aligned terminals with an obstacle.
    for offset in (2.54, 5.08, 10.16, 20.32):
        for sign in (-1, 1):
            if a[1] == b[1]:
                y = round(a[1] + sign*offset, 6)
                paths.append([a, (a[0], y), (b[0], y), b])
            elif a[0] == b[0]:
                x = round(a[0] + sign*offset, 6)
                paths.append([a, (x, a[1]), (x, b[1]), b])
    return paths


def plan(page, op, check_cancel=lambda: None):
    ids = op['labelIds']
    labels = {object_id(n): n for n in page['objects'] if n.kind in {'label', 'global_label'}}
    if len(ids) != len(set(ids)) or any(i not in labels for i in ids):
        fail('局部连线需要不重复且存在的普通/全局标签 UUID。')
    selected = set(ids)
    selected_boxes = {}
    groups = defaultdict(list)
    for i in sorted(ids):
        n = labels[i]
        groups[(n.kind, n.values[1])].append((i, tuple(location(n)[:2])))
    boxes, pins, anchors, wires, barriers, unavailable = [], [], [], [], [], []
    for n in page['objects']:
        check_cancel()
        identity = object_id(n)
        if n.kind == 'wire':
            pts = [tuple(p) for p in wire_points(n)]
            wires.extend(zip(pts, pts[1:]))
        elif n.kind == 'symbol':
            try:
                boxes.append(symbol_box(page, n, include_pins=False))
                pins.extend(symbol_pins(page, n, allow_power_flag=True))
            except (ValueError, IndexError, TypeError, DomainError) as exc:
                x, y = location(n)[:2]
                boxes.append((x-15, y-15, x+15, y+15))
                unavailable.append(dict(objectId=identity, reason=str(exc)))
            for field in properties(n).values():
                try:
                    box = text_box(field,symbol_rotation=location(n)[2])
                    if box: boxes.append(box)
                except (ValueError, IndexError, TypeError):
                    x, y = location(field)[:2]
                    boxes.append((x-15, y-15, x+15, y+15))
                    unavailable.append(dict(objectId=identity, reason='unsupported_field_envelope'))
        elif n.kind in {'label', 'global_label', 'hierarchical_label', 'text'}:
            anchors.append((identity, tuple(location(n)[:2])))
            if identity in selected:
                try: selected_boxes[identity] = text_box(n)
                except (ValueError, IndexError, TypeError):
                    x,y = location(n)[:2]
                    selected_boxes[identity] = (x-15,y-15,x+15,y+15)
                    unavailable.append(dict(objectId=identity,reason='unsupported_text_envelope'))
            if identity not in selected:
                try:
                    box = text_box(n)
                    if box: boxes.append(box)
                except (ValueError, IndexError, TypeError):
                    x, y = location(n)[:2]
                    boxes.append((x-15, y-15, x+15, y+15))
                    unavailable.append(dict(objectId=identity, reason='unsupported_text_envelope'))
        elif n.kind == 'no_connect':
            x, y = location(n)[:2]; boxes.append((x-.5,y-.5,x+.5,y+.5))
        elif n.kind == 'sheet':
            # Child sheets are real obstacles, not a reason to reject the entire parent page.
            try:
                x, y = location(n)[:2]
                w, h = map(float, n.one('size').values[1:3])
                if not all(math.isfinite(v) for v in (x,y,w,h)) or min(w,h) <= 0:
                    raise ValueError('invalid_sheet_geometry')
                boxes.append((x-.35,y-.35,x+w+.35,y+h+.35))
                for field in [*properties(n).values(), *n.children('pin')]:
                    box = text_box(field)
                    if box: boxes.append(box)
                for pin in n.children('pin'):
                    px,py = location(pin)[:2]
                    boxes.append((px-.5,py-.5,px+.5,py+.5))
            except (ValueError, IndexError, TypeError, AttributeError):
                unavailable.append(dict(objectId=identity, reason='unsupported_document_obstacle', type='sheet'))
        elif n.kind in {'rectangle','polyline'}:
            if n.kind == 'rectangle':
                a = tuple(map(float,n.one('start').values[1:3])); b = tuple(map(float,n.one('end').values[1:3]))
                pts = [a,(b[0],a[1]),b,(a[0],b[1]),a]
            else:
                pts = [tuple(map(float,p.values[1:3])) for p in n.one('pts').children('xy')]
            barriers.extend(zip(pts,pts[1:]))
        elif n.kind not in {'junction'}:
            # Unknown document graphics are not assumed empty.
            unavailable.append(dict(objectId=identity, reason='unsupported_document_obstacle', type=n.kind))
    report = dict(kind=op['kind'], sheet=op['sheet'], policy=POLICY, connected=[], skipped=[],
                  unavailable=unavailable, addedWireCount=0, addedJunctionCount=0,
                  labelsRetained=True, nativeConnectivityCheckRequired=True)
    # Graphics without a reliable envelope make this page unsuitable for auto-wiring.
    if any(r['reason'] == 'unsupported_document_obstacle' for r in unavailable):
        report['skipped'].append(dict(reason='unsupported_document_obstacle'))
        return [], report
    parent = {}
    def root(p):
        parent.setdefault(p, p)
        if parent[p] != p: parent[p] = root(parent[p])
        return parent[p]
    def union(a, b): parent[root(a)] = root(b)
    for a, b in wires: union(a, b)
    additions = []
    max_length = op.get('maxLengthMm', 25.4)
    pairs = []
    for key, entries in groups.items():
        for index, (i, a) in enumerate(entries):
            for j, b in entries[index+1:]:
                pairs.append((abs(a[0]-b[0])+abs(a[1]-b[1]), key, i, j, a, b))
    for length, key, i, j, a, b in sorted(pairs):
        check_cancel()
        if root(a) == root(b): continue
        if length > max_length:
            report['skipped'].append(dict(labelIds=[i,j], reason='length_bound')); continue
        accepted = None
        for path in candidates(a, b):
            grid=op.get("routingGridMm")
            if grid and any(abs(v/grid-round(v/grid))>1e-6 for point in path for v in point):continue
            segments = list(zip(path, path[1:]))
            actual_length = sum(abs(c[0]-d[0])+abs(c[1]-d[1]) for c,d in segments)
            if actual_length > max_length + 1e-7: continue
            if any(crosses(box, c, d) for c,d in segments for box in boxes): continue
            if any(crosses(box,c,d) for label_id,box in selected_boxes.items() if box and label_id not in (i,j) for c,d in segments): continue
            if any(contact(c,d,e,f) for c,d in segments for e,f in wires): continue
            if any(contact(c,d,e,f) or set((c,d)) & set((e,f)) for c,d in segments for e,f in barriers): continue
            # No incidental pin/label contact. At selected pin anchors, leave outward.
            valid = True
            for pin in pins:
                p = tuple(pin['at'])
                if p in (a,b):
                    q = path[1] if p == a else path[-2]
                    angle = math.radians(pin['orientation'])
                    outward = (-math.cos(angle), math.sin(angle))
                    dx,dy = q[0]-p[0],q[1]-p[1]
                    if abs(dx*outward[1]-dy*outward[0]) > 1e-6 or dx*outward[0]+dy*outward[1] <= 0:
                        valid = False
                elif any(on_segment(p,c,d) for c,d in segments): valid = False
            if not valid: continue
            if any(p not in (a,b) and any(on_segment(p,c,d) for c,d in segments) for _,p in anchors): continue
            accepted = path; break
        if accepted is None:
            report['skipped'].append(dict(labelIds=[i,j], reason='no_clear_bounded_candidate')); continue
        for c,d in zip(accepted, accepted[1:]):
            pts = ' '.join('(xy '+number(p[0])+' '+number(p[1])+')' for p in (c,d))
            additions.append(f'(wire (pts {pts}) (stroke (width 0.1524) (type default)) (uuid "{uuid.uuid4()}"))')
            wires.append((c,d)); union(c,d)
            report['addedWireCount'] += 1
        report['connected'].append(dict(labelIds=[i,j], net=key[1], points=accepted,
            lengthMm=sum(abs(c[0]-d[0])+abs(c[1]-d[1]) for c,d in zip(accepted,accepted[1:]))))
    existing = {tuple(location(n)[:2]) for n in page['tree'].children('junction')}
    for p in sorted({tuple(p) for row in report['connected'] for p in (row['points'][0], row['points'][-1])}):
        count = sum(p in (a,b) for a,b in wires) + sum(tuple(pin['at']) == p for pin in pins)
        if count >= 3 and p not in existing:
            additions.append(f'(junction (at {number(p[0])} {number(p[1])}) (diameter 0) (color 0 0 0 0) (uuid "{uuid.uuid4()}"))')
            report['addedJunctionCount'] += 1
    end = page['tree'].end-1
    return ([(end,end,'\n\t'+'\n\t'.join(additions)+'\n')] if additions else []), report
