"""Planning and follow-up decisions from complete snapshots, without role guessing."""
from collections import defaultdict


def requested_checks(request):
    group_only=bool(request.get('groups')) and not any(request.get(k)for k in ('corridors','escapeTargets','routeJobId'))
    interfaces=request.get('probeInterfaces')
    if interfaces is None:interfaces=not group_only
    geometry=bool(interfaces or any(request.get(k)for k in ('corridors','escapeTargets','routeJobId')))
    return dict(probeInterfaces=interfaces,geometry=geometry)


def route_followup(snapshot, plan, policies):
    pads={p['id']:(f,p) for f in snapshot['footprints'] for p in f['pads']}
    rules={r['net']:r for r in policies};targets={};failures=[]
    for row in plan.get('attempts',[]):
        if row.get('reason')=='candidate_found':continue
        failures.append({k:row[k] for k in ('net','reason','fromPad','toPad','sourcePorts','targetPorts') if k in row})
        if row.get('reason')!='no_legal_pad_exit':continue
        policy=rules.get(row['net'])
        if not policy:continue
        for key,count in (('fromPad','sourcePorts'),('toPad','targetPorts')):
            if row.get(count)!=0 or row.get(key) not in pads:continue
            f,p=pads[row[key]]
            targets[p['id']]=dict(reference=f['reference'],pin=p['number'],width=policy['width'],clearance=policy['clearance'])
    return dict(policy='evidence-based-pcb-followup-v1',failures=failures,
                escapeTargets=list(targets.values())[:16],omittedTargetCount=max(0,len(targets)-16),
                nextAction='pcb_plan' if targets else 'review_search_scope_and_budget' if failures else 'review_saved_candidate',
                instruction='Diagnose zero-port endpoints before retrying; a search budget failure is not proof of a geometric blockage.')


def report(snapshot, request, native):
    fps={f['reference']:f for f in snapshot['footprints']};membership={};groups=[]
    net_groups=defaultdict(lambda:defaultdict(list))
    for g in request.get('groups',[]):
        row={**g,'outOfRegion':[],'anchorPins':[]}
        for ref in g['references']:
            f=fps[ref];membership[ref]=g['name'];region=g.get('region')
            if region:
                a=f['bounds']['origin'];size=f['bounds']['size'];o=region['origin']
                if not all(o[i]-1e-6<=a[i] and a[i]+size[i]<=o[i]+region[('width','height')[i]]+1e-6 for i in (0,1)):
                    row['outOfRegion'].append(ref)
            for p in f['pads']:
                if p['net']:net_groups[p['net']][g['name']].append(dict(reference=ref,pin=p['number'],at=p['at']))
                if ref==g.get('anchorReference') and p['net']:
                    dx,dy=(p['at'][i]-f['at'][i] for i in (0,1))
                    side='centre' if max(abs(dx),abs(dy))<1e-6 else ('right' if dx>0 else 'left') if abs(dx)>abs(dy) else ('bottom' if dy>0 else 'top')
                    row['anchorPins'].append(dict(pin=p['number'],net=p['net'],at=p['at'],boardSide=side))
        groups.append(row)
    links=[dict(net=n,groups=[dict(name=g,terminals=rows)for g,rows in sorted(gs.items())])
           for n,gs in sorted(net_groups.items()) if len(gs)>1]
    interfaces=[];actions=[]
    for row in native.get('interfaces',[]):
        refs=sorted({row['reference'],*(b['otherReference'] for b in row.get('blockers',[]))})
        nets={p['net'] for ref in refs for p in fps[ref]['pads'] if p['net']}
        copper={t['id']:t for t in snapshot['tracks']}
        nets.update(copper[id]['net']for id in row.get('blockingCopperIds',[])if id in copper)
        nets=sorted(nets)
        item={**row,'affectedReferences':refs,'affectedNets':nets}
        if row['status']!='passed':
            item['nextAction']='resolve_locked_placement' if fps[row['reference']]['locked'] else 'plan_coordinated_interface_placement'
            actions.append(dict(kind=item['nextAction'],reference=row['reference'],references=refs,nets=nets,
                                suggestedPlacement=row['suggestedPlacement'],evidence='native_interface_datum_and_trial_pose'))
        interfaces.append(item)
    repairs=[]
    connector_refs={r['reference'] for r in interfaces}
    connector_refs.update(r['reference']for r in snapshot['layoutFeedback'].get('connectorAccess',{}).get('unconfirmed',[]) if r.get('reference'))
    for row in native.get('escapes',{}).get('targets',[]):
        refs=sorted({r['reference']for r in row.get('padBlockers',[])})
        movable=[ref for ref in refs if ref!=row['reference'] and not fps[ref]['locked'] and not fps[ref].get('mechanicalFeature')
                 and ref not in connector_refs and len({p['number'] for p in fps[ref]['pads']if p['number']})==2]
        kind=('review_footprint_or_route_rule' if row['rootBlockedOnEveryCopperLayer'] else
              'route_with_existing_exit' if row['legalExitCount'] else
              'compare_local_placement' if movable else
              'review_explicit_copper_rework' if row.get('blockingObjectIds') else 'review_uncovered_obstacle')
        involved={row['reference'],*refs};tracks={t['id']:t for t in snapshot['tracks']}
        nets={p['net']for ref in involved for p in fps[ref]['pads']if p['net']}
        nets.update(tracks[id]['net'] for id in row.get('blockingObjectIds',[]) if id in tracks)
        item={**row,'nextAction':kind,'movableCandidates':movable,'affectedReferences':sorted(involved),'affectedNets':sorted(nets)}
        repairs.append(item);actions.append(dict(kind=kind,reference=row['reference'],pin=row['pin'],
            references=sorted(involved),movableCandidates=movable,nets=sorted(nets),objectIds=row.get('blockingObjectIds',[])))
    review=snapshot['layoutFeedback'].get('placementReview',{})
    identities={p['id']:dict(objectId=p['id'],type='pad',reference=f['reference'],pin=p['number'],net=p['net'],footprintId=f['id'])
                for f in fps.values()for p in f['pads']}
    identities.update({t['id']:dict(objectId=t['id'],type=t['type'],net=t['net'],expectedHash=t.get('sha256'))for t in snapshot['tracks']})
    corridors=[{**c,'layersReview':[{**r,'blockers':[identities[id]for id in r['blockingObjectIds']if id in identities]}
                                   for r in c['layersReview']]}for c in native.get('corridors',[])]
    return dict(policy='explicit-pcb-layout-planning-v1',revisionId=snapshot['revisionId'],boardSha256=snapshot['boardSha256'],
        declaredPlan=request,groups=groups,crossGroupNets=links,
        unassignedReferences=sorted(ref for ref in fps if ref not in membership and not fps[ref].get('mechanicalFeature')),
        interfaces=interfaces,unconfirmedInterfaces=snapshot['layoutFeedback'].get('connectorAccess',{}).get('unconfirmed',[]),
        corridors=corridors,repairs=repairs,nextActions=actions,
        omittedInterfaceCount=native.get('omittedInterfaceCount',0),
        placementCandidates=[p for p in review.get('pairs',[])if p.get('needsPlacementReview')],
        mountingReview=snapshot['layoutFeedback'].get('mountingReview'),powerFeeds=snapshot.get('powerFeeds'),
        scope='Complete native snapshot. Functional intent is declared, not inferred from net names. Footprint bounds are planning envelopes. Straight corridors/exits do not prove routability, mating clearance, SI/PI or manufacturing. No edits or reservations are applied.')
