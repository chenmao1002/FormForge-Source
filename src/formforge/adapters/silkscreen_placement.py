"""Native reference text placement; preserve content, size, stroke and visibility."""
import math
import pcbnew as pcb
from board_outline import contains


def rect(item):
    b=item.GetBoundingBox()
    return [pcb.ToMM(v) for v in (b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom())]


def overlaps(a,b,gap=.2):
    return not (a[2]+gap<=b[0] or b[2]+gap<=a[0] or a[3]+gap<=b[1] or b[3]+gap<=a[1])


def plan(board,params):
    fps={f.GetReference():f for f in board.GetFootprints()};request=params['request'];outline=params['outline']
    operations=[];rows=[]
    texts=[t for f in fps.values() for t in f.GetFields() if t.IsVisible() and t.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS)]
    texts.extend(t for t in board.GetDrawings() if isinstance(t,pcb.PCB_TEXT) and t.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS))
    for ref in request['references']:
        if ref not in fps:raise ValueError('Silkscreen reference is absent: '+ref)
        fp=fps[ref];field=fp.Reference();layer=field.GetLayer()
        if fp.IsLocked() or not field.IsVisible() or layer not in (pcb.F_SilkS,pcb.B_SilkS):
            rows.append(dict(reference=ref,status='retained_locked_or_hidden'));continue
        old=[pcb.ToMM(field.GetPosition().x),pcb.ToMM(field.GetPosition().y)];angle=field.GetTextAngle().AsDegrees()
        field.SetTextAngle(pcb.EDA_ANGLE(0,pcb.DEGREES_T));box=rect(field)
        # Position may not be the visual centre (left/right justified fields).
        delta=[(box[0]+box[2])/2-old[0],(box[1]+box[3])/2-old[1]]
        half=[(box[2]-box[0])/2,(box[3]-box[1])/2]
        body=fp.GetBoundingBox(False,False);b=[pcb.ToMM(v) for v in (body.GetLeft(),body.GetTop(),body.GetRight(),body.GetBottom())]
        obstacles=[]
        for other in fps.values():
            if other.GetLayer()==fp.GetLayer():
                bb=other.GetBoundingBox(False,False);obstacles.append([pcb.ToMM(v) for v in (bb.GetLeft(),bb.GetTop(),bb.GetRight(),bb.GetBottom())])
            for pad in other.Pads():
                if pad.IsOnLayer(pcb.F_Mask if layer==pcb.F_SilkS else pcb.B_Mask):obstacles.append(rect(pad))
        obstacles.extend(rect(t) for t in texts if t!=field and t.GetLayer()==layer)
        candidates=[old]
        for i in range(int(request['maxOffsetMm']/.5)+1):
            gap=.4+i*.5
            for cx,cy in [((b[0]+b[2])/2,b[1]-gap-half[1]),((b[0]+b[2])/2,b[3]+gap+half[1]),
                          (b[0]-gap-half[0],(b[1]+b[3])/2),(b[2]+gap+half[0],(b[1]+b[3])/2)]:
                candidates.append([cx-delta[0],cy-delta[1]])
        chosen=None
        for at in sorted(candidates,key=lambda p:math.dist(p,old)):
            if math.dist(at,old)>request['maxOffsetMm']+1e-6:continue
            field.SetPosition(pcb.VECTOR2I(*(round(v*1e6) for v in at)));r=rect(field)
            if all(contains(outline,p,.2) for p in ([r[0],r[1]],[r[2],r[1]],[r[2],r[3]],[r[0],r[3]])) and not any(overlaps(r,o) for o in obstacles):
                chosen=[pcb.ToMM(field.GetPosition().x),pcb.ToMM(field.GetPosition().y)];break
        if chosen is None:
            field.SetPosition(pcb.VECTOR2I(*(round(v*1e6) for v in old)));field.SetTextAngle(pcb.EDA_ANGLE(angle,pcb.DEGREES_T))
            rows.append(dict(reference=ref,status='no_space_source_retained'));continue
        if chosen!=old or angle%360!=0:
            operations.append(dict(kind='set_footprint_text',objectId=fp.m_Uuid.AsString(),expectedHash=params['hashes'][fp.m_Uuid.AsString()],field='Reference',at=chosen,rotation=0,visible=True))
        rows.append(dict(reference=ref,status='placed' if chosen!=old or angle%360!=0 else 'unchanged',before=old,after=chosen))
    return dict(operations=operations,placements=rows,scope='Native reference bounding boxes; size/content/stroke preserved. Final native DRC remains required.')
