"""Native escape witnesses for bounded obstacle-copper rework."""
import pcbnew as pcb
from route_engine import Geometry,point,xy,uid
from routing_rules import native_exit_options
from body_gap import BodyGapGuard


def evaluate(board,basis,targets):
    geometry=Geometry(board,basis['outline'],basis['rules'],body_guard=BodyGapGuard(board))
    rows=[];blockers=set()
    for target in targets:
        width=target['width'];clearance=target['clearance']
        if width<basis['rules']['trackWidth'] or clearance<basis['rules']['clearance']:
            raise ValueError('Escape target width/clearance cannot relax board rules')
        pads=[p for f in board.GetFootprints() if f.GetReference()==target['reference']
              for p in f.Pads() if p.GetNumber()==target['pin']]
        if len(pads)!=1:raise ValueError('Escape target requires one exact native pad')
        pad=pads[0];a=xy(pad.GetPosition());exits=[];root_blocks=[];pad_layers=[]
        root=pcb.SHAPE_CIRCLE(point(a),pcb.FromMM(width/2))
        for index,layer in enumerate(geometry.layers):
            if not pad.IsOnLayer(layer):continue
            pad_layers.append(board.GetLayerName(layer))
            for fp in board.GetFootprints():
                if fp.GetReference()!=target['reference']:continue
                for other in fp.Pads():
                    if other.GetNetCode()!=pad.GetNetCode() and other.IsOnLayer(layer) and other.GetEffectiveShape(layer).Collide(root,pcb.FromMM(clearance)-1):
                        root_blocks.append(dict(reference=fp.GetReference(),pin=other.GetNumber(),layer=board.GetLayerName(layer),at=xy(other.GetPosition())))
            for rule in native_exit_options(pad,max(.15,width/2+.001),layer):
                # A short extra forward corridor reserves room for the first
                # legal turn, rather than counting only a barely clear neck.
                b=[round(a[i]+rule['axis'][i]*(rule['length']+.35),6) for i in (0,1)]
                legal=geometry.clear(a,b,index,pad.GetNetCode(),width,clearance) and geometry.pad_exit_clear(a,b,index,pad.GetNetCode(),width)
                exits.append(dict(layer=board.GetLayerName(layer),end=b,clear=legal))
                shape=pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(width))
                if not legal:
                    for track in board.GetTracks():
                        if track.GetNetCode()!=pad.GetNetCode() and track.IsOnLayer(layer) and track.GetEffectiveShape(layer).Collide(shape,pcb.FromMM(clearance)-1):blockers.add(uid(track))
        rows.append(dict(**target,net=pad.GetNetname(),padId=uid(pad),at=a,legalExitCount=sum(e['clear']for e in exits),exits=exits,
            rootClearanceBlockers=root_blocks,rootBlockedOnEveryCopperLayer=bool(pad_layers) and set(pad_layers)<={r['layer']for r in root_blocks}))
    return dict(targets=rows,blockedTargetCount=sum(r['legalExitCount']==0 for r in rows),blockingObjectIds=sorted(blockers),
        scope='Native straight neck plus bounded forward corridor only; actual routing and final checks remain required.')


def improved(before,after):
    old=[r['legalExitCount'] for r in before['targets']];new=[r['legalExitCount'] for r in after['targets']]
    return all(b>=a for a,b in zip(old,new)) and any(b>a for a,b in zip(old,new))


def path_improves(before,open_space,net,layer,width,path):
    """Cheap finite-width gate before mutating/auditing a candidate board."""
    shapes=[pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(width)) for a,b in zip(path,path[1:])]
    counts=[]
    for target in open_space['targets']:
        count=0
        for exit in target['exits']:
            if not exit['clear']:continue
            ray=pcb.SHAPE_SEGMENT(point(target['at']),point(exit['end']),pcb.FromMM(target['width']))
            if target['net']!=net and exit['layer']==layer and any(shape.Collide(ray,pcb.FromMM(target['clearance'])-1) for shape in shapes):continue
            count+=1
        counts.append(count)
    old=[t['legalExitCount']for t in before['targets']]
    return all(b>=a for a,b in zip(old,counts)) and any(b>a for a,b in zip(old,counts))


def plan_contact(board,params):
    """Reuse the strict via-star contract without requiring a two-part pair."""
    from local_contact_rework import prepare,native_source_check,variants,screen_operations
    from route_engine import physical_groups,apply_memory
    from copper_zones import refill
    from pad_exits import native_audit,proof
    import time
    request=params['request'];basis=params['basis'];targets=request.get('escapeTargets',[])
    if not targets:raise ValueError('Explicit pad escape targets are required for contact rework')
    paired={r['net']for c in basis.get('pairedSections',[])for r in c['rails']}
    protected={t['id']for t in params['snapshot']['tracks']if t['net']in paired}
    policies=[dict(net=n,width=max((t['width']for t in params['snapshot']['tracks']if t['net']==n and t['type']=='segment'),default=basis['rules']['trackWidth']),clearance=basis['rules']['clearance'],layers=[board.GetLayerName(l)for l in board.GetEnabledLayers().CuStack()])for n in request['nets']]
    spec=prepare(params['snapshot'],dict(contactRework=request['contactRework'],area=basis['outline']),policies,protected)
    objects={uid(t):t for t in board.GetTracks()}
    if any(objects[id].IsLocked() for id in spec['sourceObjectIds']):raise ValueError('Locked copper cannot move')
    native_source_check(board,spec);before=evaluate(board,basis,targets);groups=physical_groups(board);audit=native_audit(board)
    via=objects[spec['viaId']];retired=[objects[r['objectId']]for r in spec['removeCopper']]
    for t in retired:board.Remove(t)
    rows=[];operations=[];created=[];deadline=time.monotonic()+request['searchSeconds']
    try:
        for variant in variants(spec):
            if time.monotonic()>=deadline:break
            reason=screen_operations(board,dict(outline=basis['outline'],rules=basis['rules'],nets=policies),spec,variant['operations'])
            if reason:rows.append(dict(offset=variant['offset'],status=reason));continue
            created=apply_memory(board,variant['operations']);after=evaluate(board,basis,targets)
            valid=improved(before,after) and physical_groups(board)==groups and proof(dict(layoutFeedback=dict(padExits=audit)),dict(layoutFeedback=dict(padExits=native_audit(board))))['passed']
            if valid:
                operations=spec['removals']+variant['operations'];rows.append(dict(offset=variant['offset'],status='improved'));break
            for item in created:board.Remove(item)
            created=[]
            via.SetPosition(point(spec['sourceVia']['at']));refill(board)
            rows.append(dict(offset=variant['offset'],status='no_legal_escape_improvement'))
    finally:
        if not operations:
            for item in created:board.Remove(item)
            via.SetPosition(point(spec['sourceVia']['at']))
            for t in retired:board.Add(t)
            refill(board)
    return dict(operations=operations,chains=[],contactRework=dict(policy=spec['policy'],viaId=spec['viaId'],candidates=rows),escapeBefore=before,escapeAfter=evaluate(board,basis,targets),eligibleChainCount=0,budgetExhausted=time.monotonic()>=deadline,
        scope='Explicit via and all incident arms; preserve via identity, diameters, arm widths, fixed contacts and native connectivity. Final acceptance still required.')
