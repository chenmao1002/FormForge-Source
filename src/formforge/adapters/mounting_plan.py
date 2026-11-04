"""Executable symmetric mounting placement and versioned explicit exceptions."""
import math
from pathlib import Path
from .board_outline import contains
from .importer import digest
from ..store import DomainError

DRILLS={'M2':2.2,'M2.5':2.7,'M3':3.2}


def distance_segment(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];d=dx*dx+dy*dy
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/d)) if d else 0
    return math.dist(p,[a[0]+t*dx,a[1]+t*dy])


def conflicts(snapshot,outline,positions,drill,envelope,rules,ignore=()):
    findings=[];ignore=set(ignore)
    for at in positions:
        if not contains(outline,at,envelope/2):findings.append(dict(kind='envelope_outside_board',at=at))
        for fp in snapshot['footprints']:
            if fp['id'] in ignore:continue
            box=fp['bounds'];x,y=box['origin'];w,h=box['size']
            near=[max(x,min(at[0],x+w)),max(y,min(at[1],y+h))]
            if math.dist(at,near)<envelope/2+.25:
                findings.append(dict(kind='component_envelope',at=at,reference=fp['reference']))
        for t in snapshot['tracks']:
            if t['type']=='arc':
                # Native DRC checks curved copper; no linear approximation can certify it.
                findings.append(dict(kind='arc_requires_explicit_review',objectId=t['id']));continue
            distance=math.dist(at,t['at']) if t['type']=='via' else distance_segment(at,t['start'],t['end'])
            if distance<drill/2+t['width']/2+rules.get('holeClearance',.25):
                findings.append(dict(kind='hole_copper_clearance',at=at,objectId=t['id']))
    for i,a in enumerate(positions):
        for b in positions[i+1:]:
            if math.dist(a,b)<envelope+.5:findings.append(dict(kind='overlapping_mount_envelopes',at=a,other=b))
    return findings


def plan(snapshot,outline,rules,request,config):
    mode=request['mode'];drill=DRILLS[request['profile']];existing=snapshot.get('mountingFeatures',[])
    if mode=='none':
        return dict(request=request,positions=[],operations=[],status='explicit_exception',scope='No automatic removal of existing holes.')
    # Existing mounting holes are reused only at matching positions; never moved implicitly.
    attempts=[];x,y=outline['origin'];w,h=outline['width'],outline['height']
    if mode=='custom':candidates=[request['positions']]
    else:
        steps=[round(request['edgeInsetMm']+i*.5,6) for i in range(int((request['maxInsetMm']-request['edgeInsetMm'])/.5)+1)]
        candidates=[[[x+dx,y+dy],[x+w-dx,y+dy],[x+dx,y+h-dy],[x+w-dx,y+h-dy]]
                    for dx,dy in sorted(((a,b) for a in steps for b in steps),key=lambda p:(sum(p),abs(p[0]-p[1]),p))
                    if dx<w/2 and dy<h/2]
    chosen=None;reuse=[]
    for positions in candidates:
        matched=[f for f in existing if abs(f['drill']-drill)<1e-6 and any(math.dist(f['at'],p)<1e-6 for p in positions)]
        blocked=conflicts(snapshot,outline,positions,drill,request['envelopeDiameterMm'],rules,[f['id'] for f in matched])
        attempts.append(dict(positions=positions,findings=blocked[:12]))
        if not blocked:chosen=positions;reuse=matched;break
    if chosen is None:
        kinds=sorted({f['kind'] for a in attempts for f in a['findings']})
        raise DomainError('mounting_plan_blocked','四角固定规划没有合法空间；请调整布局/板框，或提供有reason/source的custom或none例外。阻碍：'+','.join(kinds),422)
    stock=Path(config.kicad_cli).parent.parent/'share/kicad/footprints/MountingHole.pretty'/f'MountingHole_{drill}mm_{request["profile"]}.kicad_mod'
    if not stock.is_file():raise DomainError('dependency_missing','固定孔原生库不可用。')
    source=dict(path=str(stock),sha256=digest(stock),uri='https://gitlab.com/kicad/libraries/kicad-footprints',
                version='Installed KiCad library snapshot',license='KiCad library exception',note='Automatic mounting profile; exact installed source frozen by SHA-256.')
    refs={f['reference'] for f in snapshot['footprints']};operations=[];i=1
    for at in chosen:
        if any(math.dist(f['at'],at)<1e-6 for f in reuse):continue
        while 'MH'+str(i) in refs:i+=1
        ref='MH'+str(i);refs.add(ref)
        operations.append(dict(kind='add_mounting_hole',reference=ref,at=at,drill=drill,source=source))
    return dict(request=request,positions=chosen,operations=operations,reusedIds=[f['id'] for f in reuse],
                attempts=attempts,status='planned',drill=drill,outline=outline,
                scope='Conservative two-face footprint bounds and linear copper preflight; filled zones and exact drill clearance require final native DRC. Envelope is a planning allowance, not screw qualification.')


def proof(snapshot,outline,rules,contract):
    if not contract:return dict(status='not_planned',passed=True)
    if contract['request']['mode']=='none':return dict(status='explicit_exception',passed=True,reason=contract['request']['reason'],source=contract['request']['source'])
    records=snapshot.get('mountingFeatures',[]);ids=contract['holeIds'];selected=[f for f in records if f['id'] in ids]
    actual_positions=sorted(tuple(f['at']) for f in selected);expected_positions=sorted(tuple(p) for p in contract['positions'])
    exact=len(selected)==len(ids)==len(expected_positions) and all(math.dist(a,b)<=1.1e-6 for a,b in zip(actual_positions,expected_positions))
    issues=conflicts(snapshot,outline,contract['positions'],contract['drill'],contract['request']['envelopeDiameterMm'],rules,ids)
    old_outline=contract.get('outline')
    if old_outline is not None and (math.dist(old_outline['origin'],outline['origin'])>2e-6 or any(abs(old_outline.get(k,0)-outline.get(k,0))>2e-6 for k in ('width','height','cornerRadius'))):
        issues.append(dict(kind='outline_changed_replan_required'))
    return dict(passed=exact and not issues,status='matched' if exact and not issues else 'blocked',positionsMatched=exact,findings=issues,request=contract['request'])
