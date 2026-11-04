"""Explicit pin-pair branches: bounded joint placement and direct-wire planning.

The caller still requires native ERC and complete pin partition equivalence.
Kinds express Agent roles; only connectivity and geometry are decided here.
"""
import math
import uuid

from .schematic import fail, location, number, object_id, properties, load_pages, safe_string
from .schematic_fields import symbol_box, text_box, crosses, overlaps, plan as tidy
from .schematic_transform import rotate_symbol
from .schematic_connections import on_segment, contact
from .native_syntax import parse
from .topology import symbol_pins

GRID = 1.27


def proper_cross(a,b,c,d):
    if a[0]==b[0] and c[1]==d[1]:
        p=(a[0],c[1])
    elif a[1]==b[1] and c[0]==d[0]:
        p=(c[0],a[1])
    else:return None
    return p if on_segment(p,a,b) and on_segment(p,c,d) and p not in (a,b,c,d) else None


def label_box(node):
    # Global label borders/arrow tips extend beyond the text estimate.
    return text_box(node, margin=1.75 if node.kind=='global_label' else .25)


def validate(intent, symbols, graph, groups, style):
    if not intent:
        return
    if style != 'readable' or not groups:
        fail('支路意图需要易读策略及完整显式功能组。')
    membership = {r: i for i, group in enumerate(groups) for r in group}
    partitions = {(p['reference'], p['pin']): g['id'] for g in graph['groups'] for p in g['pins']}
    ids, targets = set(), set()
    for b in intent['branches']:
        a, z = b['source'], b['target']
        ak, zk = (a['reference'], a['pin']), (z['reference'], z['pin'])
        if b['id'] in ids or (b.get('moveTarget',True) and z['reference'] in targets):
            fail('支路身份或目标器件重复；同一被移动器件只允许一个归属。')
        ids.add(b['id'])
        if b.get('moveTarget',True):targets.add(z['reference'])
        if any(k not in partitions or k[0] not in symbols for k in (ak, zk)):
            fail('支路引用未知器件或真实针号。')
        if ak[0] == zk[0] or membership[ak[0]] != membership[zk[0]]:
            fail('支路必须连接同一功能组内的不同器件。')
        if partitions[ak] != partitions[zk]:
            fail('支路端点不属于同一原生网络，不能通过排版改接。')
        if len(symbols[zk[0]][1]) != 2 or not b.get('moveTarget',True):
            if (len(symbols[zk[0]][1])!=2 and b['kind'] not in {'protection','driver'}) or len(set(b['allowedRotations']))!=1:
                fail('多引脚目标只支持protection/driver且必须明确唯一角度；普通支路仅移动双引脚器件。')
        if any(k[1] not in {p['number'] for p in symbols[k[0]][1]} for k in (ak, zk)):
            fail('支路引脚没有受支持的原生几何。')
    # Moving an anchor after its dependent was wired would invalidate the path.
    placed = set()
    for b in intent['branches']:
        if b['source']['reference'] in targets and b['source']['reference'] not in placed:
            fail('支路有循环或顺序冲突；须先放置目标锚点再放置从属器件。')
        placed.add(b['target']['reference'])


def transformed(page, node, rotation, delta):
    node = parse(node.raw)
    if rotation != location(node)[2]:
        edits, _ = rotate_symbol(page, node, rotation)
        text = node.raw
        for a, b, v in sorted(edits, reverse=True):
            text = text[:a-node.start]+v+text[b-node.start:]
        node = parse(text)
    text = node.raw
    edits = []
    for n in [node, *node.children('property')]:
        at = location(n)
        field_angle = (-rotation)%180 if n.kind=='property' and n.values[1] in {'Reference','Value'} else at[2]
        edits.append((n.one('at').start, n.one('at').end,
                      '(at '+' '.join(number(v) for v in [at[0]+delta[0], at[1]+delta[1], field_angle])+')'))
    for a, b, v in sorted(edits, reverse=True):
        text = text[:a]+v+text[b:]
    return parse(text)


def outward(pin):
    r = math.radians(pin['orientation'])
    return tuple(round(v) for v in (-math.cos(r), math.sin(r)))


def plan_stub(path, netname, occupied, wires, width, height):
    for c,d in sorted(zip(path,path[1:]),key=lambda pair:-sum(abs(pair[0][i]-pair[1][i]) for i in (0,1))):
        axis=0 if c[1]==d[1] else 1
        for fraction in (.5,.25,.75):
            mid=list(c);mid[axis]=round(round((c[axis]+(d[axis]-c[axis])*fraction)/GRID)*GRID,6);mid=tuple(mid)
            if mid in (c,d):continue
            for length in (5.08,7.62,10.16,15.24,20.32):
                for sign in (-1,1):
                    end=list(mid);end[1-axis]+=length*sign;end=tuple(round(v,6) for v in end)
                    for justify in ('left','right'):
                        label='(global_label '+safe_string(netname)+' (shape bidirectional) (at '+number(end[0])+' '+number(end[1])+' 0) (effects (font (size 1.27 1.27)) (justify '+justify+')) (uuid "'+str(uuid.uuid4())+'"))'
                        # Stroke-font widths and the global-label outline need a
                        # larger guard than plain text; native previews remain mandatory.
                        box=text_box(parse(label),margin=3.81)
                        if not (15<=box[0] and box[2]<=width-15 and 15<=box[1] and box[3]<=height-30):continue
                        if any(overlaps(box,v) or crosses(v,mid,end) for v in occupied):continue
                        if any(crosses(box,e,f) for e,f in wires):continue
                        if any(contact(mid,end,e,f) for e,f in wires if (e,f)!=(c,d)):continue
                        return mid,end,label,c,d
    return None


def paths(a, b, oa, ob, detours=False):
    # Explicit outward necks keep wires outside the native body and pin text.
    for neck in (5.08, 7.62, 10.16):
        c = tuple(round(a[i]+neck*oa[i], 6) for i in (0, 1))
        d = tuple(round(b[i]+neck*ob[i], 6) for i in (0, 1))
        middle = [[c, d]] if c[0] == d[0] or c[1] == d[1] else [
            [c, (c[0], d[1]), d], [c, (d[0], c[1]), d]]
        for mid in middle:
            raw = [a, *mid, b]; clean = []
            for p in raw:
                if clean and clean[-1] == p: continue
                clean.append(p)
            # No reversing necks, retraced segments or hidden 180-degree turns.
            if any((q[0]-p[0])*(r[0]-q[0])+(q[1]-p[1])*(r[1]-q[1]) < -1e-6
                   for p, q, r in zip(clean, clean[1:], clean[2:])): continue
            # Collapse collinear pieces before reporting or adding the path.
            simplified = []
            for p in clean:
                while len(simplified)>1 and (simplified[-1][0]-simplified[-2][0])*(p[1]-simplified[-1][1]) == (simplified[-1][1]-simplified[-2][1])*(p[0]-simplified[-1][0]):
                    simplified.pop()
                simplified.append(p)
            yield simplified
    if detours and oa[1]==ob[1]==0:
        # Independent outward neck lengths and one outside rail can get around
        # dense connector fields without moving an already placed device.
        for first in (12.7,17.78,25.4,30.48):
            for last in (2.54,5.08,7.62):
                c=(round(a[0]+first*oa[0],6),a[1]);d=(round(b[0]+last*ob[0],6),b[1])
                for y in (round(round((a[1]+b[1])/2/GRID)*GRID,6),min(a[1],b[1])-7.62,max(a[1],b[1])+7.62):
                    raw=[a,c,(c[0],round(y,6)),(d[0],round(y,6)),d,b]
                    clean=[]
                    for p in raw:
                        if not clean or clean[-1]!=p:clean.append(p)
                    if any((q[0]-p[0])*(r[0]-q[0])+(q[1]-p[1])*(r[1]-q[1])< -1e-6 for p,q,r in zip(clean,clean[1:],clean[2:])):continue
                    yield clean


def apply(source, entry, intent, page_reports, cancel, nets):
    reports = []
    for branch in intent['branches']:
        cancel()
        sr, tr = branch['source']['reference'], branch['target']['reference']
        relative = next(r['sheet'] for r in page_reports if sr in r['references'])
        page = load_pages(source, entry)[relative]
        symbols = {properties(n)['Reference'].values[2]: n for n in page['tree'].children('symbol')}
        anchor, target = symbols[sr], symbols[tr]
        if not branch.get('moveTarget',True) and set(branch['allowedRotations'])!={int(location(target)[2])}:
            fail('固定目标连接必须保持当前原生角度。')
        sp = next(p for p in symbol_pins(page, anchor) if p['number'] == branch['source']['pin'])
        # A dependent capacitor/rail may already have a wire leaving its pin.
        # Tap that physical segment rather than drawing overlapping copper-like
        # schematic lines on top of the existing neck.
        source_at=tuple(sp['at']);tapped=None
        for existing in page['tree'].children('wire'):
            pts=[tuple(map(float,p.values[1:3])) for p in existing.one('pts').children('xy')]
            if len(pts)!=2 or source_at not in pts or math.dist(*pts)<5.08-1e-6:continue
            c,d=pts;axis=0 if c[1]==d[1] else 1
            mid=list(c);mid[axis]=round(round((c[axis]+d[axis])/2/GRID)*GRID,6);mid=tuple(mid)
            if mid in (c,d):continue
            def section(e,f):
                return '(wire (pts (xy '+number(e[0])+' '+number(e[1])+') (xy '+number(f[0])+' '+number(f[1])+')) (stroke (width 0.1524) (type default)) (uuid "'+str(uuid.uuid4())+'"))'
            replacement=section(c,mid)+'\n'+section(mid,d)+'\n(junction (at '+number(mid[0])+' '+number(mid[1])+') (diameter 0) (color 0 0 0 0) (uuid "'+str(uuid.uuid4())+'"))'
            text=page['text'];page['path'].write_text(text[:existing.start]+replacement+text[existing.end:],'utf8')
            side=branch.get('tapSide') or ('bottom' if axis==0 else 'right')
            if (axis==0 and side not in {'top','bottom'}) or (axis==1 and side not in {'left','right'}):
                fail('tapSide必须与既有导线垂直。')
            tapped=dict(at=mid,orientation={'left':0.,'right':180.,'top':270.,'bottom':90.}[side])
            page=load_pages(source,entry)[relative]
            symbols={properties(n)['Reference'].values[2]:n for n in page['tree'].children('symbol')}
            anchor,target=symbols[sr],symbols[tr]
            break
        if tapped:sp=dict(sp,**tapped)
        oldpins = symbol_pins(page, target)
        oldanchors = {tuple(p['at']) for p in oldpins}
        a, oa = tuple(sp['at']), outward(sp)
        netname=nets[sr,branch['source']['pin']]
        # Labels electrically attached to moved pins travel with the symbol.
        moving_labels = [n for n in page['objects'] if n.kind in {'label', 'global_label', 'no_connect'}
                         and tuple(location(n)[:2]) in oldanchors]
        excluded = {object_id(n) for n in [target, *moving_labels]}
        boxes, pins, wires = [], [], []
        for n in page['objects']:
            if object_id(n) in excluded: continue
            if n.kind == 'symbol':
                boxes.append(symbol_box(page, n, include_pins=False))
                pins.extend(symbol_pins(page, n))
                boxes.extend(b for f in properties(n).values() if (b := text_box(f, symbol_rotation=location(n)[2])))
            elif n.kind in {'label', 'global_label', 'text'}:
                if tuple(location(n)[:2]) != a:
                    box = label_box(n)
                    if box: boxes.append(box)
            elif n.kind == 'wire':
                pts = [tuple(map(float, p.values[1:3])) for p in n.one('pts').children('xy')]
                wires.extend(zip(pts, pts[1:]))
            elif n.kind == 'no_connect':
                x,y=location(n)[:2];boxes.append((x-.8,y-.8,x+.8,y+.8))
        best = None
        old = location(target)
        for rotation in dict.fromkeys(branch['allowedRotations']):
            rotated = transformed(page, target, rotation, (0, 0))
            rp = next(p for p in symbol_pins(page, rotated) if p['number'] == branch['target']['pin'])
            # Finite grid search about the source pin, outside old individual cells.
            distances = (12.7, 17.78, 22.86, 30.48, 40.64)
            if branch['kind'] == 'decoupling': distances += (-7.62, -15.24)
            if not branch.get('moveTarget',True):distances=(0,)
            for distance in distances:
                for lateral in ((0,) if not branch.get('moveTarget',True) else (0, -7.62, 7.62, -15.24, 15.24, -25.4, 25.4)):
                    cancel()
                    b = tuple(round(a[i]+oa[i]*distance+(-oa[1], oa[0])[i]*lateral, 6) for i in (0, 1))
                    if not branch.get('moveTarget',True):b=tuple(rp['at'])
                    delta = tuple(round(b[i]-rp['at'][i], 6) for i in (0, 1))
                    if any(abs(v/GRID-round(v/GRID)) > 1e-6 for v in delta): continue
                    candidate = transformed(page, target, rotation, delta)
                    body = symbol_box(page, candidate, include_pins=False)
                    w, h = {'A3': (420, 297), 'A4': (297, 210), 'A2': (594, 420), 'A1': (841, 594), 'A0': (1189, 841)}[page['tree'].one('paper').values[1]]
                    if body[0] < 15 or body[1] < 15 or body[2] > w-15 or body[3] > h-30: continue
                    fieldboxes = [v for f in properties(candidate).values() if (v := text_box(f, symbol_rotation=rotation))]
                    if any(overlaps(body, box) for box in boxes): continue
                    if any(crosses(body,e,f) for e,f in wires): continue
                    cp = symbol_pins(page, candidate)
                    ob = outward(next(p for p in cp if p['number'] == branch['target']['pin']))
                    candidate_paths=list(paths(a,b,oa,ob,detours=branch.get('allowCrossings',False)))
                    if branch.get('allowCrossings') and not branch.get('moveTarget',True):
                        from .schematic_channel import route
                        routed=route(a,b,oa,ob,[*boxes,body,*fieldboxes],wires,[*pins,*cp],cancel,width=w,height=h)
                        if routed:candidate_paths.append(routed)
                    for path in candidate_paths:
                        if any(not (15 <= p[0] <= w-15 and 15 <= p[1] <= h-30) for p in path): continue
                        segments = list(zip(path, path[1:]))
                        if any(crosses(box, c, d) for c, d in segments for box in boxes): continue
                        # Zero-length native pins (e.g. fuse terminals on the
                        # graphic edge) may enter the .35 mm display margin only.
                        body_segments=[*segments[:-1],(segments[-1][0],tuple(round(b[i]+.5*ob[i],6) for i in (0,1)))]
                        if any(crosses(body,c,d) for c,d in body_segments):continue
                        if any(crosses(box, c, d) for c, d in segments for box in fieldboxes): continue
                        if any(contact(c,d,e,f) and not (branch.get('allowCrossings') and proper_cross(c,d,e,f)) for c,d in segments for e,f in wires):continue
                        if any(tuple(p['at']) not in (a, b) and any(on_segment(tuple(p['at']), c, d) for c, d in segments) for p in [*pins, *cp]): continue
                        length = sum(abs(c[0]-d[0])+abs(c[1]-d[1]) for c, d in segments)
                        crossing_points={p for c,d in segments for e,f in wires if (p:=proper_cross(c,d,e,f))}
                        score = length+4*len(segments)+50*len(crossing_points)+sum(overlaps(x, y) for x in fieldboxes for y in boxes)*20
                        if best is None or score < best[0]:
                            other_labels=[]
                            for label in moving_labels:
                                if label.kind not in {'label','global_label'}:continue
                                oldpin=next(p for p in oldpins if tuple(p['at'])==tuple(location(label)[:2]))
                                if oldpin['number']==branch['target']['pin']:continue
                                np=next(p for p in cp if p['number']==oldpin['number']);angle=(np['orientation']+180)%360
                                dummy=parse('(global_label '+safe_string(label.values[1])+' (at '+number(np['at'][0])+' '+number(np['at'][1])+' '+number(angle%180)+') (effects (font (size 1.27 1.27)) (justify '+('right' if angle>=180 else 'left')+')))')
                                other_labels.append(label_box(dummy))
                            if any(overlaps(v,box) for v in other_labels for box in boxes):continue
                            if any(crosses(v,c,d) for v in other_labels for c,d in [*wires,*segments]):continue
                            stub=plan_stub(path,netname,[*boxes,body,*fieldboxes,*other_labels],[*wires,*segments],w,h)
                            if stub:best = (score, candidate, cp, path, rotation, delta, stub,sorted(crossing_points))
        if best is None: fail('支路 '+branch['id']+' 无满足净空的有界候选，未退回标签表达。')
        _, candidate, cp, path, rotation, delta, stub,crossing_points = best
        # Match moved pins by number, since their positions also change on rotation.
        moved = {tuple(p['at']): next(q['at'] for q in cp if q['number'] == p['number']) for p in oldpins}
        edits = [(target.start, target.end, candidate.raw)]
        old_target_pin = next(p for p in oldpins if p['number'] == branch['target']['pin'])
        endpoint_labels = [n for n in page['objects'] if n.kind in {'label','global_label'} and
                           tuple(location(n)[:2]) in (a,tuple(old_target_pin['at']))]
        endpoint_ids = {object_id(n) for n in endpoint_labels}
        edits.extend((n.start,n.end,'') for n in endpoint_labels)
        for label in moving_labels:
            if object_id(label) in endpoint_ids: continue
            dest = moved[tuple(location(label)[:2])]; pos = label.one('at')
            at = location(label)
            raw = label.raw
            if label.kind in {'label','global_label'}:
                pin = next(p for p in cp if p['at'] == dest)
                angle=(pin['orientation']+180)%360
                raw=raw[:pos.start-label.start]+'(at '+number(dest[0])+' '+number(dest[1])+' '+number(angle%180)+')'+raw[pos.end-label.start:]
                raw=raw.replace('(justify left)', '(justify '+('right' if angle>=180 else 'left')+')').replace('(justify right)', '(justify '+('right' if angle>=180 else 'left')+')')
            else:
                raw=raw[:pos.start-label.start]+'(at '+number(dest[0])+' '+number(dest[1])+')'+raw[pos.end-label.start:]
            edits.append((label.start,label.end,raw))
        text = page['text']
        for x, y, v in sorted(edits, reverse=True): text = text[:x]+v+text[y:]
        additions = []
        for c, d in zip(path, path[1:]):
            additions.append('(wire (pts (xy '+number(c[0])+' '+number(c[1])+') (xy '+number(d[0])+' '+number(d[1])+')) (stroke (width 0.1524) (type default)) (uuid "'+str(uuid.uuid4())+'"))')
        tree = parse(text); end = tree.end-1
        text = text[:end]+'\n'+'\n'.join(additions)+text[end:]
        page['path'].write_text(text, encoding='utf8')
        # Put one preserved global name on a clear branch stub, away from pins.
        # The main wire is split at the T so native connectivity is unambiguous.
        current = load_pages(source, entry)[relative]
        existing=[]
        for n in current['objects']:
            if n.kind=='wire':
                pts=[tuple(map(float,p.values[1:3])) for p in n.one('pts').children('xy')]
                existing.append((n,pts[0],pts[1]))
        mid,end,label,c,d=stub
        def wire(e,f):
            return '(wire (pts (xy '+number(e[0])+' '+number(e[1])+') (xy '+number(f[0])+' '+number(f[1])+')) (stroke (width 0.1524) (type default)) (uuid "'+str(uuid.uuid4())+'"))'
        main=next(n for n,e,f in existing if (e,f)==(c,d))
        text=current['text'];text=text[:main.start]+wire(c,mid)+'\n'+wire(mid,d)+text[main.end:]
        endpos=parse(text).end-1
        text=text[:endpos]+wire(mid,end)+'\n'+label+'\n(junction (at '+number(mid[0])+' '+number(mid[1])+') (diameter 0) (color 0 0 0 0) (uuid "'+str(uuid.uuid4())+'"))\n'+text[endpos:]
        current['path'].write_text(text,encoding='utf8')
        reserved=[object_id(n) for n in moving_labels if n.kind in {'label','global_label'} and object_id(n) not in endpoint_ids]
        reserved.append(object_id(parse(label)))
        reports.append(dict(**branch, sheet=relative, points=path, beforeAt=old, afterAt=location(candidate),
                            reservedLabelIds=reserved,
                            nonConnectingCrossings=crossing_points,
                            lengthMm=round(sum(abs(c[0]-d[0])+abs(c[1]-d[1]) for c,d in zip(path,path[1:])),6)))
    return dict(version=1, policy='declared-pin-branches-v1', branches=reports,
                scope='Same page allocation; explicit same-net pin pairs; two-pin targets or single-angle declared protection/driver targets. Fixed-target channel routing is bounded. Roles are Agent supplied, not electrically inferred.')


def tidy_pages(source, entry, cancel):
    reports = []
    for relative, page in load_pages(source, entry).items():
        ids = [object_id(n) for n in page['tree'].children('symbol') if not properties(n)['Reference'].values[2].startswith('#')]
        if not ids: continue
        edits, report = tidy(page, dict(sheet=relative, symbolIds=ids, maxShiftMm=15.24), cancel)
        text = page['text']
        for a,b,v in sorted(edits, reverse=True): text=text[:a]+v+text[b:]
        page['path'].write_text(text, encoding='utf8'); reports.append(report)
    return reports
