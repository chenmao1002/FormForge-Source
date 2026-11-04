"""Bounded presentation-only field placement; electrical anchors never move.

Text envelopes are heuristic estimates, not native glyph measurements.
The caller still runs native ERC/netlist equivalence and inspects native SVG.
"""
import math

from .schematic import child_value, fail, location, number, object_id, properties
from .topology import descendants

POLICY = 'bounded-field-wire-avoidance-v1'
from .schematic_policy import AGENT_INSTRUCTIONS


def text_box(node, at=None, margin=.25, symbol_rotation=0, content=None):
    effects = node.one('effects')
    if 'hide' in effects.values or any(c.values[1:] == ['yes'] for c in effects.children('hide')):
        return None
    font = effects.one('font')
    if font.children('face'):
        raise ValueError('custom_font')
    size = [float(v) for v in font.one('size').values[1:3]]
    text = str(content if content is not None else node.values[2] if node.kind == 'property' else node.values[1])
    lines = text.splitlines() or ['']
    width, height = max(map(len, lines)) * size[1] * .85, len(lines) * size[0] * 1.2
    justify = effects.children('justify')
    alignment = justify[0].values[1:] if justify else []
    x0 = 0 if 'left' in alignment else -width if 'right' in alignment else -width/2
    y0 = -height if 'bottom' in alignment else 0 if 'top' in alignment else -height/2
    pos = list(at or location(node))
    pos[2] = (pos[2]+symbol_rotation)%180 if node.kind=='property' else pos[2]
    if pos[2] % 90:
        raise ValueError('non_orthogonal_text')
    angle = math.radians(-pos[2]); c, s = math.cos(angle), math.sin(angle)
    corners = [(pos[0]+x*c-y*s, pos[1]+x*s+y*c) for x in (x0,x0+width) for y in (y0,y0+height)]
    return (min(x for x,y in corners)-margin, min(y for x,y in corners)-margin,
            max(x for x,y in corners)+margin, max(y for x,y in corners)+margin)


def overlaps(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def crosses(box, a, b):
    """Continuous segment/rectangle intersection, including the segment interior."""
    lo, hi = 0., 1.
    for axis in (0,1):
        delta = b[axis]-a[axis]
        if delta == 0:
            if not box[axis] <= a[axis] <= box[axis+2]: return False
        else:
            p, q = sorted(((box[axis]-a[axis])/delta, (box[axis+2]-a[axis])/delta))
            lo, hi = max(lo,p), min(hi,q)
            if lo > hi: return False
    return True


def symbol_box(page, node, include_pins=True, *, allow_complex=False):
    definitions = [s for s in page['tree'].one('lib_symbols').children('symbol')
                   if s.values[1] == child_value(node,'lib_id')]
    if len(definitions)!=1 or (not allow_complex and (node.children('mirror') or str(child_value(node,'unit'))!='1')):
        raise ValueError('unsupported_symbol_envelope')
    if definitions[0].children('extends'):raise ValueError('unresolved_symbol_envelope')
    unit=int(child_value(node,'unit',1));body=int(child_value(node,'body_style',child_value(node,'convert',1)))
    points = [(0.,0.)]
    for part in definitions[0].children('symbol'):
        if allow_complex:
            import re
            match=re.search(r'_(\d+)_(\d+)$',str(part.values[1]))
            if not match:raise ValueError('unknown_symbol_part')
            if int(match[1]) not in (0,unit) or int(match[2]) not in (0,body):continue
        for graphic in part.children('polyline')+part.children('rectangle')+(part.children('pin') if include_pins else [])+part.children('circle'):
            for p in descendants(graphic):
                if p.kind in {'xy','start','end','at'}:
                    points.append(tuple(float(v) for v in p.values[1:3]))
            if graphic.kind=='circle':
                x,y=map(float,graphic.one('center').values[1:3]);r=float(child_value(graphic,'radius'))
                points.extend([(x-r,y-r),(x+r,y+r)])
        for arc in part.children('arc'):
            a,b,d=[tuple(map(float,arc.one(k).values[1:3])) for k in ('start','mid','end')]
            det=2*(a[0]*(b[1]-d[1])+b[0]*(d[1]-a[1])+d[0]*(a[1]-b[1]))
            if abs(det)<1e-10:raise ValueError('degenerate_symbol_arc')
            aa,bb,dd=[x*x+y*y for x,y in (a,b,d)]
            cx=(aa*(b[1]-d[1])+bb*(d[1]-a[1])+dd*(a[1]-b[1]))/det
            cy=(aa*(d[0]-b[0])+bb*(a[0]-d[0])+dd*(b[0]-a[0]))/det
            radius=math.hypot(a[0]-cx,a[1]-cy)
            # The complete circle conservatively contains the native arc.
            points.extend([(cx-radius,cy-radius),(cx+radius,cy+radius)])
        if part.children('text') or part.children('bezier'):
            raise ValueError('unsupported_symbol_graphics')
    x,y,angle=location(node);r=math.radians(angle);c,s=math.cos(r),math.sin(r)
    mirror=child_value(node,'mirror')
    relative=[(px*c-py*s,-px*s-py*c) for px,py in points]
    if mirror=='x':relative=[(px,-py) for px,py in relative]
    if mirror=='y':relative=[(-px,py) for px,py in relative]
    world=[(x+px,y+py) for px,py in relative]
    return (min(p[0] for p in world)-.35,min(p[1] for p in world)-.35,
            max(p[0] for p in world)+.35,max(p[1] for p in world)+.35)


def plan(page, op, check_cancel=lambda:None):
    ids=op['symbolIds']
    if len(ids)!=len(set(ids)): fail('字段整理的器件 UUID 不能重复。')
    symbols={object_id(n):n for n in page['tree'].children('symbol')}
    if any(i not in symbols for i in ids): fail('字段整理的器件 UUID 不存在。')
    boxes, wires, unavailable, fields = {}, [], [], {}
    for node in page['objects']:
        identity=object_id(node)
        if node.kind=='wire':
            points=[tuple(map(float,p.values[1:3])) for p in node.one('pts').children('xy')]
            wires.extend((identity,a,b) for a,b in zip(points,points[1:]))
        if node.kind=='symbol':
            try: boxes[(identity,'body')]=symbol_box(page,node)
            except (ValueError,IndexError,TypeError) as exc:
                # Unknown nearby shapes are not silently treated as empty space.
                x,y,*_=location(node);boxes[(identity,'body')]=(x-15,y-15,x+15,y+15)
                unavailable.append(dict(objectId=identity,reason=str(exc),fallbackEnvelopeMm=30))
            for name,field in properties(node).items():
                try: box=text_box(field,symbol_rotation=location(node)[2])
                except (ValueError,IndexError,TypeError) as exc:
                    x,y,*_=location(field);boxes[(identity,name)]=(x-15,y-15,x+15,y+15)
                    unavailable.append(dict(objectId=identity,property=name,reason=str(exc),fallbackEnvelopeMm=30));continue
                if box:
                    boxes[(identity,name)]=box;fields[(identity,name)]=field
        elif node.kind in {'label','global_label','hierarchical_label','text'}:
            try: box=text_box(node)
            except (ValueError,IndexError,TypeError) as exc:
                x,y,*_=location(node);box=(x-15,y-15,x+15,y+15)
                unavailable.append(dict(objectId=identity,reason=str(exc),fallbackEnvelopeMm=30))
            if box: boxes[(identity,'text')]=box
    def conflicts(key, box):
        return ([dict(type='wire',objectId=i) for i,a,b in wires if crosses(box,a,b)] +
                [dict(type='envelope',objectId=k[0],property=k[1]) for k,b in boxes.items() if k!=key and overlaps(box,b)])
    step=op.get('stepMm',1.27);radius=op.get('maxShiftMm',5.08)
    n=int(radius/step+1e-8)
    offsets=sorted([(i*step,j*step) for i in range(-n,n+1) for j in range(-n,n+1)
                    if 0<i*i+j*j and math.hypot(i*step,j*step)<=radius+1e-8],key=lambda p:(p[0]**2+p[1]**2,abs(p[0]),p[1],p[0]))
    edits, moves, unresolved = [], [], []
    selected=[(i,name) for i in sorted(ids) for name in ('Reference','Value') if (i,name) in fields]
    def metrics():
        hits=[h for k in selected for h in conflicts(k,boxes[k])]
        return dict(estimatedConflictCount=len(hits),estimatedWireContactCount=sum(h['type']=='wire' for h in hits))
    before=metrics()
    for key in selected:
        check_cancel();field=fields[key];old=location(field);hits=conflicts(key,boxes[key])
        if not hits: continue
        for dx,dy in offsets:
            check_cancel();at=[round(old[0]+dx,6),round(old[1]+dy,6),old[2]]
            box=text_box(field,at,symbol_rotation=location(symbols[key[0]])[2])
            if conflicts(key,box):continue
            target=field.one('at');edits.append((target.start,target.end,'(at '+' '.join(number(v) for v in at)+')'))
            boxes[key]=box
            moves.append(dict(objectId=key[0],property=key[1],before=old,after=at,previousConflicts=hits))
            break
        else: unresolved.append(dict(objectId=key[0],property=key[1],reason='no_clear_candidate_within_bound',conflicts=hits))
    after=metrics()
    return edits,dict(kind='tidy_fields',sheet=op['sheet'],policy=POLICY,moves=moves,
        before=before,after=after,
        movedFieldCount=len(moves),unresolved=unresolved,unavailable=unavailable,
        scope='Reference/Value positions only; bounded single pass, estimated text/graphic envelopes. '
              'No electrical anchors, text, font, angle or visibility changes; native SVG review required. '
              'No candidate within bound does not prove impossibility.')
