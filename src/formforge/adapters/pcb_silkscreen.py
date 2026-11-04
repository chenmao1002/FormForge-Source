"""Explicit footprint silk line edits with whole native-content preservation."""
import hashlib
import json
import math

from .native_syntax import Node


KIND='set_footprint_silkscreen_line'
REMOVE_KIND='remove_footprint_silkscreen_line'
REMOVE_POLYGON_KIND='remove_footprint_silkscreen_polygon'
KINDS={KIND,REMOVE_KIND,REMOVE_POLYGON_KIND}


def editable_shapes(snapshot):
    return snapshot.get('silkscreenLines',[])+snapshot.get('silkscreenPolygons',[])


def content_hash(value):
    def value_of(item):
        return [value_of(v) for v in item.values] if isinstance(item,Node) else item
    return hashlib.sha256(json.dumps(value_of(value),ensure_ascii=False,separators=(',',':')).encode('utf8')).hexdigest()


def add_native_evidence(tree,snapshot):
    lines={line['id']:line for line in editable_shapes(snapshot)}
    remainders={}
    for fp in tree.children('footprint'):
        fid=fp.one('uuid').values[1];remaining=[]
        for child in fp.values:
            if isinstance(child,Node) and child.kind in {'fp_line','fp_poly'} and child.one('uuid').values[1] in lines:
                line=lines[child.one('uuid').values[1]]
                if line['footprintId']!=fid:raise ValueError('Silkscreen parent differs from native source')
                line['sha256']=hashlib.sha256(child.raw.encode('utf8')).hexdigest()
                continue
            remaining.append(child)
        node=Node(remaining,0,'');remainders[fid]=content_hash(node)
    if any('sha256' not in line for line in lines.values()):raise ValueError('Silkscreen line UUID missing from native source')
    rest=Node([v for v in tree.values if not isinstance(v,Node) or v.kind!='footprint'],0,'')
    snapshot['silkscreenPreservation']=dict(footprintRemainders=remainders,boardRemainderHash=content_hash(rest))
    return list(lines.values())


def proof(before,after,operations):
    edits=[op for op in operations if op['kind'] in KINDS]
    if not edits:return None
    old={p['id']:p for p in editable_shapes(before)};new={p['id']:p for p in editable_shapes(after)}
    wanted={p['objectId']:p for p in edits};targets=[];untouched=True
    for id,item in old.items():
        actual=new.get(id);request=wanted.get(id)
        if request and request['kind'] in {REMOVE_KIND,REMOVE_POLYGON_KIND}:
            targets.append(dict(objectId=id,reference=item['reference'],action='remove',matched=actual is None));continue
        if item['type']=='silkscreen_polygon':
            untouched=untouched and actual==item
            continue
        same=bool(actual) and all(item[k]==actual[k] for k in ('type','footprintId','reference','width','layer'))
        for key in ('start','end'):
            expected=request[key] if request else item[key]
            same=same and math.dist(expected,actual[key])<=1.1e-6 if actual else False
        if request:targets.append(dict(objectId=id,reference=item['reference'],action='set',matched=same,start=actual['start'] if actual else None,end=actual['end'] if actual else None))
        else:untouched=untouched and same
    preserved=before['silkscreenPreservation']==after['silkscreenPreservation']
    groups=lambda snap:sorted((g['net'],sorted(g['pads'])) for g in snap['physicalGroups'])
    physical=groups(before)==groups(after)
    expected_ids=set(old)-{p['objectId'] for p in edits if p['kind'] in {REMOVE_KIND,REMOVE_POLYGON_KIND}};matched_ids=expected_ids==set(new)
    passed=matched_ids and set(wanted)<=set(old) and all(r['matched'] for r in targets) and untouched and preserved and physical
    return dict(passed=passed,targets=targets,lineSetMatchesTarget=matched_ids,untouchedSilkscreenPreserved=untouched,
        otherNativeContentPreserved=preserved,physicalConnectionsPreserved=physical,
        scope='explicit line endpoints and line/polygon deletions only; all other native footprint/board content and full physical groups preserved')
