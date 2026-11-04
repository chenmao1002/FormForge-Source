"""Limited SMD pad-gap inference and finite-width native collision checks.

Adapted from 1021C's body-core guard/cores idea; see migration manifest.
Read-only. Fab graphics describe an inferred gap, never a certified body model.
"""
import math
import pcbnew as pcb

POLICY = 'smd-pad-gap-v1'
SCOPE = 'Inferred surface SMD pad gaps only; unknown footprints, complete bodies, assembly clearance and manufacturing remain unverified.'


def uid(item):return item.m_Uuid.AsString()
def pt(p):return pcb.VECTOR2I(*(round(v*1_000_000) for v in p))
def pair(p):return (p.x,p.y)


def fab_outline(fp,layer):
    """One closed convex segment/rectangle ring; no union of loose markings."""
    edges=[];rings=[]
    for s in fp.GraphicalItems():
        if not isinstance(s,pcb.PCB_SHAPE) or s.GetLayer()!=layer:continue
        if s.GetShape()==pcb.SHAPE_T_RECT:
            a,b=pair(s.GetStart()),pair(s.GetEnd())
            rings.append([(a[0],a[1]),(b[0],a[1]),(b[0],b[1]),(a[0],b[1])])
        elif s.GetShape()==pcb.SHAPE_T_SEGMENT:edges.append((pair(s.GetStart()),pair(s.GetEnd())))
        elif s.GetShape()==pcb.SHAPE_T_POLY:
            poly=s.GetPolyShape()
            if poly.OutlineCount()!=1 or poly.HoleCount(0):return None,'unsupported_fab_shape'
            line=poly.COutline(0)
            if not line.IsClosed() or line.ArcCount():return None,'missing_or_ambiguous_closed_fab'
            rings.append([pair(line.CPoint(i)) for i in range(line.PointCount())])
        else:return None,'unsupported_fab_shape'
    adjacency={}
    for a,b in edges:
        if a==b:return None,'degenerate_fab'
        adjacency.setdefault(a,[]).append(b);adjacency.setdefault(b,[]).append(a)
    unseen=set(adjacency)
    while unseen:
        start=min(unseen);component=set();queue=[start]
        while queue:
            at=queue.pop()
            if at in component:continue
            component.add(at);queue.extend(n for n in adjacency[at] if n not in component)
        unseen-=component
        # Open pin-one marks are not body boundaries.
        if any(len(adjacency[p])!=2 for p in component):continue
        ring=[start];previous=None;at=start
        while True:
            nxt=next(p for p in adjacency[at] if p!=previous)
            if nxt==start:break
            if nxt in ring:return None,'ambiguous_fab'
            ring.append(nxt);previous,at=at,nxt
        rings.append(ring)
    if len(rings)!=1:return None,'missing_or_ambiguous_closed_fab'
    ring=rings[0]
    if len(ring)>1 and ring[0]==ring[-1]:ring=ring[:-1]
    cross=[(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]) for a,b,c in zip(ring,ring[1:]+ring[:1],ring[2:]+ring[:2])]
    if len(ring)<3 or not any(cross) or any(c>0 for c in cross) and any(c<0 for c in cross):return None,'nonconvex_fab'
    sign=1 if any(c>0 for c in cross) else -1
    if any(((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]))*sign<0 for a,b in zip(ring,ring[1:]+ring[:1]) for p in ring):return None,'nonconvex_fab'
    return ring,None


def clip_rect(polygon,rect):
    result=polygon
    for axis,value,sign in ((0,rect[0],1),(0,rect[2],-1),(1,rect[1],1),(1,rect[3],-1)):
        previous=result;result=[]
        if not previous:break
        for a,b in zip(previous[-1:]+previous[:-1],previous):
            ia=(a[axis]-value)*sign>=0;ib=(b[axis]-value)*sign>=0
            if ia!=ib:
                t=(value-a[axis])/(b[axis]-a[axis]);result.append(tuple(a[i]+t*(b[i]-a[i]) for i in (0,1)))
            if ib:result.append(b)
    return result


def infer(fp,board):
    row=dict(footprintId=uid(fp),reference=fp.GetReference(),footprint=fp.GetFPID().GetUniStringLibId(),
             locked=fp.IsLocked(),layer=board.GetLayerName(fp.GetLayer()),status='uncovered')
    def skip(reason):return {**row,'reason':reason},None
    local=pcb.FOOTPRINT(fp);local.SetOrientationDegrees(0);local.SetPosition(pcb.VECTOR2I(0,0))
    pads=list(local.Pads());layer=fp.GetLayer()
    if layer not in (pcb.F_Cu,pcb.B_Cu):return skip('unsupported_footprint_layer')
    if len(pads)<2 or any(p.GetAttribute()!=pcb.PAD_ATTRIB_SMD or not p.IsOnLayer(layer) for p in pads):return skip('requires_pure_surface_smd_pads')
    if any(p.GetShape()==pcb.PAD_SHAPE_CUSTOM for p in pads):return skip('custom_pad_geometry')
    polygon,reason=fab_outline(local,pcb.F_Fab if layer==pcb.F_Cu else pcb.B_Fab)
    if reason:return skip(reason)
    xs=[p.GetPosition().x for p in pads];ys=[p.GetPosition().y for p in pads]
    mx=(min(xs)+max(xs))/2;my=(min(ys)+max(ys))/2
    rect=[min(p[0] for p in polygon),min(p[1] for p in polygon),max(p[0] for p in polygon),max(p[1] for p in polygon)]
    boxes=[p.GetBoundingBox() for p in pads]
    if any(b.GetLeft()<mx<b.GetRight() and b.GetTop()<my<b.GetBottom() for b in boxes):return skip('center_or_exposed_pad')
    # Two opposing rows also cover common 2-pad passives and 4-pad crystals.
    def edge(v,values):return min(abs(v-min(values)),abs(v-max(values)))<=10 # native rotation rounding, 10nm
    two_x=max(xs)-min(xs)>10 and all(edge(x,xs) for x in xs)
    two_y=max(ys)-min(ys)>10 and all(edge(y,ys) for y in ys)
    for p,b in zip(pads,boxes):
        x,y=p.GetPosition().x,p.GetPosition().y
        if two_x or not two_y and edge(x,xs):
            if x<mx:rect[0]=max(rect[0],b.GetRight())
            elif x>mx:rect[2]=min(rect[2],b.GetLeft())
            else:return skip('unsupported_pad_arrangement')
        elif two_y or edge(y,ys):
            if y<my:rect[1]=max(rect[1],b.GetBottom())
            elif y>my:rect[3]=min(rect[3],b.GetTop())
            else:return skip('unsupported_pad_arrangement')
        else:return skip('interior_pad_arrangement')
    if rect[2]-rect[0]<200_000 or rect[3]-rect[1]<200_000:return skip('gap_below_0_2mm')
    if any(b.GetRight()>rect[0] and b.GetLeft()<rect[2] and b.GetBottom()>rect[1] and b.GetTop()<rect[3] for b in boxes):return skip('gap_overlaps_pad')
    clipped=clip_rect(polygon,rect)
    if len(clipped)<3:return skip('empty_fab_gap_intersection')
    angle=math.radians(fp.GetOrientationDegrees());co,si=math.cos(angle),math.sin(angle);origin=fp.GetPosition()
    points=[[round((origin.x+x*co+y*si)/1_000_000,6),round((origin.y-x*si+y*co)/1_000_000,6)] for x,y in clipped]
    native=[pt(p) for p in points]
    if len({pair(p) for p in native})<3 or not sum(a.x*b.y-a.y*b.x for a,b in zip(native,native[1:]+native[:1])):
        return skip('empty_fab_gap_intersection')
    shape=pcb.SHAPE_POLY_SET();shape.NewOutline()
    for p in native:shape.Append(p)
    return {**row,'status':'covered','reason':'closed_convex_fab_and_empty_pad_gap','polygonMm':points},shape


class BodyGapGuard:
    def __init__(self,board):
        self.rows=[];self.shapes=[];self.buckets={};self.hits={};self.raster={}
        for fp in sorted(board.GetFootprints(),key=uid):
            row,shape=infer(fp,board);self.rows.append(row)
            if shape is None:continue
            layer=fp.GetLayer();self.shapes.append((layer,row,shape));points=row['polygonMm']
            bounds=(min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))
            for key in self.keys(bounds):self.buckets.setdefault((layer,*key),[]).append((row,shape))

    @staticmethod
    def keys(box):
        return ((x,y) for x in range(math.floor(box[0]/2),math.floor(box[2]/2)+1) for y in range(math.floor(box[1]/2),math.floor(box[3]/2)+1))

    def blocker(self,a,b,width,layer,record=True):
        if not math.isfinite(width) or width<=0 or not all(len(p)==2 and all(math.isfinite(v) for v in p) for p in (a,b)):raise ValueError('Body-gap check requires finite coordinates and positive finite width')
        radius=width/2;seen=set();segment=None
        for key in self.keys((min(a[0],b[0])-radius,min(a[1],b[1])-radius,max(a[0],b[0])+radius,max(a[1],b[1])+radius)):
            for row,shape in self.buckets.get((layer,*key),()):
                id=row['footprintId']
                if id in seen:continue
                seen.add(id)
                if segment is None:segment=pcb.SHAPE_CIRCLE(pt(a),math.ceil(radius*1_000_000)) if a==b else pcb.SHAPE_SEGMENT(pt(a),pt(b),math.ceil(width*1_000_000))
                if not shape.Collide(segment,0):continue
                if record:
                    hit=self.hits.setdefault(id,dict(footprintId=id,reference=row['reference'],locked=row['locked'],rejectedCheckCount=0,samples=[]))
                    hit['rejectedCheckCount']+=1
                    if len(hit['samples'])<3:hit['samples'].append(dict(start=a,end=b,widthMm=width,layer=row['layer']))
                return id
        return None

    def audit(self,board):
        """Native shapes, including arcs and via annuli; no own-net exemption."""
        conflicts=[]
        for item in board.GetTracks():
            box=item.GetBoundingBox();bounds=tuple(v/1_000_000 for v in (box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom()))
            for layer in (pcb.F_Cu,pcb.B_Cu):
                if not item.IsOnLayer(layer):continue
                seen=set();native=None
                for key in self.keys(bounds):
                    for row,shape in self.buckets.get((layer,*key),()):
                        if row['footprintId'] in seen:continue
                        seen.add(row['footprintId'])
                        if native is None:native=item.GetEffectiveShape(layer)
                        if shape.Collide(native,0):conflicts.append(dict(footprintId=row['footprintId'],reference=row['reference'],objectId=uid(item),net=item.GetNetname(),layer=row['layer']))
        return sorted(conflicts,key=lambda c:(c['footprintId'],c['objectId'],c['layer']))

    def raster_blocked(self,shape,at):
        row=next(row for _,row,current in self.shapes if current is shape)
        hit=self.raster.setdefault(row['footprintId'],dict(footprintId=row['footprintId'],reference=row['reference'],locked=row['locked'],maskedCellCheckCount=0,samples=[]))
        hit['maskedCellCheckCount']+=1
        if len(hit['samples'])<3:hit['samples'].append(at)

    def report(self):
        return dict(policy=POLICY,scope=SCOPE,footprintCount=len(self.rows),coveredFootprintCount=len(self.shapes),
                    uncoveredFootprintCount=len(self.rows)-len(self.shapes),footprints=self.rows,
                    searchObstacles=sorted(self.hits.values(),key=lambda r:r['footprintId']),
                    rasterObstacles=sorted(self.raster.values(),key=lambda r:r['footprintId']),
                    interpretation='Search hits are observed rejected checks, not independent collisions or proof of unroutability. Review local escape space; moving a locked footprint needs an explicit decision.')
