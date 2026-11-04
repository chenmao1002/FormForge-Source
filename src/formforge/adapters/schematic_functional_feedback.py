"""Declared block identities and read-only, physically partitioned measures."""
from collections import defaultdict
from pathlib import Path

from .schematic import load_pages,properties,object_id,location
from ..store import DomainError
from .schematic_wire_quality import wire_quality,on
from .schematic_fields import symbol_box,text_box,overlaps,crosses
from .schematic_references import reference_style,reference_labels


def declarations(source,entry,op,previous=None):
    pages=load_pages(source,entry);groups=[]
    if op.get('blockLayout'):
        selected={s['sheet']:s for s in op['blockLayout']['sheets']}
        for g in (previous or {}).get('groups',[]):
            replacement=selected.get(g['sheet'])
            claimed={i for new in replacement['groups'] for i in new['objectIds']} if replacement else set()
            same_id=replacement and g['id'] in {new['id'] for new in replacement['groups']}
            if not same_id and not claimed.intersection([*g.get('objectIds',[]),*g['symbolIds']]):groups.append(g)
        for sheet in op['blockLayout']['sheets']:
            nodes={object_id(n):n for n in pages[sheet['sheet']]['objects']}
            for g in sheet['groups']:
                existing=[i for i in g['objectIds'] if i in nodes]
                symbols=[i for i in existing if nodes[i].kind=='symbol']
                prior=next((old for old in (previous or {}).get('groups',[]) if old['id']==g['id'] and old['sheet']==sheet['sheet']),{})
                branches=[b for b in prior.get('branches',[]) if all(b[end]['objectId'] in symbols for end in ['source','target'])]
                groups.append(dict(id=g['id'],name=g['name'],sheet=sheet['sheet'],objectIds=existing,
                    symbolIds=symbols,branches=branches))
    else:
        titles=op.get('groupTitles') or []
        declared=(op.get('organization') or {}).get('groups') or [dict(id=f'group-{i+1}',name=titles[i] if i<len(titles) else f'Group {i+1}',references=refs) for i,refs in enumerate(op.get('groups') or [])]
        by_ref={properties(n)['Reference'].values[2]:(sheet,object_id(n)) for sheet,p in pages.items() for n in p['tree'].children('symbol')}
        for g in declared:
            members=[by_ref[r] for r in g['references']];sheets={s for s,_ in members}
            if len(sheets)!=1:raise ValueError('Declared functional group spans files')
            branches=[]
            for b in (op.get('intent') or {}).get('branches',[]):
                if b['source']['reference'] not in g['references'] or b['target']['reference'] not in g['references']:continue
                branches.append(dict(id=b['id'],kind=b['kind'],evidence=b['evidence'],**{end:dict(objectId=by_ref[b[end]['reference']][1],pin=b[end]['pin']) for end in ['source','target']}))
            groups.append(dict(id=g['id'],name=g['name'],sheet=next(iter(sheets)),symbolIds=[i for _,i in members],branches=branches))
    return dict(version=1,source='explicit-layout-declarations',groups=groups)


def field_geometry(source,entry,page):
    boxes={};fields={};unavailable=[]
    project=source/Path(entry).with_suffix('.kicad_pro')
    try:references=reference_labels(page,reference_style(project if project.is_file() else None),Path(entry).stem)
    except (DomainError,ValueError,KeyError):references={};unavailable.append(dict(reason='instance_reference_style'))
    for n in page['objects']:
        identity=object_id(n)
        if n.kind=='symbol':
            try:boxes[identity,'body']=symbol_box(page,n,allow_complex=True)
            except (ValueError,IndexError,TypeError) as exc:unavailable.append(dict(objectId=identity,reason=str(exc)))
            for name,f in properties(n).items():
                try:
                    values=[text_box(f,symbol_rotation=location(n)[2],content=v) for v in (references.get(identity,[]) if name=='Reference' else []) or [None]]
                    values=[b for b in values if b]
                    if values:
                        box=(min(b[0] for b in values),min(b[1] for b in values),max(b[2] for b in values),max(b[3] for b in values))
                        boxes[identity,name]=box
                        if name in {'Reference','Value'}:fields[identity,name]=box
                except (ValueError,IndexError,TypeError) as exc:unavailable.append(dict(objectId=identity,property=name,reason=str(exc)))
        elif n.kind in {'label','global_label','hierarchical_label','text'}:
            try:
                at=location(n);at=[*at[:2],at[2]%180] if n.kind!='text' else at
                box=text_box(n,at=at,margin=1.75 if n.kind=='global_label' else .25)
                if box:boxes[identity,'text']=box
            except (ValueError,IndexError,TypeError) as exc:unavailable.append(dict(objectId=identity,reason=str(exc)))
    return boxes,fields,unavailable


def analyze(source,entry,sheet,page,objects,feedback,spec,budget=500000):
    groups=[g for g in (spec or {}).get('groups',[]) if g['sheet']==sheet]
    result=dict(version=1,status='available' if groups else 'not_declared',groups=[],electricalVerdict=False,
        scope='Explicit block symbol UUIDs on this file; all shared instances use the same physical geometry. Cross-block wires and crossings are separate. No label-name shortcuts or functional inference.')
    if not groups:return result
    by_id={o['id']:o for o in objects};claims={};explicit={};invalid=[]
    if len({g['id'] for g in groups})!=len(groups):invalid.append('duplicate_group_id')
    for g in groups:
        for identity in g['symbolIds']:
            if identity in claims:invalid.append('duplicate_symbol_claim')
            claims[identity]=g['id']
        for identity in g.get('objectIds',[]):
            if identity in explicit:invalid.append('duplicate_object_claim')
            explicit[identity]=g['id']
    if invalid:result.update(status='unavailable',reason=','.join(sorted(set(invalid))));return result
    segments=[(o['id'],tuple(a),tuple(b)) for o in objects if o['type']=='wire' for a,b in zip(o['points'],o['points'][1:])]
    parent={}
    def root(p):
        parent.setdefault(p,p)
        while parent[p]!=p:parent[p]=parent[parent[p]];p=parent[p]
        return p
    def union(a,b):parent[root(a)]=root(b)
    pins={};unknown=[]
    for o in objects:
        if o['type']=='symbol':
            if 'pinPositions' not in o:unknown.append(o['id'])
            for p in o.get('pinPositions',[]):pins[o['id'],p['number']]=tuple(p['at']);root(tuple(p['at']))
        elif o['type'] in {'label','global_label','hierarchical_label','junction','no_connect'} and o.get('at'):root(tuple(o['at'][:2]))
    for _,a,b in segments:union(a,b)
    boxes,fields,field_unknown=field_geometry(source,entry,page)
    work=len(parent)*len(segments)+len(fields)*(len(boxes)+len(segments))+len(segments)**2
    if work>budget:result.update(status='unavailable',reason='functional_geometry_budget');return result
    for p in list(parent):
        for _,a,b in segments:
            if on(p,a,b):union(p,a)
    shared=object();unassigned=object()
    owners=defaultdict(set)
    for (identity,_),p in pins.items():owners[root(p)].add(claims.get(identity,unassigned))
    for o in objects:
        if o['id'] not in explicit:continue
        if o['type']=='wire':owners[root(tuple(o['points'][0]))].add(explicit[o['id']])
        elif o.get('at') and o['type'] in {'label','global_label','hierarchical_label','junction','no_connect'}:owners[root(tuple(o['at'][:2]))].add(explicit[o['id']])
    buckets=defaultdict(list);wire_bucket={}
    for o in objects:
        if o['type']!='wire':continue
        ids=owners[root(tuple(o['points'][0]))]
        bucket=next(iter(ids)) if len(ids)==1 and unassigned not in ids else shared if len(ids)>1 else unassigned
        buckets[bucket].append(o);wire_bucket[o['id']]=bucket
    # Each field is checked against the whole page, including foreign blocks.
    findings=defaultdict(list)
    for key,box in fields.items():
        hits=[dict(type='wire',objectId=i) for i,a,b in segments if crosses(box,a,b)]
        hits += [dict(type='envelope',objectId=k[0],property=k[1]) for k,b in boxes.items() if k!=key and overlaps(box,b)]
        if hits:findings[key[0]].append(dict(property=key[1],conflicts=hits))
    for g in groups:
        identities=set(g['symbolIds']);symbols=[by_id[i] for i in g['symbolIds'] if i in by_id and by_id[i]['type']=='symbol']
        missing=sorted(i for i in identities if i not in by_id or by_id[i]['type']!='symbol')
        local=[*symbols,*buckets[g['id']]]
        for o in objects:
            if o['type'] in {'label','global_label','hierarchical_label','junction','no_connect'} and o.get('at') and owners[root(tuple(o['at'][:2]))]=={g['id']}:local.append(o)
        continuity=[]
        for b in g.get('branches',[]):
            keys=[(b[k]['objectId'],b[k]['pin']) for k in ['source','target']]
            state='unavailable' if any(k not in pins for k in keys) else 'connected' if root(pins[keys[0]])==root(pins[keys[1]]) else 'not_physically_connected'
            continuity.append(dict(id=b['id'],kind=b['kind'],status=state,source=b['source'],target=b['target']))
        result['groups'].append(dict(id=g['id'],name=g['name'],symbolIds=g['symbolIds'],references=[s.get('reference') for s in symbols],
            status='partial' if missing or any(i in unknown for i in identities) else 'available',missingSymbolIds=missing,
            instancePaths=page['instancePaths'],symbolCount=len(symbols),wireIds=[o['id'] for o in buckets[g['id']]],wireQuality=wire_quality(local,budget),
            labelOnlySymbols=[r for r in feedback.get('labelOnlySymbols',[]) if r['objectId'] in identities],labelOnlyPins=[r for r in feedback.get('labelOnlyPins',[]) if r['objectId'] in identities],
            branchContinuity=continuity,fields=dict(status='partial' if field_unknown else 'estimated',
                checkedFieldCount=sum(k[0] in identities for k in fields),conflicts=[dict(objectId=i,**f) for i in sorted(identities) for f in findings[i]],unavailable=field_unknown)))
    all_symbols=[o for o in objects if o['type']=='symbol']
    result['sharedWires']=dict(wireIds=[o['id'] for o in buckets[shared]],wireQuality=wire_quality([*all_symbols,*buckets[shared]],budget))
    result['unassignedWires']=dict(wireIds=[o['id'] for o in buckets[unassigned]],wireQuality=wire_quality([*all_symbols,*buckets[unassigned]],budget))
    result['crossBlockCrossings']=[]
    crossings=(feedback.get('wireQuality') or {}).get('nonConnectingCrossings')
    if crossings is None:result['crossBlockCrossings']=None
    else:
        for c in crossings:
            pairs=[p for p in c['wirePairs'] if len({wire_bucket[i] for i in p})>1 or wire_bucket[p[0]] in {shared,unassigned}]
            if pairs:result['crossBlockCrossings'].append(dict(at=c['at'],wirePairs=pairs))
    result['coverage']=dict(unavailableSymbolIds=unknown,unassignedSymbolIds=[o['id'] for o in all_symbols if o['id'] not in claims],
        wireAssignment='each complete physical wire component belongs exclusively to one block, shared, or unassigned; segments are never clipped to block rectangles')
    if unknown or field_unknown or any(g['status']!='available' for g in result['groups']):result['status']='partial'
    return result
