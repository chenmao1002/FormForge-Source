"""Bounded native terminal-hole evidence; never edits a board or fills a zone."""
import math
import pcbnew as pcb


def uid(item):return item.m_Uuid.AsString()
def xy(p):return [round(pcb.ToMM(p.x),6),round(pcb.ToMM(p.y),6)]
def clone(poly):return poly.CloneDropTriangulation()
def area(poly):return round(poly.Area()/1e12,7)
def ring(chain):return [xy(chain.CPoint(i)) for i in range(chain.PointCount())]
def polygons(poly):return [dict(outer=ring(poly.Outline(i)),holes=[ring(poly.Hole(i,h)) for h in range(poly.HoleCount(i))]) for i in range(poly.OutlineCount())]


class TerminalEvidence:
    def __init__(self,board,anchors,references,layer_ids,reference_net,policy):
        self.board=board;self.references=references;self.layers=layer_ids;self.net=reference_net;self.policy=policy
        self.connected={uid(i) for p in anchors for i in board.GetConnectivity().GetConnectedItems(p)}
        self.pads=[p for f in board.GetFootprints() for p in f.Pads()]
        self.labels={uid(p):f.GetReference()+'.'+p.GetNumber() for f in board.GetFootprints() for p in f.Pads()}
        self.tracks=list(board.GetTracks())
        self.actors=sorted([p for p in self.pads if p.GetAttribute()==pcb.PAD_ATTRIB_PTH and p.GetDrillSize().x]+[v for v in self.tracks if isinstance(v,pcb.PCB_VIA)],key=uid)
        self.returners=[p for p in self.actors if p.GetNetname()==reference_net and uid(p) in self.connected and all(p.IsOnLayer(layer_ids[n]) and poly.Collide(p.GetEffectiveShape(layer_ids[n]),0) for n,poly in references.items())]
        self.fill_error=max(0,board.GetDesignSettings().m_MaxError);self.error=pcb.FromMM(.001)
        self.buffer=self.fill_error+2*self.error;self.cache={};self.envelope_bounds={}

    def nearest_return(self,item):
        choices=sorted((math.dist(xy(item.GetPosition()),xy(r.GetPosition())),uid(r),r) for r in self.returners)
        if not choices:return None
        distance,id,actor=choices[0]
        return dict(id=id,type='via' if isinstance(actor,pcb.PCB_VIA) else 'pth_pad',name=self.labels.get(id,'GND via'),at=xy(actor.GetPosition()),distanceMm=round(distance,6),referenceLayers=list(self.references))

    def geometry(self,name):
        if name in self.cache:return self.cache[name]
        layer=self.layers[name];envelopes={};clearances={};all_obstacles=pcb.SHAPE_POLY_SET()
        for item in self.pads+self.tracks:
            if not item.IsOnLayer(layer) or item.GetNetname()==self.net:continue
            zones=[z.GetLocalClearance() for z in self.board.Zones() if z.IsOnLayer(layer) and z.GetNetname()==self.net and not z.GetIsRuleArea() and z.Outline().Contains(item.GetPosition())]
            # Minimum applicable zone clearance is a conservative lower bound;
            # larger custom-rule clearances can leave an unrecognized residue.
            gap=max(item.GetOwnClearance(layer),min(zones,default=0));poly=pcb.SHAPE_POLY_SET()
            item.TransformShapeToPolygon(poly,layer,gap+self.buffer,self.error,pcb.ERROR_OUTSIDE)
            envelopes[uid(item)]=poly;clearances[uid(item)]=gap;all_obstacles.Append(poly)
        # Native union once, rather than re-union an ever larger board-wide
        # polygon after each copper item (same strategy as signal projection).
        all_obstacles.Simplify()
        bounded=pcb.SHAPE_POLY_SET();unsupported=[];reference=self.references[name]
        for i in range(reference.OutlineCount()):
            for h in range(reference.HoleCount(i)):
                hole=pcb.SHAPE_POLY_SET();hole.AddOutline(reference.Hole(i,h));excess=clone(hole);excess.BooleanSubtract(all_obstacles)
                if excess.OutlineCount():unsupported.append(hole)
                else:bounded.BooleanAdd(hole)
        self.envelope_bounds[name]={id:shape.BBox() for id,shape in envelopes.items()}
        self.cache[name]=(envelopes,clearances,bounded,unsupported)
        return self.cache[name]

    def analyze(self,track,name,missing):
        envelopes,clearances,bounded,unsupported=self.geometry(name);layer=self.layers[name]
        connected={uid(i) for i in self.board.GetConnectivity().GetConnectedItems(track)}
        local=[a for a in self.actors if uid(a) in connected and a.GetNetname()==track.GetNetname() and a.IsOnLayer(layer) and a.IsOnLayer(track.GetLayer())
               and track.GetEffectiveShape(track.GetLayer()).Collide(a.GetEffectiveShape(track.GetLayer()),0)]
        records=[];accepted_ids=set();local_ids={uid(a) for a in local};geometric=pcb.SHAPE_POLY_SET()
        for actor in local:
            id=uid(actor);shape=envelopes.get(id);nearest=self.nearest_return(actor)
            recognizable=clone(shape) if shape is not None else pcb.SHAPE_POLY_SET();recognizable.BooleanIntersection(bounded);geometric.BooleanAdd(recognizable)
            reasons=[]
            if not self.policy:reasons.append('policy_not_enabled')
            else:
                if pcb.ToMM(self.fill_error)>self.policy['maxNativeError']+1e-9:reasons.append('native_fill_error_exceeds_policy')
                if pcb.ToMM(clearances.get(id,0))>self.policy['maxClearance']+1e-9:reasons.append('antipad_clearance_exceeds_policy')
                if nearest is None or nearest['distanceMm']>self.policy['maxReturnDistance']+1e-6:reasons.append('near_anchor_connected_return_not_proven')
            if not recognizable.OutlineCount():reasons.append('closed_hole_not_bounded_by_known_copper')
            if not reasons:accepted_ids.add(id)
            records.append(dict(id=id,name=self.labels.get(id,'signal via'),type='via' if isinstance(actor,pcb.PCB_VIA) else 'pth_pad',at=xy(actor.GetPosition()),
                clearanceMm=round(pcb.ToMM(clearances.get(id,0)),6),nativePhysicalAndGeometricContact=True,nearestReturn=nearest,eligible=not reasons,reasons=reasons))
        def exclusive(excluded,target):
            foreign=pcb.SHAPE_POLY_SET()
            # Only intersections with this already constructed mask can affect
            # its subtraction. Empty/distant masks need no board-wide union.
            # Keep exact polygons and foreign same-net obstacles in the overlap.
            if not target.OutlineCount():return foreign
            bounds=target.BBox();envelope_bounds=self.envelope_bounds[name]
            for id,shape in envelopes.items():
                if id not in excluded and bounds.Intersects(envelope_bounds[id]):foreign.BooleanAdd(shape)
            return foreign
        geometric.BooleanSubtract(exclusive(local_ids,geometric));recognized=clone(missing);recognized.BooleanIntersection(geometric)
        allowed=pcb.SHAPE_POLY_SET()
        for id in sorted(accepted_ids):allowed.BooleanAdd(envelopes[id])
        allowed.BooleanIntersection(bounded);allowed.BooleanSubtract(exclusive(accepted_ids,allowed));allowed.BooleanIntersection(missing)
        residual=clone(missing);residual.BooleanSubtract(allowed);other=clone(missing);other.BooleanSubtract(geometric)
        if not residual.OutlineCount():classification='qualified_terminal_antipad'
        elif not other.OutlineCount() and recognized.OutlineCount():classification='terminal_antipad_conditions_unmet'
        elif recognized.OutlineCount():classification='mixed_terminal_antipad_and_other_gap'
        else:classification='other_reference_gap'
        return residual,dict(classification=classification,rawMissingAreaMm2=area(missing),recognizedTerminalAreaMm2=area(recognized),acceptedTerminalAreaMm2=area(allowed),
            otherGapAreaMm2=area(other),residualAreaMm2=area(residual),terminals=records,nativeFillErrorMm=round(pcb.ToMM(self.fill_error),6),maskNumericalBufferMm=round(pcb.ToMM(self.buffer),6),
            rawMissingPolygons=polygons(missing),remainingPolygons=polygons(residual))
