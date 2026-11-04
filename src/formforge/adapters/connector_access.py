"""Native PCB Edge markers are mechanical evidence, never guessed from a name."""
import math


def review(board):
    import pcbnew as pcb
    def xy(p):return [round(pcb.ToMM(p.x),6),round(pcb.ToMM(p.y),6)]
    edges=[g for g in board.GetDrawings()if isinstance(g,pcb.PCB_SHAPE) and g.GetLayer()==pcb.Edge_Cuts]
    native_edges=edges[:];bounds=[]
    for g in edges:
        box=g.GetBoundingBox();a=xy(box.GetOrigin());size=xy(box.GetSize());half=pcb.ToMM(g.GetWidth())/2
        bounds.append(([a[i]+half for i in (0,1)],[a[i]+size[i]-half for i in (0,1)]))
    if not bounds:return dict(policy='native-connector-edge-datum-v1',interfaces=[],unconfirmed=[dict(reason='No native board outline')],failedCount=0)
    lo=[min(r[0][i]for r in bounds)for i in (0,1)];hi=[max(r[1][i]for r in bounds)for i in (0,1)]
    rows=[];unknown=[]
    for fp in board.GetFootprints():
        graphics=list(fp.GraphicalItems());texts=[g for g in graphics if isinstance(g,pcb.PCB_TEXT) and g.GetText().strip().lower()=='pcb edge']
        if not texts:
            if fp.GetReference().startswith('J'):unknown.append(dict(reference=fp.GetReference(),reason='No native PCB Edge datum; require source-backed mating datum before layout approval'))
            continue
        lines=[g for g in graphics if isinstance(g,pcb.PCB_SHAPE) and g.GetShape()==pcb.SHAPE_T_SEGMENT and any(g.GetLayer()==t.GetLayer()for t in texts)]
        if len(lines)!=1:
            unknown.append(dict(reference=fp.GetReference(),reason='Ambiguous PCB Edge marker'));continue
        a,b=xy(lines[0].GetStart()),xy(lines[0].GetEnd());mid=[(a[i]+b[i])/2 for i in (0,1)]
        pads=[xy(p.GetPosition())for p in fp.Pads()if p.GetNumber()]
        if not pads:continue
        centre=[sum(p[i]for p in pads)/len(pads)for i in (0,1)]
        tangent=[b[i]-a[i]for i in (0,1)];length=math.hypot(*tangent)
        if length<.1:continue
        normal=[-tangent[1]/length,tangent[0]/length]
        if sum((mid[i]-centre[i])*normal[i]for i in (0,1))<0:normal=[-v for v in normal]
        # A datum must separate the pads from the mating side. Ambiguous
        # outlines/markers cannot establish direction and are not auto-placed.
        if any(sum((p[i]-mid[i])*normal[i]for i in (0,1))>.05 for p in pads):
            unknown.append(dict(reference=fp.GetReference(),reason='Pads cross the claimed mating datum'));continue
        pos=xy(fp.GetPosition());edges=[('left',0,lo[0],[-1,0]),('right',0,hi[0],[1,0]),('top',1,lo[1],[0,-1]),('bottom',1,hi[1],[0,1])]
        aligned=[r for r in edges if abs(mid[r[1]]-r[2])<=.25 and sum(normal[i]*r[3][i]for i in (0,1))>.999]
        edge,axis,value,outward=min(aligned or edges,key=lambda r:abs(pos[r[1]]-r[2]))
        dot=sum(normal[i]*outward[i]for i in (0,1));gap=abs(mid[axis]-value)
        along=1-axis
        supported=any(g.GetShape()==pcb.SHAPE_T_SEGMENT and
            abs(xy(g.GetStart())[axis]-value)<.01 and abs(xy(g.GetEnd())[axis]-value)<.01 and
            min(xy(g.GetStart())[along],xy(g.GetEnd())[along])<=mid[along]-length/2 and
            max(xy(g.GetStart())[along],xy(g.GetEnd())[along])>=mid[along]+length/2 for g in native_edges)
        if not supported:
            unknown.append(dict(reference=fp.GetReference(),reason='No continuous native outer straight edge spans the mating datum; require explicit mechanical placement'));continue
        status='passed' if dot>.999 and gap<=.25 else 'inward' if dot<-.999 else 'misaligned'
        delta=math.atan2(normal[1],normal[0])-math.atan2(outward[1],outward[0])
        rot=(fp.GetOrientationDegrees()+math.degrees(delta))%360
        c,s=math.cos(delta),math.sin(delta);rel=[mid[i]-pos[i]for i in (0,1)]
        # KiCad positive angles rotate clockwise in mathematical coordinates.
        relative=[c*rel[0]+s*rel[1],-s*rel[0]+c*rel[1]]
        target=pos[:];target[axis]=value-relative[axis]
        rows.append(dict(reference=fp.GetReference(),footprintId=fp.m_Uuid.AsString(),at=pos,rotation=fp.GetOrientationDegrees(),
            markerId=lines[0].m_Uuid.AsString(),marker=[a,b],matingOutward=normal,nearestEdge=edge,edgeGapMm=round(gap,6),status=status,
            suggestedPlacement=dict(reference=fp.GetReference(),at=[round(v,6)for v in target],rotation=round(rot)%360),
            evidence='Native PCB Edge text and sole same-layer straight datum; numbered pads must be behind the datum'))
    return dict(policy='native-connector-edge-datum-v1',interfaces=rows,unconfirmed=unknown,
        failedCount=sum(r['status']!='passed'for r in rows),scope='Rectangular board-edge orientation and datum alignment only. Actual plug dimensions, enclosure and 3D insertion obstruction remain separate; unknown datums never mean accessible.')


def changed_failures(before,after):
    old={r['footprintId']:r for r in before.get('layoutFeedback',{}).get('connectorAccess',{}).get('interfaces',[])}
    result=[r for r in after.get('layoutFeedback',{}).get('connectorAccess',{}).get('interfaces',[])if r['status']!='passed' and
        (r['footprintId']not in old or old[r['footprintId']]['status']=='passed' or
         any(r[k]!=old[r['footprintId']][k]for k in ['at','rotation']))]
    known={r['reference'] for r in old.values()}
    result.extend(r for r in after.get('layoutFeedback',{}).get('connectorAccess',{}).get('unconfirmed',[]) if r.get('reference') in known)
    return result
