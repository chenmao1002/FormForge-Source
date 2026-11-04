"""Bounded local placement and ordinary-routing comparison.

The external Agent selects one series link or an explicit shared-node pair and a small search area. This
module compares the fixed placement with a few native footprint poses and an
optional explicit companion nudge, then runs
the existing self-developed router for each viable pose.  Candidate screening is
deliberately light: native pad clearance, the existing limited body model and
selected-net connectivity.  The winning concrete operations still go through
the normal one-time PCB save, DRC and adoption pipeline.
"""
import math
import time

import pcbnew as pcb

from body_gap import BodyGapGuard
from board_outline import contains
from route_engine import physical_groups, uid, xy, apply_memory
from local_rework import for_pose,moved_ids
from local_copper_rework import detours,native_source_check,screen_operations
from local_contact_rework import variants as contact_variants,native_source_check as contact_source_check,screen_operations as screen_contact


POLICY='bounded-series-pair-placement-routing-v1'


def point(values):return pcb.VECTOR2I(*(round(v*1_000_000) for v in values))


def _rotation_options(angle,quarter_turns):
    values=[angle,(angle+180)%360]
    if quarter_turns:values.extend(((angle+90)%360,(angle+270)%360))
    return list(dict.fromkeys(round(v)%360 for v in values))


def _grid(area):
    x0,y0=area['origin'];step=area['grid'];x1=x0+area['width'];y1=y0+area['height']
    xs=[];ys=[];x=x0
    while x<=x1+1e-9:xs.append(round(x,6));x+=step
    y=y0
    while y<=y1+1e-9:ys.append(round(y,6));y+=step
    centre=[x0+area['width']/2,y0+area['height']/2]
    return sorted(([x,y]for x in xs for y in ys),key=lambda p:(math.dist(p,centre),p[1],p[0]))


def _footprints(board,references):
    by_ref={f.GetReference():f for f in board.GetFootprints()}
    if any(r not in by_ref for r in references):raise ValueError('Local placement reference is absent')
    return [by_ref[r] for r in references]


def _link_pad(fp,net):
    pads=[p for p in fp.Pads() if p.GetNetname()==net]
    if len(pads)!=1:raise ValueError('Local placement link must use exactly one pad on each footprint')
    return pads[0]


def _pad_half_extent(pad,direction):
    box=pad.GetBoundingBox()
    return pcb.ToMM(box.GetWidth() if direction[0] else box.GetHeight())/2


def _inside_area(at,area):
    x,y=at;x0,y0=area['origin']
    return x0-1e-6<=x<=x0+area['width']+1e-6 and y0-1e-6<=y<=y0+area['height']+1e-6


def _pose(board,spec,settings):
    if 'placements' in spec:
        poses={p['reference']:p for p in spec['placements']};moves=[]
        for fp in _footprints(board,list(poses)):
            pose=poses[fp.GetReference()];old=xy(fp.GetPosition());rotation=round(fp.GetOrientationDegrees())%360
            if fp.IsLocked() and (old!=pose['at'] or rotation!=pose['rotation']):return None,'locked_footprint'
            if not _inside_area(pose['at'],settings['area']):return None,'footprint_origin_outside_search_area'
            fp.SetPosition(point(pose['at']));fp.SetOrientationDegrees(pose['rotation'])
            moves.append(dict(kind='move_footprint',objectId=uid(fp),expectedHash=settings['footprintHashes'][uid(fp)],at=pose['at'],rotation=pose['rotation']))
        return moves,None
    refs=settings['references'];fps=_footprints(board,refs);net=settings['linkNet'];area=settings['area']
    for fp,rotation in zip(fps,spec['rotations']):fp.SetOrientationDegrees(rotation)
    pads=[_link_pad(fp,net) for fp in fps]
    if 'individualPlacements' in spec:
        for fp,at in zip(fps,spec['individualPlacements']):fp.SetPosition(point(at))
    elif 'alignmentAxis' in spec:
        fixed=spec['anchor'];moving=1-fixed;axis=1-spec['alignmentAxis']
        delta=xy(pads[fixed].GetPosition())[axis]-xy(pads[moving].GetPosition())[axis]
        at=xy(fps[moving].GetPosition());at[axis]+=delta;fps[moving].SetPosition(point(at))
    elif spec['midpoint'] is None:
        direction=spec['direction'];distance=sum(_pad_half_extent(p,direction) for p in pads)+settings['connectedPadGapMm']
        fixed=spec['anchor'];moving=1-fixed
        fixed_at=xy(pads[fixed].GetPosition())
        sign=1 if moving==1 else -1
        desired=[fixed_at[i]+sign*direction[i]*distance for i in (0,1)]
        delta=[desired[i]-xy(pads[moving].GetPosition())[i] for i in (0,1)]
        fps[moving].SetPosition(point([xy(fps[moving].GetPosition())[i]+delta[i] for i in (0,1)]))
    else:
        direction=spec['direction'];distance=sum(_pad_half_extent(p,direction) for p in pads)+settings['connectedPadGapMm']
        midpoint=spec['midpoint'];desired=[[midpoint[i]-direction[i]*distance/2 for i in (0,1)],
                                           [midpoint[i]+direction[i]*distance/2 for i in (0,1)]]
        for fp,pad,target in zip(fps,pads,desired):
            delta=[target[i]-xy(pad.GetPosition())[i] for i in (0,1)]
            fp.SetPosition(point([xy(fp.GetPosition())[i]+delta[i] for i in (0,1)]))
    if spec.get('poseTranslation'):
        for fp in fps:
            fp.SetPosition(point([xy(fp.GetPosition())[i]+spec['poseTranslation'][i] for i in (0,1)]))
    moves=[]
    for fp in fps:
        at=xy(fp.GetPosition())
        if not _inside_area(at,area):return None,'footprint_origin_outside_search_area'
        moves.append(dict(kind='move_footprint',objectId=uid(fp),expectedHash=settings['footprintHashes'][uid(fp)],
                          at=at,rotation=round(fp.GetOrientationDegrees())%360))
    return moves,None


def placement_blockers(board,moved_ids,clearance):
    """Return native collision partners for coordinated layout planning."""
    rows=[];fps=list(board.GetFootprints());layers=list(board.GetEnabledLayers().CuStack())
    for fp in fps:
        if uid(fp)not in moved_ids:continue
        for other in fps:
            if uid(other)==uid(fp):continue
            for pad in fp.Pads():
                for q in other.Pads():
                    gap=clearance if pad.GetNetCode()!=q.GetNetCode()else 0
                    if any(pad.IsOnLayer(l)and q.IsOnLayer(l)and pad.GetEffectiveShape(l).Collide(q.GetEffectiveShape(l),pcb.FromMM(gap))for l in layers):
                        rows.append(dict(reference=fp.GetReference(),pin=pad.GetNumber(),otherReference=other.GetReference(),otherPin=q.GetNumber(),kind='native_pad_clearance'))
            layer=pcb.F_CrtYd if fp.GetLayer()==pcb.F_Cu else pcb.B_CrtYd
            a=fp.GetCourtyard(layer);b=other.GetCourtyard(layer)
            if fp.GetLayer()==other.GetLayer()and a.OutlineCount()and b.OutlineCount()and a.Collide(b,0):
                rows.append(dict(reference=fp.GetReference(),otherReference=other.GetReference(),kind='native_courtyard_overlap'))
    return rows


def _quick_geometry(board,moved_ids,outline,rules,removed_ids=()):
    from connector_access import review
    if any(r['footprintId']in moved_ids and r['status']!='passed'for r in review(board)['interfaces']):return 'connector_mating_datum_not_at_outward_edge'
    enabled=list(board.GetEnabledLayers().CuStack());fps=list(board.GetFootprints());removed_ids=set(removed_ids)
    retained=[item for item in board.GetTracks() if uid(item) not in removed_ids]
    vias=[item for item in retained if isinstance(item,pcb.PCB_VIA)]
    for fp in fps:
        if uid(fp) not in moved_ids:continue
        for pad in fp.Pads():
            box=pad.GetBoundingBox();corners=[[pcb.ToMM(box.GetLeft()),pcb.ToMM(box.GetTop())],
                [pcb.ToMM(box.GetRight()),pcb.ToMM(box.GetTop())],[pcb.ToMM(box.GetRight()),pcb.ToMM(box.GetBottom())],
                [pcb.ToMM(box.GetLeft()),pcb.ToMM(box.GetBottom())]]
            if not all(contains(outline,p,rules['copperEdge']) for p in corners):return 'moved_pad_outside_board'
            for other in fps:
                if other is fp:continue
                for candidate in other.Pads():
                    for layer in enabled:
                        if not pad.IsOnLayer(layer) or not candidate.IsOnLayer(layer):continue
                        gap=rules['clearance'] if pad.GetNetCode()!=candidate.GetNetCode() else 0
                        if pad.GetEffectiveShape(layer).Collide(candidate.GetEffectiveShape(layer),pcb.FromMM(gap)):
                            return 'native_pad_clearance'
                        if pad.GetNetCode()!=candidate.GetNetCode() and candidate.GetDrillSize().x and pad.GetEffectiveShape(layer).Collide(
                                candidate.GetEffectiveHoleShape(),pcb.FromMM(rules.get('holeClearance',rules['clearance']))):
                            return 'native_pad_hole_clearance'
            for via in vias:
                if via.GetNetCode()==pad.GetNetCode():continue
                if any(pad.IsOnLayer(layer) and pad.GetEffectiveShape(layer).Collide(via.GetEffectiveHoleShape(),
                    pcb.FromMM(rules.get('holeClearance',rules['clearance']))) for layer in enabled):return 'native_pad_hole_clearance'
            for item in retained:
                if item.GetNetCode()==pad.GetNetCode():continue
                layers=enabled if isinstance(item,pcb.PCB_VIA) else (item.GetLayer(),)
                gap=pcb.FromMM(rules['clearance']);other=item.GetBoundingBox()
                if (box.GetRight()+gap<other.GetLeft() or other.GetRight()+gap<box.GetLeft()
                    or box.GetBottom()+gap<other.GetTop() or other.GetBottom()+gap<box.GetTop()):continue
                if any(pad.IsOnLayer(layer) and item.IsOnLayer(layer) and
                       pad.GetEffectiveShape(layer).Collide(item.GetEffectiveShape(layer),gap)
                       for layer in layers):return 'native_pad_existing_copper_clearance'
    for fp in fps:
        if uid(fp) not in moved_ids:continue
        layer=pcb.F_CrtYd if fp.GetLayer()==pcb.F_Cu else pcb.B_CrtYd
        courtyard=fp.GetCourtyard(layer)
        if not courtyard.OutlineCount():continue
        for other in fps:
            if other is fp or other.GetLayer()!=fp.GetLayer():continue
            candidate=other.GetCourtyard(layer)
            if candidate.OutlineCount() and courtyard.Collide(candidate,0):return 'native_courtyard_overlap'
    guard=BodyGapGuard(board)
    for i,(layer,row,shape) in enumerate(guard.shapes):
        for other_layer,other_row,other_shape in guard.shapes[i+1:]:
            if layer==other_layer and (row['footprintId'] in moved_ids or other_row['footprintId'] in moved_ids) and shape.Collide(other_shape,0):
                return 'limited_body_overlap'
    conflicts=[c for c in guard.audit(board) if c['footprintId'] in moved_ids and c['objectId'] not in removed_ids]
    if conflicts:return 'existing_copper_through_moved_body'
    return None


def _copper_measure(board):
    length=0.;vias=0
    for item in board.GetTracks():
        if isinstance(item,pcb.PCB_VIA):vias+=1
        else:length+=pcb.ToMM(item.GetLength())
    return dict(lengthMm=round(length,6),viaCount=vias)


def _replacement_measure(board,removals):
    measure=_copper_measure(board);tracks={uid(t):t for t in board.GetTracks()}
    for row in removals:
        item=tracks[row['objectId']]
        if isinstance(item,pcb.PCB_VIA):measure['viaCount']-=1
        else:measure['lengthMm']-=pcb.ToMM(item.GetLength())
    return measure


def _boundary_anchors(board,settings,removals):
    """Keep the open end of each removed outer-net chain as a soft target."""
    selected={uid(f) for f in _footprints(board,[*settings['references'],*settings.get('companionReferences',[])])};tracks={uid(t):t for t in board.GetTracks()}
    selected_pads={p.GetNetname():set() for f in board.GetFootprints() if uid(f) in selected for p in f.Pads() if p.GetNetname()}
    for f in board.GetFootprints():
        if uid(f) not in selected:continue
        for pad in f.Pads():
            if pad.GetNetname():selected_pads[pad.GetNetname()].add(tuple(xy(pad.GetPosition())))
    counts={}
    for row in removals:
        item=tracks.get(row['objectId'])
        if item is None or isinstance(item,pcb.PCB_VIA) or row['net']==settings['linkNet']:continue
        counter=counts.setdefault(row['net'],{})
        for at in (tuple(xy(item.GetStart())),tuple(xy(item.GetEnd()))):counter[at]=counter.get(at,0)+1
    anchors={net:sorted(at for at,count in counter.items() if count%2 and at not in selected_pads.get(net,set()))
             for net,counter in counts.items()}
    # A genuinely unconnected terminal has no removed-chain boundary. Use
    # fixed native terminals on its net, never a guessed net-name role or a
    # moving companion. Existing chain boundaries keep priority.
    for net,positions in selected_pads.items():
        if net==settings['linkNet'] or anchors.get(net):continue
        targets={tuple(xy(p.GetPosition())) for f in board.GetFootprints() if uid(f) not in selected
                 for p in f.Pads() if p.GetNetname()==net}
        anchors[net]=sorted(targets,key=lambda at:(min(math.dist(at,p) for p in positions),at))[:4]
    return anchors


def _rotation_states(fp,rotations,link_net):
    states={}
    for rotation in rotations:
        local=pcb.FOOTPRINT(fp);local.SetPosition(point([0,0]));local.SetOrientationDegrees(rotation)
        link=_link_pad(local,link_net);box=link.GetBoundingBox()
        states[rotation]=dict(link=xy(link.GetPosition()),half=[pcb.ToMM(box.GetWidth())/2,pcb.ToMM(box.GetHeight())/2],
            external=[dict(net=p.GetNetname(),vector=xy(p.GetPosition())) for p in local.Pads() if p.GetNetname() and p.GetNetname()!=link_net])
    return states


def _source_alignment_specs(fps,settings):
    """Keep an existing tighter axial gap; move only the orthogonal offset."""
    pads=[_link_pad(fp,settings['linkNet']) for fp in fps];positions=[xy(p.GetPosition()) for p in pads]
    rotations=[round(fp.GetOrientationDegrees())%360 for fp in fps];result=[]
    for axis,direction in enumerate(([1,0],[0,1])):
        gap=abs(positions[1][axis]-positions[0][axis])-sum(_pad_half_extent(p,direction) for p in pads)
        movement=abs(positions[1][1-axis]-positions[0][1-axis])
        # The requested gap remains the target for re-packing. This separate
        # seed only preserves a legal-looking source gap, never narrows it.
        if not (0<gap<settings['connectedPadGapMm'] and 1e-6<movement<=min(.5,settings['area']['grid'])):continue
        for anchor in (0,1):
            if fps[1-anchor].IsLocked():continue
            result.append((movement,dict(kind='moved',midpoint=None,anchor=anchor,alignmentAxis=axis,
                rotations=rotations,sourceAxisGapMm=round(gap,6),estimatedMoveDistanceMm=round(movement,6))))
    return sorted(result,key=lambda row:(row[0],row[1]['anchor'],row[1]['alignmentAxis']))


def _shared_node_specs(fps,settings,states,anchors):
    """Keep distributed parts near their own fixed outer-net terminals.

    A common bus does not imply that its series parts should be packed together.
    Orient each external pad toward its witnessed source-chain anchor, align
    its transverse coordinate, and keep the original coordinate along that
    exit. The other node terminals stay fixed and still require actual routing.
    """
    options=[]
    for fp,rotations in zip(fps,states):
        at=xy(fp.GetPosition());original_rotation=round(fp.GetOrientationDegrees())%360;rows=[]
        for rotation,state in rotations.items():
            if fp.IsLocked() and rotation!=original_rotation:continue
            for pad in state['external']:
                for target in anchors.get(pad['net'],()):
                    vector=pad['vector'];axis=0 if abs(vector[0])>=abs(vector[1])else 1
                    if abs(vector[axis])<1e-6:continue
                    for advance in (0.,min(.5,settings['area']['grid']/4)):
                        centre=list(at);centre[1-axis]=target[1-axis]-vector[1-axis]
                        centre[axis]+=math.copysign(advance,target[axis]-at[axis])
                        if not _inside_area(centre,settings['area']):continue
                        distance=math.dist(at,centre)
                        if distance>settings['area']['grid']+1e-6 or fp.IsLocked() and distance>1e-6:continue
                        along=target[axis]-centre[axis]
                        if along*vector[axis]<=0:continue
                        lead=abs(along-vector[axis]);turns=min((rotation-original_rotation)%360,(original_rotation-rotation)%360)/90
                        rows.append((lead,distance+turns*.25,rotation,[round(v,6)for v in centre]))
        if not rows:return []
        options.append(sorted(rows)[:4])
    ranked=[]
    for a in options[0]:
        for b in options[1]:
            ranked.append((a[0]+b[0],a[1]+b[1],dict(kind='moved',midpoint=None,
                rotations=[a[2],b[2]],individualPlacements=[a[3],b[3]],
                estimatedExternalLeadMm=round(a[0]+b[0],6),placementSeed='shared-node-external-exits')))
    ranked.sort(key=lambda row:(row[0],row[1],row[2]['rotations'],row[2]['individualPlacements']))
    return [((lead,travel),spec)for lead,travel,spec in ranked]


def _exit_nudge_specs(fps,settings,states,anchors):
    """Small independent exit-directed poses, without repacking a shared bus."""
    original=[xy(fp.GetPosition()) for fp in fps]
    rotations=[round(fp.GetOrientationDegrees())%360 for fp in fps];buckets=[]
    step=min(.5,settings['area']['grid']/2)
    for index,fp in enumerate(fps):
        if fp.IsLocked():continue
        rows=[]
        for size in (step/2,step):
            for dx,dy in ((size,0),(-size,0),(0,size),(0,-size)):
                centres=[list(at) for at in original];centres[index]=[round(original[index][0]+dx,6),round(original[index][1]+dy,6)]
                if not _inside_area(centres[index],settings['area']):continue
                cost=0.
                for pad in states[index][rotations[index]]['external']:
                    at=[centres[index][i]+pad['vector'][i] for i in (0,1)]
                    targets=anchors.get(pad['net'],())
                    if targets:cost+=min(math.dist(at,t) for t in targets)
                rows.append((cost,dict(kind='moved',midpoint=None,rotations=rotations,
                    individualPlacements=centres,placementSeed='bounded-pad-exit-nudge',estimatedExternalLeadMm=round(cost,6))))
        buckets.append(sorted(rows,key=lambda row:(row[0],row[1]['individualPlacements'])))
    return [rows[i] for i in range(max(map(len,buckets),default=0)) for rows in buckets if i<len(rows)]


def _candidate_specs(board,settings,removals=()):
    if settings.get('linkTopology')=='escape-cluster':
        return dict(kind='fixed',placementSeed='source'),[(i,dict(kind='moved',placements=p,placementSeed='explicit-escape-cluster'))for i,p in enumerate(settings['poseCandidates'])]
    refs=settings['references'];fps=_footprints(board,refs);locked=[f.IsLocked() for f in fps]
    if all(locked):raise ValueError('Both local placement footprints are locked')
    rotations=[_rotation_options(round(f.GetOrientationDegrees()),settings['allowQuarterTurns']) for f in fps]
    states=[_rotation_states(fp,options,settings['linkNet']) for fp,options in zip(fps,rotations)]
    anchors=_boundary_anchors(board,settings,removals)
    directions=([1,0],[-1,0],[0,1],[0,-1])
    specs=[dict(kind='fixed',midpoint=None,direction=[1,0],rotations=[round(f.GetOrientationDegrees())%360 for f in fps])]
    # Keeping either original footprint origin is useful even when neither is
    # locked. These exact native anchors avoid forcing a small local alignment
    # onto the coarser area grid.
    anchors_to_try=[locked.index(True)] if any(locked) else [0,1]
    locations=[(None,index) for index in anchors_to_try]
    if not any(locked):locations.extend((midpoint,None) for midpoint in _grid(settings['area']))
    for midpoint,anchor in locations:
        for direction in directions:
            for a in rotations[0]:
                for b in rotations[1]:
                    if locked[0] and a!=round(fps[0].GetOrientationDegrees())%360 or locked[1] and b!=round(fps[1].GetOrientationDegrees())%360:continue
                    specs.append(dict(kind='moved',midpoint=midpoint,anchor=anchor,direction=direction,rotations=[a,b]))
    original={uid(f):(xy(f.GetPosition()),round(f.GetOrientationDegrees())%360) for f in fps}
    ranked=[]
    for spec in specs[1:]:
        current=[states[index][rotation] for index,rotation in enumerate(spec['rotations'])];direction=spec['direction']
        distance=sum(state['half'][0 if direction[0] else 1] for state in current)+settings['connectedPadGapMm']
        if spec['midpoint'] is None:
            fixed=spec['anchor'];moving=1-fixed;centres=[list(original[uid(fp)][0]) for fp in fps]
            fixed_link=[centres[fixed][axis]+current[fixed]['link'][axis] for axis in (0,1)];sign=1 if moving==1 else -1
            target=[fixed_link[axis]+sign*direction[axis]*distance for axis in (0,1)]
            centres[moving]=[target[axis]-current[moving]['link'][axis] for axis in (0,1)]
        else:
            centres=[]
            for index,state in enumerate(current):
                target=[spec['midpoint'][axis]+(-1 if index==0 else 1)*direction[axis]*distance/2 for axis in (0,1)]
                centres.append([target[axis]-state['link'][axis] for axis in (0,1)])
        if not all(_inside_area(at,settings['area']) for at in centres):continue
        turns=sum(min((rotation-original[uid(fp)][1])%360,(original[uid(fp)][1]-rotation)%360)/90
                  for fp,rotation in zip(fps,spec['rotations']))
        travel=sum(math.dist(original[uid(fp)][0],at) for fp,at in zip(fps,centres))
        facing=0
        external=0
        exit_penalty=0
        for index,(state,centre) in enumerate(zip(current,centres)):
            wanted=1 if index==0 else -1
            # Zero only when each linked pad faces the other footprint.  A
            # perpendicular pad axis is still undesirable and must not tie
            # with a genuinely facing pair.
            facing+=max(0,math.hypot(*state['link'])-wanted*sum(a*b for a,b in zip(state['link'],direction)))
            for pad in state['external']:
                targets=anchors.get(pad['net'],())
                if targets:
                    at=[centre[axis]+pad['vector'][axis] for axis in (0,1)]
                    target=min(targets,key=lambda t:math.dist(at,t));external+=math.dist(at,target)
                    axis=pad['vector'];norm=math.hypot(*axis)
                    if norm:
                        outward=sum(axis[i]*(target[i]-at[i]) for i in (0,1))/norm
                        # Penalize an external anchor behind its pad's outward axis.
                        # This only orders legal poses; actual copper still decides.
                        exit_penalty+=max(0.,-outward)

        spec={**spec,'externalExitPenaltyMm':round(exit_penalty,6),'estimatedExternalLeadMm':round(external,6),'estimatedMoveDistanceMm':round(travel,6)}
        ranked.append((facing,external+exit_penalty,travel+turns*.25,spec))
    ranked.sort(key=lambda row:(row[0],row[1],row[2],row[3]['rotations'],row[3]['direction'],row[3]['midpoint']or[]))
    # Geometry rejection is cheap. Keep a small oversample so one blocked pose
    # does not consume a routing-candidate slot.
    retain=max(0,settings['maxCandidates']*4-1)
    aligned=_source_alignment_specs(fps,settings)
    shared=(_shared_node_specs(fps,settings,states,anchors)
            if settings.get('linkTopology')=='shared-node' else [])
    nudges=_exit_nudge_specs(fps,settings,states,anchors) if settings.get('linkTopology')=='shared-node' else []
    # Interleave independent nudges with distributed alignment; one family
    # must not consume every bounded routing slot.
    exit_seeds=[row for i in range(max(len(shared),len(nudges)))
                for rows in (shared,nudges) if i<len(rows) for row in [rows[i]]]
    return specs[0],(exit_seeds+aligned+[(rank,spec) for _,_,rank,spec in ranked])[:retain]


def candidates(board,params):
    """Create concrete native poses; routing is isolated per pose by the host."""
    started=time.monotonic();settings=params['localPlacement'];source_groups=physical_groups(board)
    copper_rework=params.get('localCopperRework')
    contact_rework=params.get('localContactRework')
    if copper_rework:native_source_check(board,copper_rework)
    if contact_rework:contact_source_check(board,contact_rework)
    source_measure=_copper_measure(board);replacement_measure=_replacement_measure(board,params.get('removeCopper',[]))
    fixed,ranked=_candidate_specs(board,settings,params.get('removeCopper',[]))
    companions=_footprints(board,settings.get('companionReferences',[]))
    selected_fps=_footprints(board,[*settings['references'],*settings.get('companionReferences',[])])
    source_poses={uid(f):dict(at=xy(f.GetPosition()),rotation=round(f.GetOrientationDegrees())%360) for f in selected_fps}
    original={id:row['at'] for id,row in source_poses.items()};seen=set();rows=[];next_index=0
    # Removed KiCad SWIG proxies own detached native objects. Keep every
    # mutated detour board and its retirees alive across subsequent LoadBoard
    # calls, rather than releasing them when one nested attempt returns.
    detour_lifetimes=[]
    def removals(moves,pre_copper=()):
        return for_pose(params.get('removeCopper',[]),settings.get('copperOwners',{}),settings.get('linkCopperIds',[]),
                        settings.get('sourcePoses',{}),moves,copper_rework,pre_copper,contact_rework) if settings.get('sourcePoses') else params.get('removeCopper',[])
    offsets=[]
    if companions and not settings.get('poseCandidates') and not any(f.IsLocked() for f in companions):
        anchors=_boundary_anchors(board,settings,params.get('removeCopper',[]));step=min(.5,settings['area']['grid']/4)
        def lead(offset):
            return sum(min((math.dist([xy(p.GetPosition())[i]+offset[i] for i in (0,1)],target)
                            for target in anchors.get(p.GetNetname(),())),default=0.) for fp in companions for p in fp.Pads())
        offsets=[];companion_ids={uid(f) for f in companions}
        for size in (step,step/2):
            for offset in ([size,0],[-size,0],[0,size],[0,-size]):
                trial=pcb.LoadBoard(board.GetFileName())
                for fp in _footprints(trial,settings['companionReferences']):
                    fp.SetPosition(point([xy(fp.GetPosition())[i]+offset[i] for i in (0,1)]))
                blocked=_quick_geometry(trial,companion_ids,params['outline'],params['rules'],[r['objectId'] for r in params.get('removeCopper',[])]) is not None
                offsets.append((blocked,lead(offset),offset))
        # Prefer small legal-looking nudges, rather than letting a coarse
        # pair-placement grid force every companion into an adjacent pad.
        # This only orders proposals; the combined pose still uses one screen.
        offsets=[at for _,_,at in sorted(offsets)[:2]]

    def attempt(spec,offset=None,pair_index=None,detour=None,contact=None):
        nonlocal next_index
        index=next_index;next_index+=1
        candidate=pcb.LoadBoard(board.GetFileName())
        moves=[];reason=None
        if spec['kind']=='moved':
            moves,reason=_pose(candidate,spec,settings)
            if reason:
                row=dict(index=index,kind='moved',status='rejected',reason=reason);rows.append(row);return row
        if offset:
            for fp in _footprints(candidate,settings['companionReferences']):
                at=[xy(fp.GetPosition())[i]+offset[i] for i in (0,1)];fp.SetPosition(point(at))
                moves.append(dict(kind='move_footprint',objectId=uid(fp),expectedHash=settings['footprintHashes'][uid(fp)],
                                  at=at,rotation=round(fp.GetOrientationDegrees())%360))
        changed=moved_ids(source_poses,moves)
        pre_copper=(detour or contact or {}).get('operations',[])
        signature=(tuple(sorted((m['objectId'],tuple(m['at']),m['rotation']) for m in moves if m['objectId'] in changed)),
                   tuple((op['kind'],tuple(op.get('at',[])),tuple(tuple(p) for p in op.get('points',[]))) for op in pre_copper))
        if signature in seen:return None
        seen.add(signature)
        retired=[]
        if pre_copper:
            # Resolve source ownership before moving: native connectivity can
            # otherwise reinterpret old touching copper at the new pad pose.
            candidate=pcb.LoadBoard(board.GetFileName());tracks={uid(t):t for t in candidate.GetTracks()}
            detour_lifetimes.append((candidate,retired,tracks))
            for removal in removals(moves,pre_copper):
                item=tracks[removal['objectId']];retired.append(item);candidate.Remove(item)
            for move in moves:
                fp=next(f for f in candidate.GetFootprints() if uid(f)==move['objectId'])
                fp.SetPosition(point(move['at']));fp.SetOrientationDegrees(move['rotation'])
            reason=screen_contact(candidate,params,contact_rework,pre_copper) if contact else screen_operations(candidate,params,copper_rework,pre_copper)
            if not reason:apply_memory(candidate,pre_copper,refill_board=False)
        if changed and not reason:reason=_quick_geometry(candidate,changed,params['outline'],params['rules'],[r['objectId'] for r in removals(moves,pre_copper)])
        exit_report=None
        if changed and not reason:
            # Moving a pad onto retained same-net copper can create a new
            # diagonal/offset escape although clearance and connectivity pass.
            # Audit only moved footprints and retained copper before routing;
            # do not spend a route slot on an already invalid inherited exit.
            from pad_exits import native_audit
            exit_report=native_audit(candidate,(params.get('routingStyle')or{}).get('padExitLengthMm',.15),
                footprint_ids=changed,ignore_track_ids=[r['objectId'] for r in removals(moves,pre_copper)])
            if exit_report['findings']:reason='moved_pad_retained_copper_exit'
        row=dict(index=index,kind='fixed' if not moves else 'moved',status='rejected' if reason else 'viable',moves=moves,
            moveDistanceMm=round(sum(math.dist(original[m['objectId']],m['at']) for m in moves),6),
            placementSeed=spec.get('placementSeed')or('source' if spec['kind']=='fixed' else 'source-axis-align' if 'alignmentAxis' in spec else 'original-footprint' if spec['midpoint'] is None else 'area-grid'),
            companionReferences=settings.get('companionReferences',[]) if offset else [],companionOffset=offset,
            pairCandidateIndex=pair_index,sourceAxisGapMm=spec.get('sourceAxisGapMm'),
            spacingPolicy='source-unchanged' if spec['kind']=='fixed' else 'explicit-native-screened-poses' if 'placements' in spec else 'preserve-distributed-exits' if 'individualPlacements' in spec else 'preserve-source-axis-gap' if 'alignmentAxis' in spec else 'declared-packing-target',
            anchorReference=settings['references'][spec['anchor']] if spec.get('anchor') is not None else None,
            estimatedExternalLeadMm=spec.get('estimatedExternalLeadMm'))
        if spec.get('poseTranslation'):row['contactPoseTranslation']=spec['poseTranslation']
        if exit_report:row['retainedPadExits']=exit_report
        if detour:row.update(preCopperOperations=pre_copper,copperRework=dict(policy=copper_rework['policy'],
            sourceObjectIds=copper_rework['sourceObjectIds'],anchors=copper_rework['anchors'],width=copper_rework['width'],
            layer=copper_rework['layer'],offsetMm=detour['offsetMm']))
        if contact:
            row.update(preCopperOperations=pre_copper,contactRework=dict(policy=contact_rework['policy'],
                sourceObjectIds=contact_rework['sourceObjectIds'],viaId=contact_rework['viaId'],anchors=contact_rework['anchors'],offset=contact['offset'],
                retainedContacts=contact_rework.get('retainedContacts',[]),retainedArmContacts=contact_rework.get('retainedArmContacts',[])))
            row['moveDistanceMm']=round(row['moveDistanceMm']+math.hypot(*contact['offset']),6)
        if reason:
            row['reason']=reason
            if reason in {'native_pad_clearance','native_courtyard_overlap'}:
                row['placementBlockers']=placement_blockers(candidate,changed,params['rules']['clearance'])
            if reason=='native_pad_existing_copper_clearance':
                ignored={r['objectId'] for r in removals(moves,pre_copper)};blockers=[]
                for fp in candidate.GetFootprints():
                    if uid(fp) not in changed:continue
                    for pad in fp.Pads():
                        for track in candidate.GetTracks():
                            if uid(track) in ignored or track.GetNetCode()==pad.GetNetCode():continue
                            for layer in candidate.GetEnabledLayers().CuStack():
                                if pad.IsOnLayer(layer) and track.IsOnLayer(layer) and pad.GetEffectiveShape(layer).Collide(track.GetEffectiveShape(layer),pcb.FromMM(params['rules']['clearance'])):
                                    blockers.append(dict(reference=fp.GetReference(),pin=pad.GetNumber(),padNet=pad.GetNetname(),objectId=uid(track),net=track.GetNetname(),layer=candidate.GetLayerName(layer)));break
                row['retainedCopperBlockers']=blockers
        else:row['replacementBase']=_replacement_measure(board,removals(moves,pre_copper))
        rows.append(row);return row

    fixed_row=attempt(fixed);bases=[];failed=[]
    if settings.get('linkTopology')=='escape-cluster':
        from connector_access import review
        selected_ids={uid(f)for f in selected_fps}
        if any(r['footprintId']in selected_ids and r['status']!='passed'for r in review(board)['interfaces']):
            fixed_row.update(status='incumbent',reason='inaccessible_source_retained_without_reroute')
    # Screen each independent pair before multiplying it into companion
    # variants. Invalid front-side pairs must not erase the later pair pool.
    for _,spec in ranked:
        row=attempt(spec)
        if row is None:continue
        (bases if row['status']=='viable' else failed).append((spec,row))
        if len(bases)>=settings['maxCandidates']-1:break
    chosen=[fixed_row]
    if copper_rework:
        # One best source-axis pose receives at most two explicit obstacle
        # detours. Source/base poses retain the original obstacle copper.
        seed=next((spec for _,spec in ranked if 'alignmentAxis' in spec),bases[0][0] if bases else None)
        if bases and len(chosen)<settings['maxCandidates']:chosen.append(bases[0][1])
        if seed:
            for detour in detours(copper_rework):
                row=attempt(seed,detour=detour)
                if row and row['status']=='viable' and len(chosen)<settings['maxCandidates']:chosen.append(row)
    if contact_rework:
        # Test both small source-axis pair alignments with the same bounded
        # contact variants. Fixed/base poses keep the original contact intact.
        seeds=[spec for _,spec in ranked if 'alignmentAxis' in spec][:1]
        if not seeds and bases:seeds=[bases[0][0]]
        if bases and len(chosen)<settings['maxCandidates']:chosen.append(bases[0][1])
        for variant in contact_variants(contact_rework):
            joint_seeds=list(seeds)
            pair=_footprints(board,settings['references'])
            centre=[sum(xy(_link_pad(fp,settings['linkNet']).GetPosition())[i] for fp in pair)/2 for i in (0,1)]
            away=sum((centre[i]-contact_rework['sourceVia']['at'][i])*variant['offset'][i] for i in (0,1))<0
            if seeds and away and not any(fp.IsLocked() for fp in pair):
                scale=min(.2,.08/math.hypot(*variant['offset']))
                joint_seeds.append({**seeds[0],'poseTranslation':[round(-v*scale,6) for v in variant['offset']]})
            for seed in joint_seeds:
                row=attempt(seed,contact=variant)
                if row and row['status']=='viable' and len(chosen)<settings['maxCandidates']:chosen.append(row)
    for spec,base in bases:
        if base not in chosen:
            if len(chosen)>=settings['maxCandidates']:break
            chosen.append(base)
        if len(chosen)>=settings['maxCandidates']:break
        for offset in offsets:
            row=attempt(spec,offset,base['index'])
            if row and row['status']=='viable':chosen.append(row);break
    # A same-layer companion can sometimes rescue a rejected pair. Retain a
    # small fallback for that case, without repeating unrelated-layer failures.
    pair_layers={l for fp in _footprints(board,settings['references']) for p in fp.Pads() for l in board.GetEnabledLayers().CuStack() if p.IsOnLayer(l)}
    companion_layers={l for fp in companions for p in fp.Pads() for l in board.GetEnabledLayers().CuStack() if p.IsOnLayer(l)}
    if offsets and pair_layers&companion_layers and len(chosen)<settings['maxCandidates']:
        for spec,base in failed[:2]:
            for offset in offsets:
                row=attempt(spec,offset,base['index'])
                if row and row['status']=='viable':chosen.append(row)
                if len(chosen)>=settings['maxCandidates']:break
            if len(chosen)>=settings['maxCandidates']:break
    chosen_ids={row['index'] for row in chosen}
    for order,row in enumerate(chosen):row['routeOrder']=order
    others=[{**row,**(dict(status='deferred',reason='routing_candidate_limit') if row['status']=='viable' else {})}
            for row in rows if row['index'] not in chosen_ids]
    cluster=settings.get('linkTopology')=='escape-cluster'
    return dict(policy='bounded-escape-cluster-placement-routing-v1' if cluster else POLICY,sourcePhysicalGroups=source_groups,source=source_measure,replacementBase=replacement_measure,
        candidates=chosen+others,elapsedSeconds=round(time.monotonic()-started,3),
        candidateValidation='native board-edge, pad/hole-clearance and courtyard checks plus limited body/copper overlap; only chosen viable poses enter isolated routing',
        candidateOrdering=('source incumbent followed by bounded explicit poses; native design rules stay unchanged' if cluster else 'small source-axis alignments and independently screened pair poses precede bounded companion variants; source axial gaps never shrink and native design rules stay unchanged')+
            ('; explicit contact variants and paired nudges share the original routing limit' if contact_rework else ''),
        scope=('Two explicitly declared footprints with all attached nets rerouted' if cluster else 'One explicitly declared pair on a shared node; all other node terminals stay fixed'if settings.get('linkTopology')=='shared-node'else'One declared simple series pair')+' and one optional explicit companion'+('; one explicitly selected via star with fixed remote and finite copper contacts' if contact_rework else '')+
            '. Translations are bounded, never inferred from electrical roles. No full DRC or global placement proof.')
