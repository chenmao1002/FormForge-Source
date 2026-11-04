"""Paired geometry proposals from Agent-selected spines; no native file writes."""
import itertools
import collections
import math
import time

import pcbnew as pcb

from pair_geometry import offset,fanouts,metrics,limit_findings
from route_engine import Geometry,LAYERS,uid,xy,point,physical_groups,apply_memory
from routing_rules import native_exit_options


def smooth(points):
    for a,b,c in zip(points,points[1:],points[2:]):
        u=[b[i]-a[i]for i in (0,1)];v=[c[i]-b[i]for i in (0,1)]
        # Use the same one-nanometre miter residual as pair_geometry.directions
        # for the turn comparison. Saved coordinates and all physical clearances
        # stay unchanged; a rounded 45-degree leg must not become 45.000018 deg.
        for direction in (u,v):
            if min(abs(x) for x in direction)>1e-5 and abs(abs(direction[0])-abs(direction[1]))<=1e-6+1e-12:
                mean=(abs(direction[0])+abs(direction[1]))/2
                direction[:]=[math.copysign(mean,x) for x in direction]
        if sum(u[i]*v[i]for i in (0,1))<math.hypot(*u)*math.hypot(*v)/math.sqrt(2)-1e-8:return False
    return True


def pad_fanouts(pad,target,layer,width,continuation):
    """Generate endpoint connectors after a native, cardinal copper neck."""
    at=xy(pad.GetPosition());result=[]
    headings=((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1))
    heading=min(range(8),key=lambda i:math.dist(headings[i],tuple(0 if abs(v)<1e-6 else math.copysign(1,v)for v in continuation)))
    joins=[target]
    # A two-leg connector may otherwise meet the trunk at 90 degrees. A short
    # terminal lead supplies all three legal approach headings without moving
    # the Agent-selected paired spine or relaxing its length/skew contract.
    for i in (heading,(heading-1)%8,(heading+1)%8):
        direction=headings[i];step=.25 if all(direction) else .35
        joins.append([round(target[j]-direction[j]*step,6)for j in (0,1)])
    for rule in native_exit_options(pad,max(.15,width/2+.001),layer):
        lead=[round(at[i]+rule['axis'][i]*rule['length'],6) for i in (0,1)]
        for join in joins:
            for tail in fanouts(lead,join):
                path=[at]+tail+([target] if join!=target else [])
                extended=path+[[target[j]+continuation[j]for j in (0,1)]]
                if smooth(extended) and path not in result:result.append(path)
    return sorted(result,key=lambda path:sum(math.dist(a,b)for a,b in zip(path,path[1:])))


def plan(board,params,progress):
    req=params['pair'];started=time.monotonic();before=physical_groups(board)
    remove=params.get('removeTrackIds',[])
    tracks={uid(t):t for t in board.GetTracks()}
    if len(set(remove))!=len(remove) or any(i not in tracks or isinstance(tracks[i],(pcb.PCB_VIA,pcb.PCB_ARC)) for i in remove):
        raise ValueError('Replacement requires distinct existing straight-track IDs')
    for id in remove:board.Delete(tracks[id])
    if remove:
        from copper_zones import refill
        refill(board)
    if params.get('preTracks'):
        if any(op['kind']!='add_track' for op in params['preTracks']):raise ValueError('Pre-tracks must be explicit validated branch trims')
        apply_memory(board,params['preTracks'])
    geometry=Geometry(board,params['outline'],params['rules'])
    if req['layer'] not in geometry.names:raise ValueError('Pair layer is disabled')
    layer=geometry.names.index(req['layer'])
    selected=[]
    for rail in [req['positive'],req['negative']]:
        endpoints=[]
        for field in ['source','target']:
            anchor=rail[field];matches=[p for f in board.GetFootprints() if f.GetReference()==anchor['reference'] for p in f.Pads() if p.GetNumber()==anchor['pin']]
            if len(matches)!=1 or matches[0].GetNetname()!=rail['net'] or not matches[0].IsOnLayer(geometry.layers[layer]):
                raise ValueError('Pair endpoint absent, ambiguous, wrong net or unavailable layer')
            endpoints.append(matches[0])
        selected.append(endpoints)
    if len({uid(p) for ends in selected for p in ends})!=4:raise ValueError('Pair requires four distinct endpoint pads')
    if req['positive']['net']==req['negative']['net']:raise ValueError('Pair nets must differ')
    sign=1 if req['positiveSide']=='left' else -1;half=(req['width']+req['gap'])/2
    trunks=[offset(req['spine'],sign*half),offset(req['spine'],-sign*half)]
    choices=[]
    for endpoints,trunk in zip(selected,trunks):
        choices.extend([pad_fanouts(endpoints[0],trunk[0],geometry.layers[layer],req['width'],[trunk[1][j]-trunk[0][j]for j in (0,1)]),
            [list(reversed(p))for p in pad_fanouts(endpoints[1],trunk[-1],geometry.layers[layer],req['width'],[trunk[-2][j]-trunk[-1][j]for j in (0,1)])]])
    attempts=[];closest=None;closest_score=None
    def diagnostics():
        return dict(fanoutChoiceCounts=[len(c)for c in choices],attemptCount=len(attempts),reasonCounts=dict(collections.Counter(attempts)),
            closestGeometry=closest,scope='Evaluated fanout geometry and rejection reasons only; rejected geometry is not a legal route or native proof.')
    for ends in itertools.product(*choices):
        if time.monotonic()-started>params['searchSeconds']:break
        rails=[];indices=[]
        for i,trunk in enumerate(trunks):
            start,end=ends[2*i:2*i+2];rails.append(start[:-1]+trunk+end[1:]);indices.append([len(start)-1,len(start)-1+len(trunk)-1])
        if not all(smooth(rail)for rail in rails):attempts.append('sharp_fanout_or_spine_turn');continue
        measured=metrics(rails,trunks,req['width'])
        failures=limit_findings(measured,req)
        score=(len(failures),sum(max(0,value/max(req[key],1e-6)-1)for value,key in [(max(measured['lengthsMm']),'maxLength'),(measured['skewMm'],'maxSkew'),(max(measured['fanoutLengthsMm']),'maxFanoutLength')]))
        if closest_score is None or score<closest_score:
            closest_score=score;closest=dict(metrics=measured,failedLimits=failures,limits={k:req[k]for k in ['maxLength','maxSkew','maxFanoutLength','gap']})
        if failures:attempts.append('geometry_limit_exceeded');continue
        policy=[req['positive'],req['negative']];blocked=None
        for rail,wire in zip(rails,policy):
            code=board.GetNetcodeFromNetname(wire['net'])
            if not all(geometry.pad_exit_clear(a,b,layer,code,req['width']) for a,b in zip(rail,rail[1:])):
                blocked='pad_exit_collar';break
            if not all(geometry.clear(a,b,layer,code,req['width'],req['clearance']) for a,b in zip(rail,rail[1:])):
                blocked='foreign_obstacle';break
        if blocked:attempts.append(blocked);continue
        clear=True
        # Native shape distance proves inter-rail separation, including fanouts and bends.
        for a,b in zip(rails[0],rails[0][1:]):
            shape=pcb.SHAPE_SEGMENT(point(a),point(b),pcb.FromMM(req['width']))
            for c,d in zip(rails[1],rails[1][1:]):
                if shape.Collide(pcb.SHAPE_SEGMENT(point(c),point(d),pcb.FromMM(req['width'])),max(0,pcb.FromMM(req['clearance'])-2)):
                    clear=False
        if not clear:attempts.append('rails_too_close_or_crossed');continue
        operations=[dict(kind='add_track',net=r['net'],width=req['width'],layer=req['layer'],points=p) for r,p in zip(policy,rails)]
        apply_memory(board,operations);after=physical_groups(board)
        connections=[dict(net=r['net'],fromPad=uid(e[0]),toPad=uid(e[1])) for r,e in zip(policy,selected)]
        contract=dict(request=req,rails=[dict(net=r['net'],points=p,trunkIndices=idx,
            endpoints=[dict(id=uid(pad),at=xy(pad.GetPosition())) for pad in endpoints]) for r,p,idx,endpoints in zip(policy,rails,indices,selected)])
        progress(dict(index=1,total=1,net=req['name'],connections=2,phase='candidate'))
        return dict(strategy='formforge-agent-spine-offset-pair-v1',operations=operations,connections=connections,physicalGroupsBefore=before,
            predictedPhysicalGroupsAfter=after,pairContract=contract,attempts=attempts,plannedConnectionCount=2,budgetExhausted=False,
            elapsedSeconds=round(time.monotonic()-started,3),diagnostics=diagnostics(),manufacturingApproved=False,scope='Agent-selected paired section; saved native geometry and physical connections must pass.')
    return dict(strategy='formforge-agent-spine-offset-pair-v1',operations=[],connections=[],physicalGroupsBefore=before,
        attempts=attempts,diagnostics=diagnostics(),plannedConnectionCount=0,budgetExhausted=time.monotonic()-started>=params['searchSeconds'],manufacturingApproved=False,
        scope='No legal candidate for this explicit spine/fanout budget; not proof that the board cannot be routed.')
