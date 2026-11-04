"""Bounded, ordinary-net routing proposals; never writes a native board file.

Geometry and connectivity come from the installed native engine. Search is
FormForge's own implementation; native DRC remains the adoption authority.
"""
import heapq
import math
import time

import numpy as np
import pcbnew as pcb

from route_legality import segment_clear
from route_quality import prepare_path, summarize, compare_proposals
from body_gap import BodyGapGuard, POLICY as BODY_POLICY
from routing_rules import native_exit_options,turn_cost,on_segment
from pad_exits import native_circle

LAYERS=(pcb.F_Cu,pcb.B_Cu)
DIRECTIONS=((0,1),(1,1),(1,0),(1,-1),(0,-1),(-1,-1),(-1,0),(-1,1))


def uid(item):return item.m_Uuid.AsString()
def xy(p):return [round(pcb.ToMM(p.x),6),round(pcb.ToMM(p.y),6)]
def point(p):return pcb.VECTOR2I(round(p[0]*1e6),round(p[1]*1e6))
def length(points):return sum(math.dist(a,b) for a,b in zip(points,points[1:]))


def physical_groups(board):
    board.BuildConnectivity()
    conn=board.GetConnectivity();pads={uid(p):p for f in board.GetFootprints() for p in f.Pads() if p.GetNetCode()}
    seen=set();result=[]
    for key,pad in pads.items():
        if key in seen:continue
        members={uid(item) for item in conn.GetConnectedItems(pad)}&pads.keys();members.add(key)
        if any(pads[k].GetNetCode()!=pad.GetNetCode() for k in members):raise ValueError('Native connectivity mixed different nets')
        seen.update(members)
        result.append(dict(net=pad.GetNetname(),pads=sorted(members)))
    return sorted(result,key=lambda g:(g['net'],g['pads']))


def repair_pairs(groups,pads):
    """Round-robin physical component pairs before retrying their terminals."""
    buckets=[]
    for i,group in enumerate(groups):
        for other in groups[i+1:]:
            rows=sorted((math.dist(xy(pads[a].GetPosition()),xy(pads[b].GetPosition())),a,b)
                        for a in group for b in other)
            if rows:buckets.append(rows)
    buckets.sort(key=lambda rows:rows[0])
    return [rows[index] for index in range(max(map(len,buckets),default=0))
            for rows in buckets if index<len(rows)]


class Geometry:
    def __init__(self,board,outline,rules,yield_zones=(),body_guard=None,reservations=()):
        self.board=board;self.outline=outline;self.rules=rules
        self.body_guard=body_guard
        self.layers=tuple(board.GetEnabledLayers().CuStack());self.names=[board.GetLayerName(l) for l in self.layers]
        self.shapes=[[] for _ in self.layers];self.pad_shapes=[[] for _ in self.layers];self.pad_centres={};self.circular_pad_shapes=set();self.holes=[];self.pads=[]
        if board.GetCopperLayerCount() not in (2,4):raise ValueError('Ordinary routing requires an enabled 2/4-layer board')
        self.zone_clearances={};self.keepouts=[]
        for zone in board.Zones():
            if zone.GetIsRuleArea():
                self.keepouts.append((zone.Outline(),{i for i,l in enumerate(self.layers) if zone.IsOnLayer(l)},
                                      zone.GetDoNotAllowTracks(),zone.GetDoNotAllowVias()))
                continue
            if not zone.IsFilled():raise ValueError('Routing requires filled ordinary copper zones')
            if uid(zone) in yield_zones:continue  # Explicitly consented fill is regenerated after routing, not a fixed obstacle.
            for layer,items in zip(self.layers,self.shapes):
                if not zone.IsOnLayer(layer):continue
                if not zone.HasFilledPolysForLayer(layer):raise ValueError('Ordinary copper zone has no native fill for its layer')
                shape=zone.GetFilledPolysList(layer)
                if shape.OutlineCount():
                    items.append((zone.GetNetCode(),shape,shape.BBox(),False));self.zone_clearances[id(shape)]=pcb.ToMM(zone.GetLocalClearance())
        for fp in board.GetFootprints():
            # Footprint rule areas are not included in BOARD.Zones().
            # Native outlines already carry the footprint's board transform.
            for zone in fp.Zones():
                if zone.GetIsRuleArea():
                    self.keepouts.append((zone.Outline(),{i for i,l in enumerate(self.layers) if zone.IsOnLayer(l)},
                                          zone.GetDoNotAllowTracks(),zone.GetDoNotAllowVias()))
            for pad in fp.Pads():
                self.pads.append(pad)
                for layer,items,pad_items in zip(self.layers,self.shapes,self.pad_shapes):
                    if pad.IsOnLayer(layer):
                        entry=(pad.GetNetCode(),pad.GetEffectiveShape(layer),pad.GetBoundingBox(),False);items.append(entry);pad_items.append(entry);self.pad_centres[id(entry[1])]=xy(pad.GetPosition())
                        if native_circle(pad,layer):self.circular_pad_shapes.add(id(entry[1]))
                if pad.GetDrillSize().x:
                    owner=pad.GetNetCode() if pad.GetAttribute()==pcb.PAD_ATTRIB_PTH else 0
                    self.holes.append((owner,pad.GetEffectiveHoleShape(),pad.GetBoundingBox(),True))
        self.junctions={};self.linear_segments={}
        for track in board.GetTracks():
            for layer,items in zip(self.layers,self.shapes):
                if track.IsOnLayer(layer):items.append((track.GetNetCode(),track.GetEffectiveShape(layer),track.GetBoundingBox(),False))
            if isinstance(track,pcb.PCB_VIA):self.holes.append((track.GetNetCode(),track.GetEffectiveHoleShape(),track.GetBoundingBox(),True))
            elif not isinstance(track,pcb.PCB_ARC):
                a,b=xy(track.GetStart()),xy(track.GetEnd())
                if math.dist(a,b)>1e-8:
                    layer=self.layers.index(track.GetLayer());net=track.GetNetCode()
                    self.linear_segments.setdefault((layer,net),[]).append((a,b))
                    for p,q in [(a,b),(b,a)]:
                        self.junctions.setdefault((layer,net,tuple(p)),[]).append(tuple(q[i]-p[i]for i in (0,1)))
        # Non-net native copper graphics are fixed obstacles, never contacts.
        for drawing in board.GetDrawings():
            for layer,items in zip(self.layers,self.shapes):
                if drawing.IsOnLayer(layer):items.append((0,drawing.GetEffectiveShape(layer),drawing.GetBoundingBox(),False))
        for r in reservations:
            shape=pcb.SHAPE_SEGMENT(point(r['start']),point(r['end']),pcb.FromMM(r['width']))
            self.shapes[self.layers.index(r['layer'])].append((r['netCode'],shape,shape.BBox(),False))
        self.index=[self.index_shapes(items+self.holes) for items in self.shapes]
        self.pad_index=[self.index_shapes(items) for items in self.pad_shapes]
        self.maximum_zone_clearance=max(self.zone_clearances.values(),default=0)

    @staticmethod
    def bbox(box):
        a=xy(box.GetOrigin());b=xy(box.GetSize());return a[0],a[1],a[0]+b[0],a[1]+b[1]

    def index_shapes(self,items):
        index={}
        for item in items:
            x0,y0,x1,y1=self.bbox(item[2])
            for x in range(math.floor(x0/2),math.floor(x1/2)+1):
                for y in range(math.floor(y0/2),math.floor(y1/2)+1):index.setdefault((x,y),[]).append(item)
        return index

    def candidates(self,layer,a,b,padding,index=None):
        seen=set()
        index=(self.index if index is None else index)[layer]
        for x in range(math.floor((min(a[0],b[0])-padding)/2),math.floor((max(a[0],b[0])+padding)/2)+1):
            for y in range(math.floor((min(a[1],b[1])-padding)/2),math.floor((max(a[1],b[1])+padding)/2)+1):
                for item in index.get((x,y),[]):
                    key=id(item[1])
                    if key not in seen:seen.add(key);yield item

    def inside(self,p,radius):
        from board_outline import contains
        return contains(self.outline,p,radius+self.rules['copperEdge'])

    def clear(self,a,b,layer,net,width,clearance,for_via=False):
        if not self.inside(a,width/2) or not self.inside(b,width/2):return False
        if not self.junction_clear(a,b,layer,net):return False
        if self.body_guard and self.body_guard.blocker(a,b,width,self.layers[layer]):return False
        shape=pcb.SHAPE_CIRCLE(point(a),pcb.FromMM(width/2)) if a==b else pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(width))
        if any(layer in layers and (vias if for_via else tracks) and boundary.Collide(shape,0)
               for boundary,layers,tracks,vias in self.keepouts):return False
        for owner,foreign,_,hole in self.candidates(layer,a,b,width/2+max(clearance,self.rules['holeClearance'],self.maximum_zone_clearance)):
            if owner==net and owner:continue
            gap=self.rules['holeClearance'] if hole else max(clearance,self.zone_clearances.get(id(foreign),0))
            if foreign.Collide(shape,max(0,pcb.FromMM(gap)-1)):return False
        return True

    def junction_clear(self,a,b,layer,net):
        """A new leg must also respect the tangent of saved copper it contacts.

        A via does not exempt a degree-two bend on either individual layer.
        Actual pad interiors, existing branches and mid-segment T contacts
        keep their separate semantics; collinear retracing adds no bend.
        """
        if math.dist(a,b)<1e-8:return True
        for at,other in [(a,b),(b,a)]:
            old=self.junctions.get((layer,net,tuple(at)))
            if not old:continue
            directions={tuple(round(v/math.hypot(*d),6)for v in d)for d in old}
            if len(directions)!=1:continue
            if any(on_segment(at,p,q) and math.dist(at,p)>1e-7 and math.dist(at,q)>1e-7 for p,q in self.linear_segments.get((layer,net),[])):continue
            dot=pcb.SHAPE_CIRCLE(point(at),0)
            if any(owner==net and shape.Collide(dot,0) for owner,shape,_,_ in self.candidates(layer,at,at,0,self.pad_index)):continue
            u=next(iter(directions));v=[other[i]-at[i]for i in (0,1)]
            cosine=sum(u[i]*v[i]for i in (0,1))/math.hypot(*v)
            if cosine>-1/math.sqrt(2)+2e-6 and cosine<1-2e-6:return False
        return True

    def pad_exit_clear(self,a,b,layer,net,width,neck=.15):
        """Keep contacts centred; only native circles permit radial 45 degrees."""
        if math.dist(a,b)<1e-8:return True
        if abs(a[0]-b[0])<=1e-7 or abs(a[1]-b[1])<=1e-7:
            copper=pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(width))
            for owner,shape,_,_ in self.candidates(layer,a,b,width/2,self.pad_index):
                if owner!=net or not shape.Collide(copper,0):continue
                centre=self.pad_centres[id(shape)];offset=abs(a[0]-centre[0]) if abs(a[0]-b[0])<=1e-7 else abs(a[1]-centre[1])
                if offset>.000002:return False
            return True
        neck=max(.15,neck);centre=pcb.SHAPE_SEGMENT(point(a),point(b),0)
        copper=pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(width))
        for owner,shape,_,_ in self.candidates(layer,a,b,max(neck,width/2),self.pad_index):
            if owner!=net or not (shape.Collide(centre,pcb.FromMM(neck-.001)) or shape.Collide(copper,0)):continue
            position=self.pad_centres[id(shape)];dx,dy=b[0]-a[0],b[1]-a[1]
            offset=abs((position[0]-a[0])*dy-(position[1]-a[1])*dx)/math.hypot(dx,dy)
            if id(shape) in self.circular_pad_shapes and abs(abs(dx)-abs(dy))<=.000002 and offset<=.000002:continue
            return False
        return True

    def via_clear(self,at,net,diameter,drill,clearance,retained_pad_ids=()):
        if not all(self.clear(at,at,l,net,diameter,clearance,for_via=True) for l in range(len(self.layers))):return False
        circle=pcb.SHAPE_CIRCLE(point(at),pcb.FromMM(diameter/2))
        # Contact rework may preserve an independently witnessed existing pad
        # overlap. Ordinary new vias still cannot overlap any pad. Foreign
        # copper, bodies and hole clearance above/below remain unconditional.
        if any(p.GetEffectiveShape(pcb.F_Cu if p.IsOnLayer(pcb.F_Cu) else pcb.B_Cu).Collide(circle,0)
               for p in self.pads if not(p.GetNetCode()==net and uid(p) in retained_pad_ids)):return False
        hole_circle=pcb.SHAPE_CIRCLE(point(at),pcb.FromMM(drill/2))
        return not any(shape.Collide(hole_circle,pcb.FromMM(self.rules['holeClearance'])-1) for _,shape,_,_ in self.holes)


class Grid:
    def __init__(self,geometry,policy,cell,deadline,bounds=None,zone_masks=None):
        self.geometry=geometry;self.policy=policy;self.cell=cell;self.deadline=deadline
        bounds=bounds or geometry.outline
        self.net=geometry.board.GetNetcodeFromNetname(policy['net']);self.origin=bounds['origin']
        self.W=math.floor(bounds['width']/cell)+1;self.H=math.floor(bounds['height']/cell)+1
        if self.W*self.H>400_000:raise ValueError('Routing grid exceeds 400000 cells per layer')
        self.masks=[];self.edge_cache={};self.via_cache={}
        self.zone_cache_hits=0;self.zone_cache_misses=0
        width=policy['width'];clearance=policy['clearance']
        for layer,items in enumerate(geometry.shapes):
            mask=np.zeros((self.H,self.W),dtype=np.bool_)
            if geometry.names[layer] not in policy.get('layers',['F.Cu','B.Cu']):
                mask[:]=True;self.masks.append(mask);continue
            rim=math.ceil((width/2+geometry.rules['copperEdge'])/cell)
            mask[:rim,:]=True;mask[-rim:,:]=True;mask[:,:rim]=True;mask[:,-rim:]=True
            body_items=[(-1,shape,shape.BBox(),False) for native,_,shape in geometry.body_guard.shapes if native==geometry.layers[layer]] if geometry.body_guard else []
            for owner,shape,box,hole in items+geometry.holes+body_items:
                if time.monotonic()>=deadline:raise TimeoutError('grid budget')
                if owner==self.net and owner:continue
                gap=0 if owner==-1 else geometry.rules['holeClearance'] if hole else max(clearance,geometry.zone_clearances.get(id(shape),0));r=width/2+gap
                # Filled polygons dominate raster time on large poured boards.
                # Reuse only an exact native polygon and identical raster inputs,
                # within this planning call. Never cache pads, bodies or tracks.
                zone_key=None;target=mask
                if zone_masks is not None and id(shape) in geometry.zone_clearances:
                    zone_key=(shape.Format(False),width,gap,cell,tuple(self.origin),self.W,self.H)
                    if zone_key in zone_masks:
                        mask|=zone_masks[zone_key];self.zone_cache_hits+=1;continue
                    self.zone_cache_misses+=1
                    target=np.zeros_like(mask)
                x0,y0,x1,y1=geometry.bbox(box)
                lo_x=max(0,math.floor((x0-r-self.origin[0])/cell));hi_x=min(self.W-1,math.ceil((x1+r-self.origin[0])/cell))
                lo_y=max(0,math.floor((y0-r-self.origin[1])/cell));hi_y=min(self.H-1,math.ceil((y1+r-self.origin[1])/cell))
                for y in range(lo_y,hi_y+1):
                    if time.monotonic()>=deadline:raise TimeoutError('grid budget')
                    for x in range(lo_x,hi_x+1):
                        if not target[y,x] and shape.Collide(pcb.SHAPE_CIRCLE(point(self.at(y,x)),pcb.FromMM(width/2)),max(0,pcb.FromMM(gap)-1)):
                            target[y,x]=True
                            if owner==-1:geometry.body_guard.raster_blocked(shape,self.at(y,x))
                if zone_key is not None:
                    zone_masks[zone_key]=target;mask|=target
            self.masks.append(mask)

    def at(self,y,x):return [round(self.origin[0]+x*self.cell,6),round(self.origin[1]+y*self.cell,6)]

    def legal(self,a,b,layer):
        for start,end in ((a,b),(b,a)):
            rules=getattr(self,'endpoint_rules',{}).get((layer,tuple(start)))
            if rules:
                delta=[end[i]-start[i] for i in (0,1)]
                if not any(abs(delta[0]*r['axis'][1]-delta[1]*r['axis'][0])<=1e-7 and
                    sum(delta[i]*r['axis'][i]for i in (0,1))>=r['length']-1e-7 for r in rules):return False
        key=(layer,tuple(sorted((tuple(a),tuple(b)))))
        if key not in self.edge_cache:
            # Same-net copper is normally traversable, but this must not let
            # a route leave or re-enter a pad diagonally elsewhere.
            clear=self.geometry.pad_exit_clear(a,b,layer,self.net,self.policy['width'],self.policy.get('routingStyle',{}).get('padExitLengthMm',.15))
            self.edge_cache[key]=clear and self.geometry.clear(a,b,layer,self.net,self.policy['width'],self.policy['clearance'])
        return self.edge_cache[key]

    def ports(self,pad):
        # A grid describes one immutable native board. Repeated component
        # representatives may reuse the same exact pad/contact escapes.
        native=hasattr(pad,'TransformShapeToPolygon')
        key=('pad',uid(pad)) if native else ('contact',tuple(xy(pad.GetPosition())),tuple(pad.IsOnLayer(l) for l in self.geometry.layers))
        if not hasattr(self,'port_cache'):self.port_cache={}
        if key in self.port_cache:return dict(self.port_cache[key])
        result=self._ports(pad)
        if time.monotonic()<self.deadline:self.port_cache[key]=dict(result)
        return result

    def _ports(self,pad):
        at=xy(pad.GetPosition());result={};native_pad=hasattr(pad,'TransformShapeToPolygon')
        # Keep the first diagonal's finite copper cap clear of the pad too;
        # a wide trace can still touch it when its centreline neck is 0.15 mm.
        neck=max(.15,self.policy.get('routingStyle',{}).get('padExitLengthMm',.15),self.policy['width']/2+.001)
        def smooth(path):
            for a,b,c in zip(path,path[1:],path[2:]):
                u=[b[i]-a[i]for i in (0,1)];v=[c[i]-b[i]for i in (0,1)]
                if sum(u[i]*v[i]for i in (0,1))<math.hypot(*u)*math.hypot(*v)/math.sqrt(2)-1e-8:return False
            return True
        for layer,native_layer in enumerate(self.geometry.layers):
            if not pad.IsOnLayer(native_layer):continue
            exit_rules=native_exit_options(pad,neck,native_layer) if native_pad else [None]
            if native_pad:
                if not hasattr(self,'endpoint_rules'):self.endpoint_rules={}
                self.endpoint_rules[layer,tuple(at)]=exit_rules
            elif not self.contact_outside_pad_collar(at,layer):continue
            for exit_rule in exit_rules:
                lead=[round(at[i]+exit_rule['axis'][i]*exit_rule['length'],6) for i in (0,1)] if exit_rule else at
                cx=round((lead[0]-self.origin[0])/self.cell);cy=round((lead[1]-self.origin[1])/self.cell)
                choices=sorted(((y,x) for y in range(max(0,cy-5),min(self.H,cy+6)) for x in range(max(0,cx-5),min(self.W,cx+6))),key=lambda p:math.dist(at,self.at(*p)))
                found=0
                for y,x in choices:
                    if time.monotonic()>=self.deadline:return result
                    if found>=(6 if native_pad else 16):break
                    if self.masks[layer][y,x]:continue
                    b=self.at(y,x);dx,dy=b[0]-lead[0],b[1]-lead[1];s=min(abs(dx),abs(dy))
                    bends=([lead[0]+math.copysign(s,dx),lead[1]+math.copysign(s,dy)],
                           [b[0]-math.copysign(s,dx),b[1]-math.copysign(s,dy)])
                    for bend in bends:
                        path=[]
                        for p in [at,lead,[round(v,6) for v in bend],b]:
                            if not path or math.dist(path[-1],p)>1e-6:path.append(p)
                        if exit_rule and any(sum((p[i]-lead[i])*exit_rule['axis'][i]for i in (0,1))<-1e-7 for p in path[2:]):continue
                        if smooth(path) and all(self.legal(a,b,layer) for a,b in zip(path,path[1:])):
                            key=(layer,y,x)
                            if key not in result or length(path)<length(result[key]):result[key]=path
                            found+=1;break
        return result

    def contact_outside_pad_collar(self,at,layer):
        """A saved copper/plane contact cannot bypass a native pad escape."""
        radius=max(.15,self.policy.get('routingStyle',{}).get('padExitLengthMm',.15))+self.policy['width']/2
        circle=pcb.SHAPE_CIRCLE(point(at),pcb.FromMM(radius));native_layer=self.geometry.layers[layer]
        return not any(p.GetNetCode()==self.net and p.IsOnLayer(native_layer) and
            p.GetEffectiveShape(native_layer).Collide(circle,0) for p in self.geometry.pads)

    def search(self,source,target,allow_vias,max_vias,via_cost,max_expansions,on_first_contact=None):
        self.first_contact_route=None
        weight=self.policy.get('searchHeuristicWeight') or 1.
        starts=self.component_ports(source,xy(target.GetPosition()));goals=self.component_ports(target,xy(source.GetPosition()))
        if time.monotonic()>=self.deadline:return None,dict(reason='search_budget',sourcePorts=len(starts),targetPorts=len(goals),expanded=0)
        if not starts or not goals:return None,dict(reason='no_legal_pad_exit',sourcePorts=len(starts),targetPorts=len(goals),expanded=0)
        penalty=self.policy.get('routingStyle',{}).get('bendPenaltyMm',0)
        directional=bool(self.policy.get('routingStyle'))
        def allowed_turn(a,b):return not directional or a==8 or b==8 or min((a-b)%8,(b-a)%8)<=1
        def heading(a,b):
            dy,dx=b[1]-a[1],b[0]-a[0];norm=math.hypot(dx,dy)
            return next((i for i,(y,x) in enumerate(DIRECTIONS)if norm and abs(dx/norm-x/math.hypot(x,y))<1e-6 and abs(dy/norm-y/math.hypot(x,y))<1e-6),8)
        def lead_cost(path):
            directions=[heading(a,b)for a,b in zip(path,path[1:])]
            return length(path)+sum(turn_cost(a,b,penalty)for a,b in zip(directions,directions[1:]))
        goal_cost={key:lead_cost(path) for key,path in goals.items()}
        # Lower-bound the actual complete goal costs, rather than the empty
        # rectangle between contacts. Each term is an obstacle-free octile
        # metric plus the fixed endpoint lead and, if needed, one layer hop.
        # Ignoring obstacles, remaining via limits and join turns can only
        # underestimate; taking the minimum preserves an admissible bound.
        targets={layer:tuple((gy,gx,tail+(0 if layer==goal_layer else via_cost))for (goal_layer,gy,gx),tail in goal_cost.items()
            if layer==goal_layer or allow_vias and max_vias>0)for layer in range(len(self.masks))}
        heuristic_cache={};diagonal_extra=math.sqrt(2)-1
        def heuristic(layer,y,x):
            key=(layer,y,x)
            if key not in heuristic_cache:
                heuristic_cache[key]=min(((max(abs(gy-y),abs(gx-x))+diagonal_extra*min(abs(gy-y),abs(gx-x)))*self.cell+tail
                    for gy,gx,tail in targets[layer]),default=math.inf)
            return heuristic_cache[key]
        def state_for(key,vias,direction=8):return (*key,vias,direction) if directional else (*key,vias)
        queue=[];cost={};parent={};start_key={};counter=0
        for key,path in starts.items():
            direction=heading(path[-2],path[-1]) if len(path)>1 else 8
            state=state_for(key,0,direction);g=lead_cost(path);cost[state]=g;start_key[state]=key
            heapq.heappush(queue,(g+weight*heuristic(*key),counter,state));counter+=1
        expanded=0;closed=set();best=None;best_cost=float('inf');evaluated_goals=0
        def trace(state):
            chain=[state]
            while chain[-1] in parent:chain.append(parent[chain[-1]])
            chain.reverse();start=starts[start_key[chain[0]]];end=list(reversed(goals[state[:3]]))
            return start,[s[:4]for s in chain],end
        def finish(stop,complete=False):
            if best is None:return None,dict(reason=stop,expanded=expanded,heuristicKind='layer-and-goal-lead-octile',searchHeuristicWeight=weight,heuristicCells=len(heuristic_cache))
            start,chain,end=trace(best)
            return (start,chain,end),dict(reason='candidate_found',expanded=expanded,
                completePathCost=best_cost,costKind='length-plus-via-and-bend-penalty' if penalty else 'length-plus-via-penalty',bendPenaltyMm=penalty,
                sourceLeadLengthMm=length(start),targetLeadLengthMm=length(end),
                goalPortCount=len(goals),evaluatedGoalCount=evaluated_goals,
                heuristicKind='layer-and-goal-lead-octile',searchHeuristicWeight=weight,heuristicCells=len(heuristic_cache),
                goalSelectionComplete=complete and weight==1,selectionStopReason=stop)
        while queue and expanded<max_expansions:
            if time.monotonic()>=self.deadline:return finish('search_budget')
            priority,_,state=heapq.heappop(queue)
            if state in closed:continue
            # Only weight=1 is a lower bound on unsettled complete routes.
            # Explicit weighted mode stops on the same priority comparison
            # but must never advertise that result as an optimality proof.
            if best is not None and priority>=best_cost-1e-8:return finish('frontier_bound'if weight==1 else'weighted_frontier_bound',weight==1)
            closed.add(state);expanded+=1;layer,y,x,vias=state[:4];incoming=state[4] if directional else 8
            if (layer,y,x) in goals:
                evaluated_goals+=1;complete_cost=cost[state]+goal_cost[(layer,y,x)]
                tail=list(reversed(goals[(layer,y,x)]))
                if penalty and len(tail)>1:complete_cost+=turn_cost(incoming,heading(tail[0],tail[1]),penalty)
                valid_join=len(tail)<2 or allowed_turn(incoming,heading(tail[0],tail[1]))
                if valid_join and complete_cost<best_cost-1e-8:best=state;best_cost=complete_cost
                if valid_join and self.first_contact_route is None:
                    self.first_contact_route=trace(state)
                    if on_first_contact:on_first_contact(self.first_contact_route)
                # Keep expanding: another, cheaper target contact can be
                # reached through this grid cell using a different lead.
            candidates=[]
            for direction,(dy,dx) in enumerate(DIRECTIONS):
                if not allowed_turn(incoming,direction):continue
                ny,nx=y+dy,x+dx
                if not (0<=ny<self.H and 0<=nx<self.W) or not segment_clear(self.masks[layer],(y,x),(ny,nx)):continue
                if self.legal(self.at(y,x),self.at(ny,nx),layer):candidates.append((state_for((layer,ny,nx),vias,direction),self.cell*(math.sqrt(2) if dx and dy else 1)+turn_cost(incoming,direction,penalty)))
            previous=parent.get(state)
            just_changed_layer=previous is not None and previous[0]!=layer and previous[1:3]==state[1:3]
            # Through vias already span every enabled layer. An immediate
            # second hop at the same point only resets heading and emits a
            # coincident duplicate via; it is never a physical new passage.
            if allow_vias and vias<max_vias and not just_changed_layer:
                key=(y,x)
                if key not in self.via_cache:self.via_cache[key]=self.geometry.via_clear(self.at(y,x),self.net,self.policy['viaDiameter'],self.policy['viaDrill'],self.policy['clearance'])
                if self.via_cache[key]:
                    for next_layer in range(len(self.masks)):
                        if next_layer!=layer and not self.masks[next_layer][y,x]:candidates.append((state_for((next_layer,y,x),vias+1),via_cost))
            for neighbor,delta in candidates:
                g=cost[state]+delta
                if g+1e-8>=cost.get(neighbor,float('inf')):continue
                cost[neighbor]=g;parent[neighbor]=state
                heapq.heappush(queue,(g+weight*heuristic(*neighbor[:3]),counter,neighbor));counter+=1
        return finish('expansion_limit' if queue else 'bounded_grid_exhausted',not queue)

    def component_ports(self,pad,source):
        """Connect to already connected copper, without retracing it to a pad."""
        class Contact:
            def __init__(self,at,item):self.at=at;self.item=item
            def GetPosition(self):return point(self.at)
            def IsOnLayer(self,layer):return self.item.IsOnLayer(layer)
        choices=[]
        connected={uid(item) for item in self.geometry.board.GetConnectivity().GetConnectedItems(pad)}
        allowed_layers=tuple(layer for layer,name in zip(self.geometry.layers,self.geometry.names)
            if name in self.policy.get('layers',['F.Cu','B.Cu']))
        # SWIG connectivity returns a via as PCB_TRACK, losing its layer-pair
        # behavior. Resolve UUIDs against the board's concrete track inventory.
        for item in self.geometry.board.GetTracks():
            if uid(item) not in connected:continue
            if not isinstance(item,pcb.PCB_TRACK) or isinstance(item,pcb.PCB_ARC) or item.GetNetCode()!=self.net:continue
            # Ineligible front-side segment endpoints must not consume the
            # bounded contact budget before an existing through via is seen.
            if not any(item.IsOnLayer(layer)for layer in allowed_layers):continue
            if isinstance(item,pcb.PCB_VIA):positions=[xy(item.GetPosition())]
            else:
                a,b=xy(item.GetStart()),xy(item.GetEnd());dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
                t=max(0,min(1,((source[0]-a[0])*dx+(source[1]-a[1])*dy)/den)) if den else 0
                positions=[a,b,[round(a[0]+t*dx,6),round(a[1]+t*dy,6)]]
            for at in positions:choices.append((math.dist(source,at),at,item))
        goals=self.ports(pad);seen=set()
        # A congested representative pad need not be the only escape from an
        # already connected component. Native pads retain their centre/neck
        # rules; they are not treated as unrestricted copper contacts.
        peers=sorted((p for p in self.geometry.pads if uid(p) in connected and
            uid(p)!=uid(pad) and p.GetNetCode()==self.net),key=lambda p:(math.dist(source,xy(p.GetPosition())),uid(p)))
        for peer in peers[:4]:
            if time.monotonic()>=self.deadline:break
            for port,path in self.ports(peer).items():
                if port not in goals or length(path)<length(goals[port]):goals[port]=path
        for _,at,item in sorted(choices,key=lambda r:r[0]):
            key=(tuple(at),tuple(layer for layer in allowed_layers if item.IsOnLayer(layer)))
            if key in seen:continue
            seen.add(key)
            if len(seen)>12:break
            for port,path in self.ports(Contact(at,item)).items():
                if port not in goals or length(path)<length(goals[port]):goals[port]=path
        # Contact a saved, independently proven plane island near the other
        # terminal. A shared zone UUID alone is never a connectivity witness.
        from copper_zones import connected_fill
        cx=round((source[0]-self.origin[0])/self.cell);cy=round((source[1]-self.origin[1])/self.cell)
        radius=math.ceil(1.6/self.cell);step=max(1,round(.2/self.cell))
        cells={(y,x) for y in range(max(0,cy-radius),min(self.H,cy+radius+1),step) for x in range(max(0,cx-radius),min(self.W,cx+radius+1),step)}
        # Large power pads can have their entire old 1.6 mm neighbourhood
        # inside the forbidden pad collar. Probe beyond their actual native
        # exits too. These are goals only: path/via clearance and connectivity
        # still require the ordinary search and post-fill component merge.
        neck=max(.15,self.policy.get('routingStyle',{}).get('padExitLengthMm',.15),self.policy['width']/2+.001)
        for terminal in self.geometry.pads:
            if terminal.GetNetCode()!=self.net or xy(terminal.GetPosition())!=source:continue
            for native_layer in allowed_layers:
                if not terminal.IsOnLayer(native_layer):continue
                for rule in native_exit_options(terminal,neck,native_layer):
                    for extra in (.2,.6,1.2):
                        at=[source[i]+rule['axis'][i]*(rule['length']+extra) for i in (0,1)]
                        x=round((at[0]-self.origin[0])/self.cell);y=round((at[1]-self.origin[1])/self.cell)
                        for dy,dx in ((0,0),(-1,0),(1,0),(0,-1),(0,1)):
                            if 0<=y+dy<self.H and 0<=x+dx<self.W:cells.add((y+dy,x+dx))
        cells=sorted(cells,key=lambda p:(math.dist(source,self.at(*p)),p))
        for layer,native_layer in enumerate(self.geometry.layers):
            if not any(z.IsOnLayer(native_layer) and z.GetNetCode()==self.net for z in self.geometry.board.Zones()):continue
            # Cache only within this immutable grid. plan rebuilds the grid
            # after every accepted connection and native refill.
            if not hasattr(self,'fill_contact_cache'):self.fill_contact_cache={}
            cache_key=(tuple(sorted(connected|{uid(pad)})),native_layer)
            if cache_key not in self.fill_contact_cache:
                self.fill_contact_cache[cache_key]=connected_fill(self.geometry.board,[pad],native_layer,self.policy['net'])[0]
            region=self.fill_contact_cache[cache_key]
            if not region.OutlineCount():continue
            for y,x in cells:
                if time.monotonic()>=self.deadline:return goals
                if self.masks[layer][y,x]:continue
                at=self.at(y,x)
                if region.Contains(point(at)) and self.contact_outside_pad_collar(at,layer) and self.legal(at,at,layer):goals.setdefault((layer,y,x),[at])
        return goals


def compact(points):
    clean=[]
    for p in points:
        if clean and math.dist(clean[-1],p)<1e-6:continue
        if len(clean)>1:
            a,b=clean[-2:];cross=(b[0]-a[0])*(p[1]-b[1])-(b[1]-a[1])*(p[0]-b[0])
            dot=(b[0]-a[0])*(p[0]-b[0])+(b[1]-a[1])*(p[1]-b[1])
            if abs(cross)<1e-7 and dot>=0:clean.pop()
        clean.append(p)
    return clean


def operations_for(grid,route,quality=None):
    start,chain,end=route;policy=grid.policy;layer=chain[0][0];points=list(start);ops=[]
    def flush():
        path,measurements=prepare_path(points,lambda a,b:grid.legal(a,b,layer),grid.deadline,grid.policy.get('routingStyle'))
        if quality is not None:quality.append(dict(net=policy['net'],layer=grid.geometry.names[layer],**measurements))
        if len(path)>1:ops.append(dict(kind='add_track',net=policy['net'],width=policy['width'],layer=grid.geometry.names[layer],points=path))
    for current in chain[1:]:
        next_layer,y,x,_=current;at=grid.at(y,x)
        if next_layer!=layer:
            flush();ops.append(dict(kind='add_via',net=policy['net'],at=at,diameter=policy['viaDiameter'],drill=policy['viaDrill']));layer=next_layer;points=[at]
        else:points.append(at)
    points.extend(end);flush();return ops


def apply_memory(board,operations,refill_board=True):
    items=[]
    for op in operations:
        net=board.GetNetcodeFromNetname(op['net'])
        if op['kind']=='move_via':
            item=next((t for t in board.GetTracks() if uid(t)==op['objectId']),None)
            if not isinstance(item,pcb.PCB_VIA) or item.GetNetCode()!=net:raise ValueError('Local contact via identity changed')
            item.SetPosition(point(op['at']))
        elif op['kind']=='add_track':
            for a,b in zip(op['points'],op['points'][1:]):
                item=pcb.PCB_TRACK(board);item.SetStart(point(a));item.SetEnd(point(b));item.SetLayer(board.GetLayerID(op['layer']));item.SetWidth(pcb.FromMM(op['width']));item.SetNetCode(net);board.Add(item);items.append(item)
        elif op['kind']=='add_via':
            item=pcb.PCB_VIA(board);item.SetViaType(pcb.VIATYPE_THROUGH);item.SetLayerPair(*LAYERS);item.SetPosition(point(op['at']));item.SetWidth(pcb.FromMM(op['diameter']));item.SetDrill(pcb.FromMM(op['drill']));item.SetNetCode(net);board.Add(item);items.append(item)
        else:raise ValueError('Unsupported in-memory copper operation')
    if refill_board:
        from copper_zones import refill
        refill(board)
    return items


def direct_connection(geometry,policy,source,target,deadline):
    """Try a centred cardinal terminal link before requiring two grid collars.

    Adjacent pads can legally connect without any planar bend even when their
    exterior neck collars overlap. This is not a waiver for an offset contact,
    an obstacle or a connection between different native copper layers.
    """
    a,b=xy(source.GetPosition()),xy(target.GetPosition());net=source.GetNetCode()
    if not net or net!=target.GetNetCode() or math.dist(a,b)<1e-6:return None
    if round((a[0]-b[0])*1e6) and round((a[1]-b[1])*1e6):return None
    for layer,name in enumerate(geometry.names):
        if name not in policy.get('layers',['F.Cu','B.Cu']):continue
        native=geometry.layers[layer]
        if not source.IsOnLayer(native) or not target.IsOnLayer(native):continue
        def legal(a,b):
            return geometry.clear(a,b,layer,net,policy['width'],policy['clearance']) and geometry.pad_exit_clear(a,b,layer,net,policy['width'])
        if not legal(a,b):continue
        path,quality=prepare_path([a,b],legal,deadline,policy.get('routingStyle'))
        return [dict(kind='add_track',net=policy['net'],width=policy['width'],layer=name,points=path)],[dict(net=policy['net'],layer=name,**quality)]
    return None


def prepared_cost(operations,via_cost,style):
    """Price emitted copper using the same length/via/turn units as search."""
    total=0.;penalty=(style or {}).get('bendPenaltyMm',0)
    for op in operations:
        if op['kind']=='add_via':total+=via_cost;continue
        points=op['points'];total+=length(points);headings=[]
        for a,b in zip(points,points[1:]):
            dx,dy=b[0]-a[0],b[1]-a[1]
            if abs(dx)+abs(dy)<1e-9:continue
            headings.append(DIRECTIONS.index((0 if abs(dy)<1e-9 else (1 if dy>0 else -1),
                                               0 if abs(dx)<1e-9 else (1 if dx>0 else -1))))
        total+=sum(turn_cost(a,b,penalty) for a,b in zip(headings,headings[1:]))
    return total


def plan(board,params,progress):
    started=time.monotonic();deadline=started+params['searchSeconds']
    before=physical_groups(board)
    pre_copper=params.get('preCopperOperations',[]);copper_rework=params.get('localCopperRework');contact_rework=params.get('localContactRework');pre_quality=[]
    if pre_copper:
        if bool(copper_rework)==bool(contact_rework):raise ValueError('Pre-route copper requires one explicit local source contract')
        if contact_rework:
            from local_contact_rework import native_source_check,screen_operations,proposal_quality
        else:
            from local_copper_rework import native_source_check,screen_operations,proposal_quality
        native_source_check(board,copper_rework or contact_rework)
        pre_quality=proposal_quality(pre_copper)
    pair_limit=params.get('maxPairAttemptsPerConnection') or 12
    policy=params.get('bodyAvoidance')
    if policy not in (None,BODY_POLICY):raise ValueError('Unsupported body avoidance policy')
    source_body=BodyGapGuard(board);existing_body_conflicts=source_body.audit(board)
    # Retain removed wrappers until all native observations finish. The source
    # file is never saved by this planner, including failed searches.
    retired=[];tracks={uid(t):t for t in board.GetTracks()};seen=set();selected={p['net'] for p in params['nets']}
    for item in params.get('removeCopper',[]):
        id=item['objectId'];t=tracks.get(id)
        if id in seen or t is None or isinstance(t,pcb.PCB_ARC) or t.GetNetname()!=item['net'] or item['net'] not in selected:
            raise ValueError('Ordinary replacement identity/type/net is invalid: '+str(dict(objectId=id,duplicate=id in seen,
                exists=t is not None,arc=isinstance(t,pcb.PCB_ARC) if t is not None else None,
                expectedNet=item['net'],actualNet=t.GetNetname() if t is not None else None,selected=item['net'] in selected)))
        if item['type']!=('via' if isinstance(t,pcb.PCB_VIA) else 'segment'):raise ValueError('Ordinary replacement type differs')
        seen.add(id);retired.append(t);board.Remove(t)
    # Remove the explicitly witnessed source copper before moving footprints.
    # KiCad may otherwise reassign a touching segment to the moved pad's new
    # net, which makes source identity validation order-dependent.
    if params.get('preMoves'):
        footprints={uid(f):f for f in board.GetFootprints()}
        for move in params['preMoves']:
            fp=footprints.get(move['objectId'])
            if fp is None:raise ValueError('Local placement footprint identity changed')
            fp.SetPosition(point(move['at']));fp.SetOrientationDegrees(move['rotation'])
    if pre_copper:
        reason=screen_operations(board,params,copper_rework or contact_rework,pre_copper)
        if reason:raise ValueError('Local copper detour failed native screen: '+reason)
        apply_memory(board,pre_copper,refill_board=False)
    if retired or params.get('preMoves') or pre_copper:
        from copper_zones import refill
        refill(board)
    body=BodyGapGuard(board)
    reservations=[]
    if params.get('preMoves'):
        from connector_access import review as connector_review
        moved={m['objectId']for m in params['preMoves']}
        policies={p['net']:p for p in params['nets']}
        counts={}
        for f in board.GetFootprints():
            for p in f.Pads():counts[p.GetNetname()]=counts.get(p.GetNetname(),0)+1
        footprints={uid(f):f for f in board.GetFootprints()}
        datums={r['footprintId']:r for r in connector_review(board)['interfaces']if r['status']=='passed'}
        held_pads={id for group in physical_groups(board) if len(group['pads'])>1 for id in group['pads']}
        geometries={}
        for fp in board.GetFootprints():
            row=datums.get(uid(fp));inward=[-v for v in row['matingOutward']] if row else None
            for pad in fp.Pads():
                policy=policies.get(pad.GetNetname())
                if not policy or counts.get(pad.GetNetname(),0)<2:continue
                if uid(fp)not in moved and uid(pad)in held_pads:continue
                if pad.GetNetname()not in geometries:
                    geometries[pad.GetNetname()]=Geometry(board,params['outline'],params['rules'],[z['objectId']for z in policy.get('yieldZones',[])],body)
                geom=geometries[pad.GetNetname()]
                targets=[xy(p.GetPosition())for f in board.GetFootprints()for p in f.Pads()if p.GetNetCode()==pad.GetNetCode()and uid(f)!=uid(fp)]
                for layer in geom.layers:
                    if not pad.IsOnLayer(layer):continue
                    options=native_exit_options(pad,max(.15,(params.get('routingStyle')or{}).get('padExitLengthMm',.15)),layer);legal=[]
                    for option in options:
                        axis=option['axis']
                        if inward and sum(axis[i]*inward[i]for i in (0,1))<.999:continue
                        a=xy(pad.GetPosition())
                        for extra in [2*(params['rules']['viaDiameter']+policy['clearance']),.7,0]:
                            b=[round(a[i]+axis[i]*(option['length']+extra),6)for i in (0,1)]
                            if geom.clear(a,b,geom.layers.index(layer),pad.GetNetCode(),policy['width'],policy['clearance']):
                                legal.append((min((math.dist(b,t)for t in targets),default=0),b));break
                    if legal:
                        _,b=min(legal)
                        reservations.append(dict(reference=fp.GetReference(),pin=pad.GetNumber(),netCode=pad.GetNetCode(),start=xy(pad.GetPosition()),end=b,width=policy['width'],layer=layer))
    operations=list(pre_copper);connections=[];attempts=[];completed=0;quality=list(pre_quality)
    zone_masks={};net_budgets=[]
    pads={uid(p):p for f in board.GetFootprints() for p in f.Pads()}
    def components(net):return [g['pads'] for g in physical_groups(board) if g['net']==net]
    queue=list(params['nets']);retried=set()
    for index,policy in enumerate(queue):
        # Reserve an equal share of remaining search time for each later net.
        # Unused time flows forward; a single-net request keeps its old budget.
        # Native fill/proof remains authoritative even if it overruns a share.
        net_started=time.monotonic()
        group_counts={}
        for group in physical_groups(board):group_counts[group['net']]=group_counts.get(group['net'],0)+1
        pending=sum(group_counts.get(p['net'],0)>1 for p in queue[index:])
        net_deadline=min(deadline,net_started+max(0.,deadline-net_started)/max(1,pending))
        progress(dict(index=index+1,total=len(queue),net=policy['net'],connections=len(connections),phase='search'))
        if not board.GetNetcodeFromNetname(policy['net']):raise ValueError('Unknown routing net: '+policy['net'])
        policy={**policy,'viaDiameter':params['rules']['viaDiameter'],'viaDrill':params['rules']['viaDrill'],'routingStyle':params.get('routingStyle') or {},'searchHeuristicWeight':params.get('searchHeuristicWeight') or 1.}
        groups=components(policy['net']);net_before=len(groups)
        while len(groups)>1 and len(connections)<params['maxConnections'] and time.monotonic()<net_deadline:
            pairs=repair_pairs(groups,pads)
            if policy.get('restoreSourceConnectionsOnly'):
                originals=[set(g['pads'])for g in before if g['net']==policy['net']]
                pairs=[pair for pair in pairs if any(pair[1]in g and pair[2]in g for g in originals)]
                if not pairs:break
            geometry=Geometry(board,params['outline'],params['rules'],[z['objectId'] for z in policy.get('yieldZones',[])],body if params.get('bodyAvoidance') else None,reservations)
            grid=None;grid_seconds=0.
            found=False
            for _,a,b in pairs[:pair_limit]:
                if time.monotonic()>=net_deadline:break
                direct=direct_connection(geometry,policy,pads[a],pads[b],net_deadline)
                # Direct links solve overlapping pad collars, but must not
                # pre-empt cheaper contacts on independently witnessed fill.
                compare_fill=direct is not None and any(z.GetNetCode()==pads[a].GetNetCode() and
                    any(z.IsOnLayer(layer) and name in policy.get('layers',['F.Cu','B.Cu'])
                        for layer,name in zip(geometry.layers,geometry.names)) for z in board.Zones())
                search_stats=None
                if direct is not None and not compare_fill:
                    ops,path_quality=direct
                    stats=dict(reason='candidate_found',proposalSelection='direct-cardinal-terminal',expanded=0,
                               completePathCost=length(ops[0]['points']),costKind='length-plus-via-and-bend-penalty',gridBuildSkipped=True)
                else:
                    # Exact native checks already cover a direct terminal
                    # link. Build the raster only for an actual grid search;
                    # same-net fill still gets its existing cost comparison.
                    if grid is None:
                        grid_started=time.monotonic()
                        try:grid=Grid(geometry,policy,params['grid'],net_deadline,params.get('searchBounds'),zone_masks=zone_masks)
                        except TimeoutError:attempts.append(dict(net=policy['net'],reason='grid_budget'));break
                        grid_seconds=round(time.monotonic()-grid_started,6)
                    first_proposal={}
                    def capture_first(route):
                        measurements=[];ops=operations_for(grid,route,measurements)
                        first_proposal.update(operations=ops,quality=measurements)
                    route,stats=grid.search(pads[a],pads[b],policy['allowVias'],policy['maxViasPerConnection'],params['viaCost'],params['maxExpansions'],capture_first)
                    stats.update(gridBuildSeconds=grid_seconds,gridCellsPerLayer=grid.W*grid.H,
                                 zoneRasterCacheHits=grid.zone_cache_hits,zoneRasterCacheMisses=grid.zone_cache_misses)
                    if route is None and direct is None:
                        attempts.append(dict(net=policy['net'],fromPad=a,toPad=b,**stats));continue
                    search_stats=dict(stats)
                    if route is None:
                        ops,path_quality=direct
                    elif grid.first_contact_route!=route:
                        path_quality=[];ops=operations_for(grid,route,path_quality)
                        first_quality=first_proposal['quality'];first_ops=first_proposal['operations']
                        comparison=compare_proposals(ops,path_quality,first_ops,first_quality,policy.get('routingStyle'))
                        stats['proposalComparison']=comparison
                        if not comparison['useCompleteCostCandidate']:
                            ops=first_ops;path_quality=first_quality
                        stats['proposalSelection']='complete-cost' if comparison['useCompleteCostCandidate'] else 'first-contact-quality-fallback'
                    else:
                        ops=first_proposal['operations'];path_quality=first_proposal['quality'];stats['proposalSelection']='same-contact'
                    if compare_fill:
                        direct_ops,direct_quality=direct
                        direct_cost=prepared_cost(direct_ops,params['viaCost'],policy.get('routingStyle'))
                        searched_cost=prepared_cost(ops,params['viaCost'],policy.get('routingStyle')) if route is not None else None
                        use_search=searched_cost is not None and searched_cost<direct_cost-1e-8
                        comparison=dict(directCost=direct_cost,searchedPreparedCost=searched_cost,useSearch=use_search,
                            scope='Bounded legal candidates; explicit via and bend costs, not global or SI/PI optimality.')
                        if use_search:
                            stats.update(proposalSelection='grid-contact-over-direct',completePathCost=searched_cost,
                                         directComparison=comparison)
                        else:
                            ops,path_quality=direct_ops,direct_quality
                            stats=dict(reason='candidate_found',proposalSelection='direct-cardinal-terminal',
                                expanded=search_stats.get('expanded',0),completePathCost=direct_cost,
                                costKind='length-plus-via-and-bend-penalty',directComparison=comparison,contactSearch=search_stats)
                attempts.append(dict(net=policy['net'],fromPad=a,toPad=b,**stats))
                items=apply_memory(board,ops);after=components(policy['net'])
                if len(after)>=len(groups):
                    for item in items:board.Delete(item)
                    from copper_zones import refill
                    refill(board)
                    attempts.append(dict(net=policy['net'],reason='native_memory_connection_not_merged'));continue
                operations.extend(ops);quality.extend(path_quality);connections.append(dict(net=policy['net'],fromPad=a,toPad=b));groups=after;found=True
                progress(dict(index=index+1,total=len(queue),net=policy['net'],connections=len(connections),phase='connected'))
                break
            if not found:break
        if len(groups)<=1:completed+=1
        net_budgets.append(dict(net=policy['net'],allocatedSeconds=round(max(0.,net_deadline-net_started),6),
            elapsedSeconds=round(time.monotonic()-net_started,6),shareExhausted=time.monotonic()>=net_deadline,
            componentsBefore=net_before,componentsAfter=len(groups),retry=index>=len(params['nets'])))
        if len(groups)>1 and time.monotonic()>=net_deadline and deadline-time.monotonic()>1 and policy['net']not in retried:
            # Reuse time left by easier nets; never extend the original deadline.
            queue.append(policy);retried.add(policy['net'])
        progress(dict(index=index+1,total=len(queue),net=policy['net'],connections=len(connections),phase='net_finished',componentsBefore=net_before,componentsAfter=len(groups)))
    after=physical_groups(board)
    feedback=body.report();feedback.update(enforced=bool(params.get('bodyAvoidance')),existingConflicts=existing_body_conflicts,
        existingConflictCount=len(existing_body_conflicts))
    from connector_access import review as connector_review
    feedback['connectorAccess']=connector_review(board)
    feedback['connectorEscapeReservations']=reservations
    refs={uid(p):f.GetReference() for f in board.GetFootprints() for p in f.Pads()}
    feedback['unresolvedConnections']=[dict(net=a['net'],fromPad=a['fromPad'],toPad=a['toPad'],fromReference=refs[a['fromPad']],toReference=refs[a['toPad']],
        reason=a['reason'],**{k:a[k] for k in ('sourcePorts','targetPorts','expanded') if k in a}) for a in attempts if 'fromPad' in a and a['reason']!='candidate_found']
    feedback['unfinishedNets']=[dict(net=p['net'],physicalComponentCount=sum(g['net']==p['net'] for g in after)) for p in params['nets'] if sum(g['net']==p['net'] for g in after)>1]
    comparison=None
    if params.get('qualityScopeIds'):
        from local_rework import comparison_quality
        rows=[]
        for t in board.GetTracks():
            if isinstance(t,pcb.PCB_VIA):rows.append(dict(id=uid(t),type='via',net=t.GetNetname(),at=xy(t.GetPosition())))
            elif not isinstance(t,pcb.PCB_ARC):rows.append(dict(id=uid(t),type='segment',net=t.GetNetname(),layer=board.GetLayerName(t.GetLayer()),
                width=pcb.ToMM(t.GetWidth()),start=xy(t.GetStart()),end=xy(t.GetEnd())))
        fps=[dict(pads=[dict(at=xy(p.GetPosition())) for p in f.Pads()]) for f in board.GetFootprints()]
        comparison=comparison_quality(fps,rows,tracks,params['qualityScopeIds'])
    return dict(strategy='formforge-native-geometry-weighted-a-star-v1'if (params.get('searchHeuristicWeight') or 1.)>1 else'formforge-native-geometry-octilinear-a-star-v1',searchHeuristicWeight=params.get('searchHeuristicWeight') or 1.,routingStyle=params.get('routingStyle'),operations=operations,connections=connections,readability=summarize(quality),comparisonReadability=comparison,layoutFeedback=feedback,
        physicalGroupsBefore=before,predictedPhysicalGroupsAfter=after,attempts=attempts,completedNetCount=completed,
        plannedConnectionCount=len(connections),maxPairAttemptsPerConnection=pair_limit,searchBounds=params.get('searchBounds'),elapsedSeconds=round(time.monotonic()-started,3),
        budgetExhausted=time.monotonic()>=deadline,netBudgets=net_budgets,
        budgetPolicy='remaining-time-per-net-v1',manufacturingApproved=False,
        scope='Ordinary selected-net continuation proposal; native checks and post-save physical connections are required.')
