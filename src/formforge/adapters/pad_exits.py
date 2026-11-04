"""Centred native pad escapes, with radial 45-degree exits for true circles."""
import math
import hashlib
import json
from collections import defaultdict

POLICY='centered-native-pad-exit-v3'
NECK=.15
ERROR=.001
CENTER_TOL=.000002


def clip(polygon,a,b):
    """Convex half-plane clipping; inputs in mm, no sampled clearance proof."""
    sign=1 if sum(p[0]*q[1]-q[0]*p[1]for p,q in zip(polygon,polygon[1:]+polygon[:1]))>0 else -1
    lo,hi=0.,1.;d=[b[i]-a[i]for i in (0,1)]
    for p,q in zip(polygon,polygon[1:]+polygon[:1]):
        edge=[q[i]-p[i]for i in (0,1)];c=sign*(edge[0]*(a[1]-p[1])-edge[1]*(a[0]-p[0]));v=sign*(edge[0]*d[1]-edge[1]*d[0])
        if abs(v)<1e-12:
            if c<-1e-8:return None
        elif v>0:lo=max(lo,-c/v)
        else:hi=min(hi,-c/v)
        if lo>hi+1e-8:return None
    return max(0.,lo),min(1.,hi)


def contains(polygon,at):return clip(polygon,at,at)is not None


def convex(polygon):
    cross=[(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])for a,b,c in zip(polygon,polygon[1:]+polygon[:1],polygon[2:]+polygon[:2])]
    return len(polygon)>2 and (all(v>=-1e-9 for v in cross)or all(v<=1e-9 for v in cross))


def native_circle(pad,layer):
    """Only the native per-layer circular copper, not a circular-looking land."""
    import pcbnew as pcb
    size=pad.GetSize(layer);offset=pad.GetOffset(layer)
    return pad.GetShape(layer)==pcb.PAD_SHAPE_CIRCLE and size.x==size.y and size.x>0 and offset.x==offset.y==0


def audit(pads,tracks,neck=NECK,touches=None):
    """Pads contain native polygons per copper layer; vias are not planar turns.

    Track clipping sees off-centre escapes and through-routes. Straight runs are
    accumulated across objects/width changes. A turn inside another same-net
    pad is a direct terminal connection rather than an exposed short neck.
    Optional native finite-width contact detects diagonal side attachments.
    """
    groups=defaultdict(list);padgroups=defaultdict(list)
    for t in tracks:
        if t['type']=='segment':groups[t['net'],t['layer']].append(t)
    for p in pads:padgroups[p['net'],p['layer']].append(p)
    findings=[];covered=set();exits=0;seen=set()
    for p in pads:
        polygon=p['polygon'];net,layer=p['net'],p['layer'];segments=groups[net,layer];covered.add(p['id'])
        shape_hash=hashlib.sha256(json.dumps([p['at'],polygon,net,layer],separators=(',',':')).encode()).hexdigest()
        def other_pad(at):return any(q['id']!=p['id']and contains(q['polygon'],at)for q in padgroups[net,layer])
        for t in segments:
            a,b=t['start'],t['end'];d=[b[i]-a[i]for i in (0,1)];length=math.hypot(*d)
            if length<1e-8:continue
            interval=clip(polygon,a,b);diagonal=abs(d[0])>1e-7 and abs(d[1])>1e-7
            crossings=[]
            if interval is not None:
                lo,hi=interval
                if hi<1-1e-8:crossings.append(([a[i]+d[i]*hi for i in (0,1)],[x/length for x in d]))
                if lo>1e-8:crossings.append(([a[i]+d[i]*lo for i in (0,1)],[-x/length for x in d]))
            elif touches and touches(p,t):
                # Actual copper touching the side of a pad is still an escape,
                # even when the segment centreline misses the pad perimeter.
                if not any(q['id']!=p['id']and contains(q['polygon'],a)and contains(q['polygon'],b)for q in padgroups[net,layer]):
                    fraction=max(0.,min(1.,sum((p['at'][i]-a[i])*d[i]for i in (0,1))/(length*length)))
                    contact=[a[i]+fraction*d[i]for i in (0,1)]
                    crossings.append((contact,[x/length for x in d]))
            for at,direction in crossings:
                beyond=[at[i]+direction[i]*.002 for i in (0,1)]
                if other_pad(beyond):continue
                key=(p['id'],layer,tuple(round(x,5)for x in at),tuple(round(x,5)for x in direction))
                if key in seen:
                    # Duplicate copper must not hide a new/reworked escape
                    # behind the unchanged object's finding and UUID.
                    for f in findings:
                        if f['padId']==p['id']and f['layer']==layer and f['exitAt']==[round(v,6)for v in at]and f['direction']==[round(v,6)for v in direction]:
                            f['objectIds']=sorted(set(f['objectIds'])|{t['id']})
                    continue
                seen.add(key);exits+=1
                row=dict(reference=p['reference'],pin=p['pin'],padId=p['id'],padAt=p['at'],padGeometryHash=shape_hash,net=net,layer=layer,exitAt=[round(v,6)for v in at],direction=[round(v,6)for v in direction],requiredStraightMm=neck,objectIds=[t['id']])
                radial_diagonal=diagonal and p.get('nativeCircle',False) and abs(abs(direction[0])-abs(direction[1]))<=1e-7
                if diagonal and not radial_diagonal:
                    findings.append(dict(**row,kind='diagonal-pad-exit'));continue
                offset=abs((p['at'][0]-at[0])*direction[1]-(p['at'][1]-at[1])*direction[0])
                if offset>CENTER_TOL:
                    findings.append(dict(**row,kind='off-center-pad-exit',centerOffsetMm=round(offset,6),reason='escape_axis_misses_pad_center'))
                axis=direction;intervals=[];ids=set()
                for line in segments:
                    u,v=line['start'],line['end']
                    if abs((u[0]-at[0])*axis[1]-(u[1]-at[1])*axis[0])>1e-6 or abs((v[0]-at[0])*axis[1]-(v[1]-at[1])*axis[0])>1e-6:continue
                    s,z=sorted(sum((point[i]-at[i])*axis[i]for i in (0,1))for point in (u,v));intervals.append((s,z,line['id']))
                if offset<=CENTER_TOL:
                    # A hidden diagonal or transverse jog inside the pad must
                    # not replace the straight centre-to-edge lead.
                    start=sum((p['at'][i]-at[i])*axis[i]for i in (0,1));covered_to=start
                    for s,z,id in sorted(intervals):
                        if z<covered_to-CENTER_TOL:continue
                        if s>covered_to+CENTER_TOL:break
                        covered_to=max(covered_to,z)
                    if covered_to<-CENTER_TOL:
                        findings.append(dict(**row,kind='off-center-pad-exit',centerOffsetMm=0.,reason='straight_lead_does_not_reach_pad_center'))
                reach=0.
                for s,z,id in sorted(intervals):
                    if s>reach+1e-6:break
                    if z>=-1e-6:
                        reach=max(reach,z)
                        if s<neck+.002:ids.add(id)
                for line in segments:
                    u,v=line['start'],line['end'];ld=[v[i]-u[i]for i in (0,1)];cross=ld[0]*axis[1]-ld[1]*axis[0]
                    if abs(cross)<1e-8:continue
                    parameter=-((u[0]-at[0])*axis[1]-(u[1]-at[1])*axis[0])/cross
                    if not -1e-6<=parameter<=1+1e-6:continue
                    turn=[u[i]+parameter*ld[i]for i in (0,1)];distance=sum((turn[i]-at[i])*axis[i]for i in (0,1))
                    if distance<-.002 or distance>reach+1e-6 or distance>=neck-.002 or other_pad(turn):continue
                    findings.append({**row,'kind':'short-pad-neck','straightLengthMm':round(max(0.,distance),6),'turnAt':[round(v,6)for v in turn],'objectIds':sorted(ids|{line['id']})})
    unique={ (f['padId'],f['layer'],f['kind'],tuple(f['exitAt']),tuple(f.get('turnAt',[]))):f for f in findings }
    findings=sorted(unique.values(),key=lambda r:(r['reference'],r['pin'],r['layer'],r['exitAt'],r['kind']))
    return dict(policy=POLICY,minimumStraightMm=neck,polygonErrorMm=ERROR,coveredPadCount=len(covered),exitCount=exits,
        diagonalExitCount=sum(f['kind']=='diagonal-pad-exit'for f in findings),offCenterExitCount=sum(f['kind']=='off-center-pad-exit'for f in findings),centerToleranceMm=CENTER_TOL,shortNeckCount=sum(f['kind']=='short-pad-neck'for f in findings),findings=findings,uncoveredPads=[],
        scope='Linear copper escapes from native convex outer pad polygons through the native pad centre with continuous centre-to-edge lead. True native circles additionally allow radial 45-degree exits; other lands require cardinal exits. Outside straight length aggregates across objects; direct straight terminal connections are allowed. Not an assembly, solderability or PI proof.')


def native_audit(board,neck=NECK,footprint_ids=None,ignore_track_ids=()):
    import pcbnew as pcb
    def uid(item):return item.m_Uuid.AsString()
    def xy(p):return[round(pcb.ToMM(p.x),6),round(pcb.ToMM(p.y),6)]
    pads=[];native={};uncovered=[]
    layers=list(board.GetEnabledLayers().CuStack())
    ignored=set(ignore_track_ids)
    for fp in board.GetFootprints():
        if footprint_ids is not None and uid(fp) not in footprint_ids:continue
        for p in fp.Pads():
            if not p.GetNetCode():continue
            for layer in layers:
                if not p.IsOnLayer(layer):continue
                poly=pcb.SHAPE_POLY_SET();p.TransformShapeToPolygon(poly,layer,0,pcb.FromMM(ERROR),pcb.ERROR_OUTSIDE)
                polygon=[]
                if poly.OutlineCount()==1 and not poly.HoleCount(0):
                    ring=poly.COutline(0);polygon=[xy(ring.CPoint(i))for i in range(ring.PointCount())]
                row=dict(id=uid(p),reference=fp.GetReference(),pin=p.GetNumber(),at=xy(p.GetPosition()),net=p.GetNetname(),layer=board.GetLayerName(layer))
                if not convex(polygon):uncovered.append(dict(**row,reason='unsupported_nonconvex_or_compound_pad'));continue
                native[uid(p),row['layer']]=p.GetEffectiveShape(layer);pads.append(dict(**row,polygon=polygon,nativeCircle=native_circle(p,layer)))
    tracks=[];by_id={}
    for t in board.GetTracks():
        if uid(t) in ignored:continue
        if isinstance(t,(pcb.PCB_VIA,pcb.PCB_ARC)):continue
        row=dict(id=uid(t),type='segment',net=t.GetNetname(),layer=board.GetLayerName(t.GetLayer()),start=xy(t.GetStart()),end=xy(t.GetEnd()),width=pcb.ToMM(t.GetWidth()))
        by_id[row['id']]=t;tracks.append(row)
    def touches(p,t):return native[p['id'],p['layer']].Collide(by_id[t['id']].GetEffectiveShape(),0)
    report=audit(pads,tracks,neck,touches);report['uncoveredPads']=uncovered
    return report


def proof(previous,actual):
    report=actual.get('layoutFeedback',{}).get('padExits',{})
    if report.get('policy')!=POLICY:return dict(policy=POLICY,passed=False,reason='native_pad_exit_audit_missing',findings=[])
    old=previous.get('layoutFeedback',{}).get('padExits',{}).get('findings',[])
    def key(r):return(r['padId'],r['layer'],r['kind'],r.get('padGeometryHash'),tuple(r['exitAt']),tuple(r.get('turnAt',[])))
    before={t['id']:t for t in previous.get('tracks',[])}
    def geometry(t):return{k:t.get(k)for k in ('type','net','layer','start','end','at','width','drill')}
    changed={t['id']for t in actual.get('tracks',[])if t['id']not in before or geometry(t)!=geometry(before[t['id']])}
    keys={key(r)for r in old};blocked=[r for r in report['findings']if key(r)not in keys or changed.intersection(r['objectIds'])]
    return dict(policy=POLICY,passed=not blocked,minimumStraightMm=report['minimumStraightMm'],blockingExitCount=len(blocked),
        unchangedExistingFindingCount=len(report['findings'])-len(blocked),diagonalExitCount=report['diagonalExitCount'],offCenterExitCount=report['offCenterExitCount'],shortNeckCount=report['shortNeckCount'],findings=blocked,
        uncoveredPads=report['uncoveredPads'],scope=report['scope'])
