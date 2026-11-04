"""Topology-led placement review, not an automatic placer or adoption gate.

Read actual two-terminal links before asking the router for longer detours.
The length bound ignores bodies/obstacles; only a checked candidate can prove
that rotating, swapping or translating components is an improvement.
"""
import collections
import math

POLICY='two-terminal-placement-review-v1'


def review(footprints,tracks,trace_paths=None):
    if __package__:
        from .routing_rules import trace_chains
        from .route_readability_policy import metrics
    else:
        from routing_rules import trace_chains
        from route_readability_policy import metrics
    by_net=collections.defaultdict(list);parts={};groups=collections.defaultdict(list)
    for fp in footprints:
        electrical=[p for p in fp['pads']if p.get('net')]
        parts[fp['id']]=(fp,electrical)
        for p in electrical:by_net[p['net']].append((fp,p))
    for t in tracks:groups[t['net']].append(t)
    chains=collections.defaultdict(list)
    for c in (trace_chains(footprints,tracks) if trace_paths is None else trace_paths):chains[c['net']].append(c)
    pairs=[];uncovered=[]
    for net,terminals in sorted(by_net.items()):
        if len(terminals)!=2:continue
        (a,p),(b,q)=terminals
        if a['id']==b['id']or any(len(parts[f['id']][1])!=2 for f in (a,b)):continue
        other=[next(x['net']for x in parts[f['id']][1]if x['id']!=pad['id'])for f,pad in terminals]
        # Parallel components and same-net duplicate terminals are not series links.
        if net in other or other[0]==other[1]:continue
        row=dict(net=net,references=sorted([a['reference'],b['reference']]),
            footprintIds=sorted([a['id'],b['id']]),padIds=sorted([p['id'],q['id']]),
            pads=[dict(reference=f['reference'],pin=x['number'],at=x['at'],id=x['id'])for f,x in terminals],
            lockedReferences=sorted(f['reference']for f in (a,b)if f.get('locked')))
        copper=groups[net];choices=chains[net]
        ends={tuple(p['at']),tuple(q['at'])}
        if not copper or any(t['type']!='segment'for t in copper)or len(choices)!=1 or a['layer']!=b['layer']:
            uncovered.append(dict(**row,reason='not_single_surface_linear_chain'));continue
        c=choices[0]
        if c['layer']!=a['layer']or {tuple(c['points'][0]),tuple(c['points'][-1])}!=ends or set(c['objectIds'])!={t['id']for t in copper}:
            uncovered.append(dict(**row,reason='branch_width_change_or_noncentre_endpoint'));continue
        points=c['points'];measure=metrics(points);dx,dy=[abs(p['at'][i]-q['at'][i])for i in (0,1)]
        bound=max(dx,dy)+(math.sqrt(2)-1)*min(dx,dy)
        backwards=[]
        for path in (points,list(reversed(points))):
            step=[path[1][i]-path[0][i]for i in (0,1)];target=[path[-1][i]-path[0][i]for i in (0,1)]
            backwards.append(sum(x*y for x,y in zip(step,target))<-1e-6)
        ratio=measure['lengthMm']/bound if bound else None;excess=max(0,measure['lengthMm']-bound)
        needs_review=bool(bound and excess>1. and ratio>1.5 and (measure['bendCount']>=4 or all(backwards)))
        pairs.append(dict(**row,layer=c['layer'],objectIds=c['objectIds'],points=points,**measure,
            octilinearLowerBoundMm=round(bound,6),excessLengthMm=round(excess,6),detourRatio=round(ratio,3)if ratio else None,
            backwardEscapeCount=sum(backwards),padAxisOffsetMm=round(min(dx,dy),6),needsPlacementReview=needs_review,
            reason='long_detour_between_two_terminal_parts'if needs_review else None))
    pairs.sort(key=lambda r:(-r['excessLengthMm'],r['references']))
    return dict(policy=POLICY,seriesPairCount=len(pairs),reviewCount=sum(p['needsPlacementReview']for p in pairs),pairs=pairs,uncoveredLinks=uncovered,
        threshold=dict(minExcessLengthMm=1.,minDetourRatio=1.5,minBends=4,orBothEscapesBackward=True),
        scope='Advisory simple single-layer linear links between two-terminal footprints only. The octilinear bound ignores obstacles, escape necks and electrical roles; it does not prove an available route or optimal placement. Review pin alignment, rotation, swapping and external feed paths, then validate native candidates. Locked/mechanical constraints remain explicit.')


def compare(previous,actual):
    def get(snapshot):return snapshot.get('layoutFeedback',{}).get('placementReview')or review(snapshot.get('footprints',[]),snapshot.get('tracks',[]))
    before,after=get(previous),get(actual)
    old={(p['net'],tuple(p['padIds'])):p for p in before['pairs']};changes=[]
    for p in after['pairs']:
        key=(p['net'],tuple(p['padIds']));q=old.get(key)
        if q and (abs(p['lengthMm']-q['lengthMm'])>1e-5 or p['bendCount']!=q['bendCount']):
            changes.append(dict(net=p['net'],references=p['references'],before={k:q[k]for k in ('lengthMm','bendCount','excessLengthMm','needsPlacementReview')},after={k:p[k]for k in ('lengthMm','bendCount','excessLengthMm','needsPlacementReview')}))
    return dict(policy=POLICY,beforeReviewCount=before['reviewCount'],afterReviewCount=after['reviewCount'],changedLinks=changes,scope=after['scope'])
