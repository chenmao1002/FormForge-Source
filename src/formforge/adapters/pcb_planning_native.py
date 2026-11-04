"""Read-only native probes for proposed interfaces, exits and explicit corridors."""
import pcbnew as pcb
from route_engine import Geometry, point, xy, uid
from body_gap import BodyGapGuard
from pad_escape_rework import evaluate
from connector_access import review
from local_placement import placement_blockers


def probe(board, params):
    request=params['request'];basis=params['basis']
    interfaces=[]
    access=review(board) if request.get('probeInterfaces',True) else dict(interfaces=[])
    for row in access['interfaces'][:8]:
        item=dict(row,blockers=[])
        if row['status']!='passed':
            # Load a private in-memory board for each proposal; never mutate the source.
            trial=pcb.LoadBoard(params['board'])
            fp=next(f for f in trial.GetFootprints()if uid(f)==row['footprintId'])
            fp.SetPosition(point(row['suggestedPlacement']['at']));fp.SetOrientationDegrees(row['suggestedPlacement']['rotation'])
            item['blockers']=placement_blockers(trial,{uid(fp)},basis['rules']['clearance'])
            item['blockingCopperIds']=sorted({uid(t) for p in fp.Pads() for t in trial.GetTracks()
                if t.GetNetCode()!=p.GetNetCode() and any(p.IsOnLayer(l)and t.IsOnLayer(l)and
                    p.GetEffectiveShape(l).Collide(t.GetEffectiveShape(l),pcb.FromMM(basis['rules']['clearance'])-1)
                    for l in trial.GetEnabledLayers().CuStack())})
        interfaces.append(item)
    targets=request.get('escapeTargets',[])
    escapes=evaluate(board,basis,targets) if targets else dict(targets=[])
    for row in escapes['targets']:
        pad_blocks={};track_ids=set()
        for exit in row['exits']:
            if exit['clear']:continue
            layer=board.GetLayerID(exit['layer'])
            shape=pcb.SHAPE_SEGMENT(point(row['at']),point(exit['end']),pcb.FromMM(row['width']))
            for fp in board.GetFootprints():
                for p in fp.Pads():
                    if p.GetNetname()!=row['net'] and p.IsOnLayer(layer) and p.GetEffectiveShape(layer).Collide(shape,pcb.FromMM(row['clearance'])-1):
                        pad_blocks[uid(p)]=dict(reference=fp.GetReference(),pin=p.GetNumber(),padId=uid(p),at=xy(p.GetPosition()))
            track_ids.update(uid(t)for t in board.GetTracks() if t.GetNetname()!=row['net'] and t.IsOnLayer(layer)
                             and t.GetEffectiveShape(layer).Collide(shape,pcb.FromMM(row['clearance'])-1))
        row.update(padBlockers=sorted(pad_blocks.values(),key=lambda p:(p['reference'],p['pin'])),blockingObjectIds=sorted(track_ids))
    corridors=[]
    if request.get('corridors'):
        geometry=Geometry(board,basis['outline'],basis['rules'],body_guard=BodyGapGuard(board))
        for c in request['corridors']:
            rows=[];net=board.GetNetcodeFromNetname(c['net'])
            for name in c['layers']:
                layer=board.GetLayerID(name);index=geometry.layers.index(layer);objects=set()
                clear=all(geometry.clear(a,b,index,net,c['width'],c['clearance']) for a,b in zip(c['points'],c['points'][1:]))
                for a,b in zip(c['points'],c['points'][1:]):
                    shape=pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(c['width']))
                    for obj in [p for fp in board.GetFootprints()for p in fp.Pads()]+list(board.GetTracks()):
                        if obj.GetNetCode()!=net and obj.IsOnLayer(layer) and obj.GetEffectiveShape(layer).Collide(shape,pcb.FromMM(c['clearance'])-1):objects.add(uid(obj))
                rows.append(dict(layer=name,clear=clear,blockingObjectIds=sorted(objects),
                    otherObstaclesMayInclude='filled zones, keepouts, holes, board edge and limited body envelopes'))
            corridors.append(dict(**c,layersReview=rows,status='clear' if all(r['clear']for r in rows)else 'blocked'))
    return dict(interfaces=interfaces,escapes=escapes,corridors=corridors,omittedInterfaceCount=max(0,len(access['interfaces'])-8),
                interfaceProbeLimit=8,scope='In-memory native geometry probes only; no saved edits, routing, reservation or manufacturing approval.')
