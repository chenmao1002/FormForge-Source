"""Versioned PCB drafts, exact pad-net contracts and an isolated native worker."""
import hashlib
import math
import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .connectivity import assemble, group, fingerprint, read_graph
from .importer import digest, verify_snapshot, within
from .native_syntax import parse
from .kicad import Cancelled
from .pair_geometry import verify as pair_proof
from .board_stackup import validate as validate_stackup, layer_names
from . import pcb_mounting
from . import pcb_silkscreen
from . import pcb_netlist
from . import ordinary_rework
from .local_rework import for_pose as local_rework_for_pose
from .local_copper_rework import prepare as prepare_local_copper_rework
from .local_contact_rework import prepare as prepare_local_contact_rework
from .local_node_link import prepare as prepare_local_node_link
from .pcb_footprint_variants import derive as derive_footprints,restore_libraries
from . import pcb_replacement
from ..store import DomainError, uid, now


def fail(message): raise DomainError('invalid_input', message)


def initialization_replacement(revision, params, source):
    board=revision['entrypoints'].get('board');replacement=params.get('replaceExisting')
    if not board:
        if replacement:fail('此版本没有旧PCB，不能声明重建替换。')
        return None
    if not replacement:fail('此版本已有 PCB；重建须显式提供 replaceExisting 及旧板摘要和原因。')
    path=within(source,board)
    if not path.is_file() or digest(path)!=replacement['expectedBoardHash']:
        raise DomainError('version_conflict','待重建旧板摘要已变化。',409)
    return dict(revisionId=revision['id'],board=board,boardSha256=replacement['expectedBoardHash'],reason=replacement['reason'])


def install_handoff_footprints(source,handoff_root,mapping,reinitialization_id=None):
    # A new handoff can freeze a silk-only derivative under the same alias.
    # Keep prior frozen libraries intact; the new table binds this initialization.
    footprints=source/'formforge-footprints'
    if reinitialization_id:footprints=footprints/reinitialization_id
    for mapped in mapping:
        lib,name=mapped['footprint'].split(':');existing=footprints/(lib+'.pretty')/(name+'.kicad_mod')
        if existing.exists() and digest(existing)!=mapped['sha256']:fail('重建封装库同名内容与冻结交接不一致。')
    shutil.copytree(handoff_root/'footprints',footprints,dirs_exist_ok=True)
    relative=footprints.relative_to(source).as_posix()
    libraries=sorted({m['footprint'].split(':')[0] for m in mapping})
    table='(fp_lib_table\n (version 7)\n'+''.join(f' (lib(name "{lib}")(type "KiCad")(uri "${{KIPRJMOD}}/{relative}/{lib}.pretty")(options "")(descr "FormForge frozen PCB input"))\n' for lib in libraries)+')\n'
    (source/'fp-lib-table').write_text(table,encoding='utf8')
    return footprints


def read_progress(path):
    # A Windows reader can briefly lose access while the worker atomically
    # replaces this advisory snapshot. The final result is read separately.
    try:return json.loads(path.read_text(encoding='utf8'))
    except (OSError,json.JSONDecodeError):return None


def pad_graph(snapshot):
    components, groups, seen = [], {}, set()
    for footprint in snapshot['footprints']:
        ref = footprint['reference']
        if not ref or ref in seen: fail('PCB 存在缺失或重复器件标号。')
        seen.add(ref);pins = {}
        if footprint.get('mechanicalFeature'):continue  # Only source/geometry/identity-verified pure NPTH.
        for pad in footprint['pads']:
            number = pad['number']
            if not number: continue
            net = pad['net'] if pad['netCode'] else None
            if number in pins and pins[number] != net: fail('同号焊盘分属不同网络。')
            pins[number] = net
        components.append(dict(reference=ref,pins=sorted(pins)))
        for pin, net in pins.items(): groups.setdefault(('net',net) if net else ('isolated',ref,pin),[]).append((ref,pin))
    return assemble(components,[group(pins,key[1] if key[0]=='net' else '(isolated)') for key,pins in groups.items()])


def schematic_binding(source, manifest):
    rows = [r for r in manifest if Path(r['path']).suffix in {'.kicad_sch','.kicad_sym'} or
            Path(r['path']).name in {'sym-lib-table','provenance.json','formforge-specification.json'}]
    settings = {}
    for row in manifest:
        if row['path'].endswith('.kicad_pro'):
            data=json.loads(within(source,row['path']).read_text(encoding='utf8'))
            settings[row['path']]={k:data[k] for k in ('schematic','erc') if k in data}
    return fingerprint(dict(files=rows,settings=settings))


def context_binding(job):
    return fingerprint({k:job['contextSnapshot'][k] for k in ('specification','preferences')})


def copper_object(item):
    if item['type']=='zone':
        def ring(points):
            points=list(points)
            if points and points[0]==points[-1]:points.pop()
            if not points:return []
            options=[]
            for order in (points,list(reversed(points))):
                n=min(range(len(order)),key=lambda i:order[i]);options.append(order[n:]+order[:n])
            return min(options)
        outlines=[dict(outer=ring(o['outer']),holes=sorted(ring(h) for h in o['holes'])) for o in item['outlines']]
        return {k:item[k] for k in ('id','type','net','layer','clearance','minimumThickness','thermalGap','thermalSpoke','priority','padConnection','islandRemoval','ruleArea')} | dict(outlines=sorted(outlines,key=lambda o:o['outer']))
    row={k:item[k] for k in ('id','type','net','width')}
    if item['type']=='via':row.update(at=item['at'],drill=item['drill'])
    else:row.update(points=sorted([item['start'],item['end']]),layer=item['layer'])
    return row


def copper_target(before,operations,created):
    # Legacy boards can contain several rule areas with the nil UUID; a zone
    # can also produce one observation per copper layer. Preserve every row.
    target={}
    for t in before['tracks']+before.get('zones',[]):target.setdefault(t['id'],[]).append(copper_object(t))
    additions={}
    for index,operation in enumerate(operations):
        kind=operation['kind']
        if kind in {'remove_track','remove_via','remove_zone'}:target.pop(operation['objectId'])
        elif kind=='move_via':
            rows=target[operation['objectId']]
            if len(rows)!=1 or rows[0]['type']!='via':fail('过孔移动身份不唯一。')
            target[operation['objectId']]=[{**rows[0],'at':operation['at']}]
        elif kind=='add_track':
            for n,(a,b) in enumerate(zip(operation['points'],operation['points'][1:])):
                additions[(index,n)]=dict(type='segment',net=operation['net'],width=operation['width'],points=sorted([a,b]),layer=operation['layer'])
        elif kind=='add_via':additions[(index,0)]=dict(type='via',net=operation['net'],width=operation['diameter'],at=operation['at'],drill=operation['drill'])
        elif kind=='add_zone':
            x,y=operation['outline']['origin'];w,h=operation['outline']['width'],operation['outline']['height']
            item={k:operation[k] for k in ('net','layer','clearance','minimumThickness','thermalGap','thermalSpoke','priority','padConnection')}
            item.update(id='',type='zone',outlines=[dict(outer=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]],holes=[])],islandRemoval='always',ruleArea=False)
            normalized=copper_object(item);normalized.pop('id');additions[(index,0)]=normalized
    keys=[(r['operation'],r['segment']) for r in created]
    if len(set(keys))!=len(keys) or set(keys)!=set(additions):fail('PCB 引擎返回的新增对象与请求数量不一致。')
    for record in created:
        if record['id'] in target:fail('PCB 新增对象 UUID 重复。')
        target[record['id']]=[{**additions[(record['operation'],record['segment'])],'id':record['id']}]
    return sorted([row for rows in target.values() for row in rows],key=lambda t:t['id'])


def copper_matches(expected,actual):
    def equal(a,b):
        if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(a,b,rel_tol=0,abs_tol=1.1e-6)
        if type(a)!=type(b):return False
        if isinstance(a,dict):
            if a.keys()!=b.keys():return False
            # Native nanometre rounding can reverse lexicographic endpoint
            # order on a nearly vertical segment. Segments are undirected;
            # compare both orders with the existing coordinate tolerance.
            if a.get('type')=='segment' and 'points' in a:
                if not (equal(a['points'],b['points']) or equal(a['points'],list(reversed(b['points'])))):return False
                return all(equal(a[k],b[k]) for k in a if k!='points')
            return all(equal(a[k],b[k]) for k in a)
        if isinstance(a,list):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
        return a==b
    remaining={}
    for t in actual['tracks']+actual.get('zones',[]):remaining.setdefault(t['id'],[]).append(copper_object(t))
    for row in expected:
        candidates=remaining.get(row['id'],[])
        match=next((i for i,item in enumerate(candidates) if equal(row,item)),None)
        if match is None:return False
        candidates.pop(match)
    return not any(remaining.values())


def routing_proof(plan,actual):
    before=plan['physicalGroupsBefore'];after=actual['physicalGroups']
    groups=[(g['net'],set(g['pads'])) for g in after]
    preserved=all(any(net==g['net'] and set(g['pads'])<=pads for net,pads in groups) for g in before)
    same_pads={p for g in before for p in g['pads']}=={p for g in after for p in g['pads']}
    matched=all(any(net==c['net'] and {c['fromPad'],c['toPad']}<=pads for net,pads in groups) for c in plan['connections'])
    merges=len(before)-len(after)
    replacement=plan.get('replacement') or plan.get('copperReplacement')
    ids=(replacement.get('removedTrackIds') or replacement.get('removedObjectIds',[])) if replacement else []
    removed=bool(ids) and not set(ids)&{t['id'] for t in actual.get('tracks',[])}
    nets={r['net'] for r in plan.get('copperReplacement',{}).get('nets',[])}
    reworked_complete=all(sum(g['net']==net for g in after)==1 for net in nets)
    progress=merges>=0 and removed if replacement else merges>0
    if nets:progress=progress and bool(plan['connections'])
    return dict(passed=preserved and same_pads and matched and progress and reworked_complete,preservedSourceConnections=preserved,
        declaredConnectionsMatched=matched,padSetMatched=same_pads,physicalComponentsMerged=merges,
        physicalProgressPolicy='preserve-and-replace' if replacement else 'new-connections',replacedTracksAbsent=removed if replacement else None,
        reworkedNetsComplete=reworked_complete,plannedConnectionCount=len(plan['connections']),physicalComponentCountBefore=len(before),physicalComponentCountAfter=len(after))


def new_drc_errors(baseline,candidate):
    """Fewer legacy errors must not hide a new native error pair/type."""
    def key(row):return row['type'],tuple(sorted(i.get('uuid','') for i in row.get('items',[])))
    old={key(row) for row in baseline.get('violations',[]) if row.get('severity')=='error'}
    return [row for row in candidate.get('violations',[]) if row.get('severity')=='error' and key(row) not in old]


def rework_drc_proof(plan,raw,actual):
    """A connected pad graph alone cannot detect newly dangling copper tails."""
    selected={r['net'] for r in plan['connections']}|{r['net'] for r in plan.get('copperReplacement',{}).get('nets',[])};tracks={t['id']:t for t in actual['tracks']}
    def affected(finding):
        copper=[tracks[i['uuid']] for i in finding['items'] if i.get('uuid') in tracks]
        # DRC may attach a rule-area/zone as a second item. Its non-track UUID
        # does not turn an unrelated source net into a reworked net.
        return not copper or any(t['net'] in selected for t in copper)
    findings=[v for v in raw['violations'] if v['type']=='track_dangling' and not v.get('excluded',False) and affected(v)]
    return dict(passed=not findings,selectedNets=sorted(selected),danglingCopperFindingCount=len(findings),findings=findings[:80])


def pad_changes(before,expected,declared):
    old={(f['reference'],p['number']):p['net'] for f in before['footprints'] for p in f['pads'] if p['number']}
    new={(p['reference'],p['pin']):g['name'] for g in expected['groups'] for p in g['pins']}
    if not old.keys()<=new.keys():fail('同步不支持删除器件或旧引脚。')
    actual=sorted([dict(reference=r,pin=p,beforeNet=old.get((r,p)),afterNet=new[(r,p)]) for r,p in new if (r,p) not in old or old[(r,p)]!=new[(r,p)]],key=lambda x:(x['reference'],x['pin']))
    if len({(r['reference'],r['pin']) for r in declared})!=len(declared) or sorted(declared,key=lambda x:(x['reference'],x['pin']))!=actual:
        fail('实际原理图/焊盘网络变化与完整expectedPadChanges不一致。')
    return actual


def sync_proof(before,after,declared,replacements=()):
    old={f['id']:f for f in before['footprints']};new={f['id']:f for f in after['footprints']}
    replaced={r['objectId'] for r in replacements}
    layout=all(i in new and (i in replaced or all(f.get(k)==new[i].get(k) for k in ('reference','footprint','at','rotation','layer','texts','instanceAttributes','locked'))) for i,f in old.items())
    replacement_proof=pcb_replacement.proof(before,after,replacements)
    changed={(r['reference'],r['pin']) for r in declared if r['beforeNet'] is not None}
    excluded={p['id'] for f in before['footprints'] for p in f['pads'] if (f['reference'],p['number']) in changed}
    released={p for row in replacements for p in row['releasedPadIds']};excluded.update(released)
    groups=[(g['net'],set(g['pads'])) for g in after['physicalGroups']]
    preserved=all(not (set(g['pads'])-excluded) or any(net==g['net'] and (set(g['pads'])-excluded)<=pads for net,pads in groups) for g in before['physicalGroups'])
    return dict(passed=layout and preserved and replacement_proof['passed'],originalLayoutAndTextPreserved=layout and not replacements,
                nonTargetLayoutAndTextPreserved=layout,footprintReplacements=replacement_proof,releasedPhysicalPadIds=sorted(released),unmodifiedPhysicalConnectionsPreserved=preserved,
                addedReferences=sorted(f['reference'] for i,f in new.items() if i not in old))


def release_sections(basis,operations,revision_id):
    result=json.loads(json.dumps(basis));seen=set()
    for operation in operations:
        if operation['kind']!='release_pair':continue
        name=operation['name'];matches=[c for c in result.get('pairedSections',[]) if c['request']['name']==name]
        if name in seen or len(matches)!=1 or fingerprint(matches[0])!=operation['expectedHash']:
            raise DomainError('version_conflict','成组契约不存在、重复或摘要已变化。',409)
        seen.add(name);contract=matches[0];result['pairedSections'].remove(contract)
        result.setdefault('releasedPairedSections',[]).append(dict(contract=contract,sourceRevisionId=revision_id,reason=operation['reason'],sha256=operation['expectedHash']))
    return result


def pair_replacement(basis,snapshot,request,replacement):
    """Only the current contract owns deletion scope; never accept caller IDs."""
    matches=[c for c in basis.get('pairedSections',[]) if c['request']['name']==request['name']]
    if not replacement:
        if matches:fail('成组段名称已存在，须明确replaceSection摘要和原因。')
        return []
    if len(matches)!=1 or fingerprint(matches[0])!=replacement['expectedHash']:
        raise DomainError('version_conflict','待重布成组段不存在或摘要已变化。',409)
    old=matches[0]
    if any(old['request'][k]!=request[k] for k in ('positive','negative')):fail('原子重布必须保持原网络、极性和四端点。')
    # A newer fanout algorithm may repair exits under the exact same electrical
    # and spine constraints. The explicit current hash/reason authorizes that
    # atomic rework; pair_plan rejects it later if geometry is also unchanged.
    if not pair_proof([old],snapshot)['passed']:fail('待重布原生铜与保存契约不一致。')
    ids=[s['id'] for rail in old['rails'] for s in rail['segments']]
    if not ids or len(ids)!=len(set(ids)):fail('待重布契约拥有范围为空或重复。')
    foreign={s['id'] for c in basis.get('pairedSections',[]) if c is not old for rail in c['rails'] for s in rail['segments']}
    if set(ids)&foreign:fail('待重布铜与其他成组契约共享，不能隐式拆除。')
    objects={o['id']:o for o in snapshot['objects']}
    if any(i not in objects or objects[i]['type']!='segment' for i in ids):fail('重布仅支持契约拥有的原生直铜段。')
    removals=[dict(kind='remove_track',objectId=i,expectedHash=objects[i]['sha256']) for i in ids];additions=[];seen=set()
    for trim in replacement.get('trimBranches',[]):
        id=trim['objectId'];item=objects.get(id)
        if not item or item['sha256']!=trim['expectedHash']:
            raise DomainError('version_conflict','待裁短支路不存在或摘要已变化。',409)
        if id in seen or id in ids or id in foreign or item['type']!='segment' or item['net'] not in {request['positive']['net'],request['negative']['net']}:
            fail('只可明确裁短两网络未受其他契约保护的独立直段，不可重复或裁剪受保护铜。')
        seen.add(id);a=item['start'];b=item['end'];p=trim['at'];dx=b[0]-a[0];dy=b[1]-a[1];norm=dx*dx+dy*dy
        t=((p[0]-a[0])*dx+(p[1]-a[1])*dy)/norm if norm>1e-12 else 0
        if not 1e-6<t<1-1e-6 or math.dist(p,[a[0]+t*dx,a[1]+t*dy])>1e-6:fail('裁短端点必须严格位于原直段内部，不允许延长或改走向。')
        old_end=item[trim['endpoint']]
        def touches(owned):
            x,y=owned['start'],owned['end'];vx=y[0]-x[0];vy=y[1]-x[1];nn=vx*vx+vy*vy
            u=max(0,min(1,((old_end[0]-x[0])*vx+(old_end[1]-x[1])*vy)/nn)) if nn else 0
            return owned['layer']==item['layer'] and owned['net']==item['net'] and math.dist(old_end,[x[0]+u*vx,x[1]+u*vy])<=(owned['width']+item['width'])/2+1e-6
        if not any(touches(objects[i]) for i in ids):fail('被裁短的原支路端必须实际接触待替换成组段。')
        points=[list(a),list(b)];points[0 if trim['endpoint']=='start' else 1]=list(p)
        removals.append(dict(kind='remove_track',objectId=id,expectedHash=item['sha256']))
        additions.append(dict(kind='add_track',net=item['net'],layer=item['layer'],width=item['width'],points=points))
    return [dict(kind='release_pair',name=request['name'],expectedHash=replacement['expectedHash'],reason=replacement['reason'])]+removals+additions


def text_proof(operations,snapshot):
    objects={f['id']:f for f in snapshot['footprints']};results=[]
    for op in operations:
        if op['kind']!='set_footprint_text':continue
        observed=next((f for f in objects.get(op['objectId'],{}).get('texts',[]) if f['field']==op['field']),None)
        matched=bool(observed) and math.dist(observed['at'],op['at'])<=1.1e-6 and abs((observed['rotation']-op['rotation']+180)%360-180)<=1e-6 and observed['visible']==op['visible']
        results.append(dict(objectId=op['objectId'],field=op['field'],matched=matched,observed=observed))
    return dict(passed=all(r['matched'] for r in results),fields=results)


def board_text_proof(before,after,operations):
    fields=('text','at','rotation','layer','size','thickness','mirrored')
    old={r['id']:r for r in before.get('boardTexts',[])};new={r['id']:r for r in after.get('boardTexts',[])}
    removed={op['objectId']for op in operations if op['kind']=='remove_board_text'}
    preserved=all(i in new and all(r[k]==new[i][k]for k in fields)for i,r in old.items()if i not in removed)
    absent=not removed&new.keys();additions=[op for op in operations if op['kind']=='add_board_text'];available={i:r for i,r in new.items()if i not in old};matches=[]
    for op in additions:
        found=next((i for i,r in available.items()if r['text']==op['text'] and r['layer']==op['layer'] and math.dist(r['at'],op['at'])<=1.1e-6
            and abs((r['rotation']-op['rotation']+180)%360-180)<=1e-6 and all(abs(v-op['size'])<=1.1e-6 for v in r['size'])
            and abs(r['thickness']-op['thickness'])<=1.1e-6 and r['mirrored']==(op['layer']=='B.Silkscreen')),None)
        matches.append(dict(text=op['text'],objectId=found,matched=found is not None))
        if found is not None:available.pop(found)
    return dict(passed=preserved and absent and not available and all(r['matched']for r in matches),preservedExisting=preserved,removedAbsent=absent,
                additions=matches,unexpectedNewCount=len(available),scope='Saved native standalone silk text; circuit identities and copper checked separately.')


def validate_operations(snapshot, operations, rules):
    ordinary_rework.validate_zone_rules(snapshot,operations)
    objects={o['id']:o for o in snapshot['objects']};seen=set();outline_seen=False
    enabled=snapshot.get('copperLayers',['F.Cu','B.Cu']);stackup_seen=False
    nets={p['net'] for o in snapshot['footprints'] for p in o['pads'] if p['netCode']}
    references={f['reference'] for f in snapshot['footprints']}
    if any(op['kind'] in pcb_silkscreen.KINDS for op in operations) and not all(op['kind'] in pcb_silkscreen.KINDS for op in operations):
        fail('丝印线段须单独成批编辑，以证明其他原生内容保持。')
    numeric=[o for o in operations if o['kind']=='set_board_rules']
    if numeric:
        if len(operations)!=1:fail('PCB数值规则须独立修改并检查。')
        from ..pcb_rule_settings import resolve
        resolve({},numeric[0]['rules'])
    via_rules=[o for o in operations if o['kind']=='set_via_rules']
    if via_rules:
        if len(via_rules)!=1 or via_rules[0] is not operations[0]:fail('显式过孔规则操作须位于批次第一项且只能一次。')
        rule=via_rules[0]
        if rule['diameter']-rule['drill']<.15-1e-9:fail('通孔单边铜环至少0.075mm。')
        rules={**rules,'viaDiameter':rule['diameter'],'viaDrill':rule['drill']}
    for operation in operations:
        kind=operation['kind']
        if kind in {'set_board_rules','release_pair','set_zone_rules','set_via_rules','add_board_text'}:continue
        if kind=='add_mounting_hole':
            if operation['reference'] in references:fail('机械孔位号与现有对象或批次重复。')
            references.add(operation['reference']);continue
        if kind=='set_stackup':
            if stackup_seen or operation is not operations[0]:fail('叠层操作必须位于批次第一项且只能一次。')
            stackup_seen=True
            try:enabled=validate_stackup(operation['profile'])
            except ValueError as exc:fail(str(exc))
            if any(o['type']!='via' and o.get('layer') not in enabled for o in snapshot.get('tracks',[])+snapshot.get('zones',[])):fail('不能禁用仍有铜对象的层；须先明确移除并检查。')
            continue
        if 'objectId' in operation:
            if operation['objectId']=='00000000-0000-0000-0000-000000000000':fail('不能按空UUID修改旧版对象；请先明确迁移其唯一身份。')
            target=objects.get(operation['objectId'])
            if not target or target['sha256']!=operation['expectedHash']:
                raise DomainError('version_conflict','PCB 对象不存在或摘要已改变。',409)
            facet=operation['field'] if kind=='set_footprint_text' else 'geometry';key=(target['id'],facet)
            if key in seen:fail('同一批次不能重复修改同一 PCB 对象属性。')
            if kind=='move_footprint' and any((target['id'],f) in seen for f in ('Reference','Value')):fail('器件移动必须位于文字位置修改之前。')
            seen.add(key)
            expected={'move_footprint':'footprint','move_via':'via','set_footprint_text':'footprint','set_footprint_silkscreen_line':'silkscreen_line','remove_footprint_silkscreen_line':'silkscreen_line','remove_footprint_silkscreen_polygon':'silkscreen_polygon','remove_track':'segment','remove_via':'via','remove_zone':'zone','remove_mounting_hole':'footprint','remove_board_text':'board_text'}[kind]
            if target['type']!=expected:fail('PCB 操作与对象类型不一致。')
            if kind=='move_via' and target['net']!=operation['net']:fail('局部接点移动不能改变过孔网络。')
            if kind==pcb_silkscreen.KIND:
                if math.dist(operation['start'],operation['end'])<.001:fail('丝印直线不能为零长或短于0.001mm。')
                if operation['start']==target['start'] and operation['end']==target['end']:fail('丝印端点与当前相同，没有修改。')
            if kind=='remove_mounting_hole' and not target.get('mechanicalFeature'):fail('只能明确删除已验证纯机械孔，不能删除电气器件。')
            if kind=='move_footprint':
                side=operation.get('layer')
                if side is not None and side not in enabled:fail('器件安装面必须是已启用的表面铜层。')
                if target.get('mechanicalFeature'):
                    if operation['rotation']!=0:fail('当前纯机械圆孔移动保持原始0度姿态。')
                    if side is not None and side!=target.get('layer'):fail('纯机械定位孔不支持改变安装面。')
        elif kind=='set_outline':
            if outline_seen:fail('一次仅能修改一次板框。')
            outline_seen=True
        elif operation['net'] not in nets:fail('PCB 铜网络不存在；本轮不允许改变焊盘网络。')
        if kind in {'add_track','add_zone'} and operation['layer'] not in enabled:fail('目标铜层尚未启用。')
        if kind=='add_track':
            if operation['width']<rules['trackWidth']:fail('走线宽度小于此板最小规则。')
            for a,b in zip(operation['points'],operation['points'][1:]):
                dx,dy=abs(a[0]-b[0]),abs(a[1]-b[1])
                # Offset-pair vertices round independently to native nanometres.
                # Match the pair geometry's one-nanometre diagonal tolerance.
                if dx+dy<1e-6 or not (dx<1e-6 or dy<1e-6 or abs(dx-dy)<=1e-6+1e-12):fail('首版走线仅支持非零长度的水平、垂直或45度线段。')
        if kind=='add_via' and (operation['diameter']<rules['viaDiameter'] or operation['drill']<rules['viaDrill'] or operation['drill']>=operation['diameter']):
            fail('过孔尺寸不符合板规则。')
        if kind=='add_zone' and (operation['clearance']<rules['clearance'] or operation['minimumThickness']<ordinary_rework.zone_floors(rules)[0] or operation['thermalSpoke']<ordinary_rework.zone_floors(rules)[1] or operation['thermalGap']<rules['clearance']):
            fail('铜区净距、厚度或热焊盘参数低于板规则。')


def body_gap_proof(plan,actual):
    """Check all newly saved copper against re-extracted, unchanged gap geometry."""
    expected=plan['layoutFeedback']
    fields=('footprintId','reference','footprint','locked','layer','status','reason','polygonMm')
    def geometry(row):return {k:row[k] for k in fields if k in row}
    before=sorted((geometry(r) for r in expected['footprints']),key=lambda r:r['footprintId'])
    after=sorted((geometry(f['bodyGap']) for f in actual['footprints']),key=lambda r:r['footprintId'])
    new_ids={t['id'] for t in actual['tracks']}-set(plan['sourceCopperIds'])
    conflicts=[c for f in actual['footprints'] for c in f['bodyGap']['conflicts'] if c['objectId'] in new_ids]
    binding=(expected.get('revisionId')==plan.get('revisionId') and expected.get('boardSha256')==plan.get('boardSha256'))
    return dict(policy=expected['policy'],passed=binding and before==after and bool(new_ids) and not conflicts,
                sourceBindingMatched=binding,footprintGeometryMatched=before==after,newCopperCount=len(new_ids),
                conflicts=conflicts,coveredFootprintCount=expected['coveredFootprintCount'],uncoveredFootprintCount=expected['uncoveredFootprintCount'],
                revisionId=actual['revisionId'],boardSha256=actual['boardSha256'],scope=expected['scope'])


def local_source_comparison(source,candidate,minimum,style=None):
    """Compare search results with the untouched input, not a failed reroute."""
    missing=candidate['selectedNetUnfinishedComponentCount']-source['selectedNetUnfinishedComponentCount']
    vias=candidate['boardViaCount']-source['boardViaCount']
    reduction=round(source['boardCopperLengthMm']-candidate['boardCopperLengthMm'],6)
    old=source.get('comparisonReadability');new=candidate.get('comparisonReadability')
    prettier=False
    if style and old and new:
        fields=('backtrackCount','shortClusterCount','bendCount')
        allowance=min(style['maxAddedLengthMm'],old['lengthMm']*style['maxAddedLengthRatio'])
        prettier=(all(new[k]<=old[k] for k in fields) and any(new[k]<old[k] for k in fields)
                  and -reduction<=allowance+1e-6)
    if candidate.get('sourceGroupsPreserved') is False:reason='source_connectivity_better'
    elif missing>0:reason='source_connectivity_better'
    elif missing<0:reason='connectivity_improved'
    elif candidate.get('inaccessibleConnectorCount',0)<source.get('inaccessibleConnectorCount',0):reason='connector_access_improved'
    elif vias>0:reason='source_via_count_better'
    elif vias<0:reason='via_count_improved'
    elif prettier:reason='readability_improved_with_bounded_length'
    elif reduction+1e-6<minimum or reduction<=1e-6:reason='source_retained_below_declared_improvement'
    else:reason='copper_improved'
    return dict(preferred=reason in {'connector_access_improved','connectivity_improved','via_count_improved','copper_improved','readability_improved_with_bounded_length'},
                reason=reason,copperReductionMm=reduction,viaChange=vias,unfinishedComponentChange=missing)


def local_retained_plan(candidate,before,selected_nets):
    """Return source geometry and zero edits; discarded search stays diagnostic."""
    from .route_quality import summarize
    return dict(candidate,operations=[],connections=[],plannedConnectionCount=0,attempts=[],
        predictedPhysicalGroupsAfter=before['physicalGroups'],
        completedNetCount=sum(sum(g['net']==net for g in before['physicalGroups'])==1 for net in selected_nets),
        readability=summarize([]),comparisonReadability=None,
        layoutFeedback={**before['layoutFeedback'],'footprints':[],'retainedSource':True})


def local_search_bounds(outline,area,tracks,grid,move_points=()):
    """A grid-aligned search window, preserving the real board legality frame."""
    points=[area['origin'],[area['origin'][0]+area['width'],area['origin'][1]+area['height']]]
    points.extend(move_points)
    points.extend(p for t in tracks for p in ([t['at']] if t['type']=='via' else [t['start'],t['end']]))
    origin=[];sizes=[]
    for axis,size in enumerate(('width','height')):
        base=outline['origin'][axis];end=base+outline[size]
        lo=max(base,base+math.floor((min(p[axis] for p in points)-2-base)/grid)*grid)
        hi=min(end,base+math.ceil((max(p[axis] for p in points)+2-base)/grid)*grid)
        origin.append(round(lo,6));sizes.append(round(hi-lo,6))
    return dict(origin=origin,width=sizes[0],height=sizes[1])


class PcbAdapter:
    def __init__(self,config):
        self.config=config;self.source_sha256=digest(Path(__file__));self.mounting_policy_sha256=digest(Path(pcb_mounting.__file__))
        self.netlist_policy_sha256=digest(Path(pcb_netlist.__file__))
        modules=('connector_access.py','pad_escape_rework.py','existing_track_optimization.py','interface_silkscreen_placement.py','mounting_plan.py','silkscreen_placement.py','mounting_review.py','pcb_interface_legends.py','board_outline.py','pcb_worker.py','pcb_silkscreen.py','pcb_footprint_variants.py','pcb_footprint_replace.py','native_asserts.py','route_engine.py','local_placement.py','local_rework.py','local_copper_rework.py','local_contact_rework.py','local_node_link.py','route_legality.py','route_path_readability.py','route_readability_policy.py','route_quality.py','routing_rules.py','pad_exits.py','placement_feedback.py','body_gap.py','copper_zones.py','pair_geometry.py','pair_routes.py','board_stackup.py','native_syntax.py','reference_terminals.py','ordinary_rework.py')
        modules=(*modules,'pcb_planning_native.py')
        contents={name:Path(__file__).with_name(name).read_bytes() for name in modules}
        self.engine_hashes={name:hashlib.sha256(data).hexdigest() for name,data in contents.items()}
        self.engine_hash=fingerprint(self.engine_hashes);self.engine_root=config.local/'engines'/self.engine_hash
        self.engine_root.mkdir(parents=True,exist_ok=True)
        for name,data in contents.items():
            path=self.engine_root/name
            if path.exists() and digest(path)!=self.engine_hashes[name]:raise DomainError('input_changed','冻结的原生引擎缓存摘要不一致。',409)
            if not path.exists():path.write_bytes(data)

    def describe(self):
        return dict(id='kicad10-pcb-worker', available=bool(self.config.kicad_python),
                    implementation='isolated-pcbnew-swig; replaceable; deprecated-upstream',
                    capabilities=['automatic_mounting_plan','optimize_tracks','optimize_interface_silkscreen','optimize_reference_silkscreen','cleanup_tracks','set_stackup','initialize','read','move_footprint','set_footprint_text','add_board_text','remove_board_text','set_footprint_silkscreen_line','remove_footprint_silkscreen_line','remove_footprint_silkscreen_polygon','set_outline','add_track','remove_track','add_via','remove_via','add_zone','remove_zone','set_zone_rules','add_mounting_hole','remove_mounting_hole','sync_same_footprints','sync_added_components','sync_explicit_footprint_replacements','reference_projection','agent_spine_paired_sections','atomic_paired_section_rework','atomic_ordinary_net_rework','bounded_local_series_pair_placement_routing','bounded_local_shared_node_placement_routing'],
                    engineBundleHash=self.engine_hash,
                    footprintPlacement=dict(layers=['F.Cu','B.Cu'],omittedLayer='preserve-current',flipAxis='top-bottom',rotation='absolute-native-angle-after-flip'),
                    outlinePolicy=dict(default='rounded-rectangle',cornerRadiusMm=3,smallBoardRadius='short-side/4',squareCorners='explicit-user-request-with-reason'),
                    mountingPolicySha256=self.mounting_policy_sha256,
                    netlistPolicySha256=self.netlist_policy_sha256,
                    autonomousPlacement='bounded-declared-series-pair-or-shared-node-and-one-optional-companion',autonomousRouting='bounded-selected-ordinary-nets',routingLayers=4,supportedCopperLayerCounts=[2,4],
                    routingUsesThirdPartyRouter=False,bodyAvoidancePolicies=['smd-pad-gap-v1'],layoutFeedback='limited-smd-pad-gap-audit',placementFeedback='two-terminal-placement-review-v1')

    def worker(self,params,folder,check_cancel,timeout=60,progress=None):
        if not self.config.kicad_python:raise DomainError('dependency_missing','缺少 KiCad 10 Python 板编辑引擎。')
        folder.mkdir(parents=True,exist_ok=True)
        request=folder/'request.json';response=folder/'result.json';log=folder/'worker.log'
        request.write_text(json.dumps(params,ensure_ascii=False,indent=2),encoding='utf8')
        # A content-addressed directory can still change after startup. Check
        # its bytes before launching a process and recording their provenance.
        modules=dict(self.engine_hashes)
        for name,expected in modules.items():
            path=self.engine_root/name
            if not path.is_file() or digest(path)!=expected:
                raise DomainError('input_changed','冻结的原生引擎缓存摘要不一致。',409)
        worker_file=self.engine_root/'pcb_worker.py'
        worker_sha=modules['pcb_worker.py']
        env=os.environ.copy();env.update(KICAD_CONFIG_HOME=str(folder/'config'),TEMP=str(folder),TMP=str(folder),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        deadline=time.monotonic()+timeout
        with log.open('wb') as stream:
            process=subprocess.Popen([self.config.kicad_python,'-I','-B',str(worker_file),str(request),str(response)],
                cwd=folder,env=env,stdout=stream,stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0)
            try:
                last_event=0
                while process.poll() is None:
                    check_cancel()
                    if progress:
                        data=read_progress(folder/'progress.json')
                        if data and data['eventIndex']>last_event:last_event=data['eventIndex'];progress(data)
                    if time.monotonic()>=deadline:raise DomainError('tool_failed','PCB 引擎达到时间预算。')
                    time.sleep(.1)
            finally:
                if process.poll() is None:process.kill()
                process.wait(timeout=10)
        if process.returncode or not response.is_file():
            raise DomainError('tool_failed','PCB 原生操作失败：'+log.read_text(encoding='utf8',errors='replace')[-1800:])
        result=json.loads(response.read_text(encoding='utf8'))
        result['engineProvenance']=dict(workerSha256=worker_sha,modulesSha256=modules,engineBundleHash=self.engine_hash,adapterSha256=self.source_sha256,mountingPolicySha256=self.mounting_policy_sha256,netlistPolicySha256=self.netlist_policy_sha256,pythonPath=self.config.kicad_python)
        return result

    def edit_basis(self,parent,job,*,verified=False):
        original=within(self.config.data,parent['relativePath'])
        if not verified:verify_snapshot(original,parent['manifest'])
        basis=parent.get('pcbDesign')
        if not basis:fail('此板尚无 FormForge PCB 基础契约；已有板导入编辑将在后续接入。')
        if schematic_binding(original,parent['manifest'])!=basis['schematicBinding'] or context_binding(job)!=basis['contextBinding']:
            raise DomainError('version_conflict','原理图或工程条件变化，须先重新同步并审查 PCB。',409)
        if digest(within(original,parent['entrypoints']['board']))!=job['params']['expectedBoardHash']:
            raise DomainError('version_conflict','PCB 文件摘要已变化。',409)
        return original,basis

    def prepare_edit(self,parent,job,folder,check_cancel,read_snapshot=None):
        operations=job['params'].get('operations',[])
        kinds={'optimize_reference_silkscreen','optimize_interface_silkscreen','optimize_tracks'}
        requested=[o for o in operations if o['kind'] in kinds]
        if not requested:return job
        if len(operations)!=1:fail('自动优化须单独成批，以同一原生布局规划。')
        original,basis=self.edit_basis(parent,job)
        before=(read_snapshot or self.read)(parent,folder/'before',check_cancel)
        req=requested[0];tracks=req['kind']=='optimize_tracks';interfaces=req['kind']=='optimize_interface_silkscreen'
        action='existing_tracks_plan' if tracks else 'interface_silkscreen_plan' if interfaces else 'silkscreen_plan'
        generated=self.worker(dict(action=action,board=str(within(original,parent['entrypoints']['board'])),
            outline=basis['outline'],basis=basis,snapshot=before,request=req,
            legends=job['params'].get('interfaceLegends',basis.get('interfaceLegends',[])),
            hashes={o['id']:o['sha256'] for o in before['objects']}),folder/'plan',check_cancel,req.get('searchSeconds',20)+30)
        (folder/'optimization-plan.json').write_text(json.dumps(generated,ensure_ascii=False,indent=2),encoding='utf8')
        if len(generated['operations'])>500:fail('优化批次超过500项，请缩小网络或线链范围。')
        key='_trackOptimization' if tracks else '_silkscreenPlan'
        return {**job,'params':{**job['params'],'operations':generated['operations'],key:generated}}

    def cleanup_noop(self,parent,job,folder,check_cancel,read_snapshot=None):
        """Only a sole cleanup with no contract updates may finish without a candidate."""
        params=job['params'];operations=params.get('operations',[])
        if len(operations)!=1 or operations[0]['kind']!='cleanup_tracks' or params.get('mountingPlan') is not None or params.get('powerFeeds') is not None or params.get('interfaceLegends') is not None:
            return None
        from .pcb_cleanup import plan,locked_ids
        original,basis=self.edit_basis(parent,job)
        before=(read_snapshot or self.read)(parent,folder/'before',check_cancel)
        if pad_graph(before)['hash']!=basis['expectedGraph']['hash']:
            fail('基线焊盘网络已偏离原理图契约。')
        cleanup=plan(before,basis,operations[0]['nets'],locked_ids(within(original,parent['entrypoints']['board'])),check_cancel)
        check_cancel();verify_snapshot(original,parent['manifest'])
        if cleanup['operations']:return None
        return dict(revisionId=parent['id'],baseRevisionId=parent['id'],noop=True,adopted=False,
            checksPassed=False,draftChecksPassed=False,manufacturingApproved=False,changeCount=0,
            cleanup=cleanup,sourceManifestVerified=True,boardSha256=params['expectedBoardHash'],
            checksSkipped=['candidate-creation','candidate-erc-drc','candidate-adoption'],
            reason='清理规划无删除项，完整源清单和工程条件保持；未创建新版本，也未产生新的工程验收结论。')

    def route_plan(self,parent,job,folder,check_cancel,phase,read_snapshot=None):
        original,basis=self.edit_basis(parent,job);params=job['params'];rules=basis['rules']
        names=[n['net'] for n in params['nets']]
        if len(set(names))!=len(names):fail('布线网络不能重复。')
        if params['searchSeconds']+30>=params['timeoutSeconds']:fail('布线总预算须为原生检查保留至少30秒。')
        for net in params['nets']:
            if net['width']<rules['trackWidth'] or net['clearance']<rules['clearance']:fail('布线线宽/净距不得小于本板规则。')
        before=(read_snapshot or self.read)(parent,folder/'before',check_cancel)
        prefix,reworked=ordinary_rework.replacements(basis,before,params['nets'])
        local=params.get('localPlacement');copper_rework=None;contact_rework=None
        if local:
            refs=sorted(local['references']);shared_node=local.get('linkTopology')=='shared-node';escape_cluster=local.get('linkTopology')=='escape-cluster'
            if escape_cluster:
                fps={f['reference']:f for f in before['footprints']}
                if any(r not in fps for r in refs):fail('局部布局器件不存在。')
                pair=dict(objectIds=[],footprintIds=[fps[r]['id']for r in refs],lockedReferences=[r for r in refs if fps[r]['locked']])
            elif shared_node:
                try:pair=prepare_local_node_link(before,local)
                except ValueError as error:fail(str(error))
            else:
                matches=[p for p in before['layoutFeedback']['placementReview']['pairs'] if p['net']==local['linkNet'] and p['references']==refs]
                if len(matches)!=1:fail('局部布局目标必须匹配当前placementReview中一条简单串联链接，或显式选择shared-node。')
                pair=matches[0]
            footprints={f['reference']:f for f in before['footprints']}
            all_refs=refs+local.get('companionReferences',[])
            if any(r not in footprints for r in all_refs):fail('局部布局器件位号不存在。')
            selected={n['net'] for n in params['nets']};required={p['net'] for r in all_refs for p in footprints[r]['pads'] if p['net']}
            if not required<=selected:fail('局部移动可能影响的全部器件网络都必须列入本次布线。')
            if pair['lockedReferences']==refs:fail('局部布局的两个器件均已锁定。')
            if any(n['net']==local['linkNet'] and n.get('replaceCopper') for n in params['nets']):fail('局部布局会自动重布组内串联铜，不能重复声明replaceCopper。')
            objects={o['id']:o for o in before['objects']};protected={s['id'] for c in basis.get('pairedSections',[]) for rail in c['rails'] for s in rail['segments']}
            if set(pair['objectIds'])&protected:fail('局部布局目标铜属于成组契约，须先按成组流程处理。')
            from .routing_rules import trace_chains
            pad_owners={}
            for r in all_refs:
                for p in footprints[r]['pads']:
                    if p['net']:pad_owners.setdefault((p['net'],tuple(p['at'])),set()).add(footprints[r]['id'])
            local_ids=set(pair['objectIds']);local_owners={id:set(pair['footprintIds']) for id in local_ids}
            for chain in trace_chains(before['footprints'],before['tracks']):
                owners=pad_owners.get((chain['net'],tuple(chain['points'][0])),set())|pad_owners.get((chain['net'],tuple(chain['points'][-1])),set())
                if owners:
                    local_ids.update(chain['objectIds'])
                    for id in chain['objectIds']:local_owners.setdefault(id,set()).update(owners)
            if len(local_ids)>(480 if escape_cluster else 64):fail('局部布局影响的铜链超过64个对象，须缩小范围或显式重布。')
            if local_ids&protected:fail('局部布局影响的铜属于成组契约，须先按成组流程处理。')
            try:
                copper_rework=prepare_local_copper_rework(before,local,params['nets'],protected)
                contact_rework=prepare_local_contact_rework(before,local,params['nets'],protected)
            except ValueError as error:fail(str(error))
            explicit_rework=copper_rework or contact_rework
            if explicit_rework and (set(explicit_rework['sourceObjectIds'])&local_ids or
                    set(explicit_rework['sourceObjectIds'])&{o['objectId'] for o in prefix}):
                fail('显式局部障碍铜链不能与自动布局重接或普通replaceCopper范围重复。')
            local_removals=[]
            by_net={}
            for id in sorted(local_ids):
                item=objects.get(id)
                if not item or item['type']!='segment' or item['net'] not in selected:fail('局部布局只支持已选器件端点相连的所选网络原生直线铜。')
                local_removals.append(dict(kind='remove_track',objectId=id,expectedHash=item['sha256']))
                by_net.setdefault(item['net'],[]).append(id)
            if {o['objectId'] for o in prefix}&local_ids:fail('局部布局与显式普通重布范围重复。')
            prefix=prefix+local_removals
            reworked=reworked+[dict(net=net,reason='bounded local placement and route comparison',removedObjectIds=ids) for net,ids in sorted(by_net.items())]
        if any(len(set(n.get('layers',['F.Cu','B.Cu'])))!=len(n.get('layers',['F.Cu','B.Cu'])) or not set(n.get('layers',['F.Cu','B.Cu']))<=set(before['copperLayers']) for n in params['nets']):fail('布线层必须已启用且不能重复。')
        zones={z['id']:z for z in before.get('zones',[])}
        for net in params['nets']:
            allowed=net.get('yieldZones',[])
            if len({r['objectId'] for r in allowed})!=len(allowed):fail('让路铜区不能重复。')
            for consent in allowed:
                zone=zones.get(consent['objectId'])
                if not zone or zone.get('sha256')!=consent['expectedHash']:
                    raise DomainError('version_conflict','让路铜区不存在或摘要已变化。',409)
                if zone['ruleArea'] or not zone['filled'] or zone['net']==net['net']:fail('只支持明确的已填充外网普通铜区让路。')
        existing={p['net'] for f in before['footprints'] for p in f['pads'] if p['netCode']}
        if any(n not in existing for n in names):fail('布线网络不存在；不能通过找路新增网络。')
        search_area=params.get('searchArea')
        if search_area:
            if local:fail('显式搜索区域与局部布局候选不能同时使用。')
            outline=basis['outline'];lo=search_area['origin'];hi=[lo[0]+search_area['width'],lo[1]+search_area['height']]
            board_lo=outline['origin'];board_hi=[board_lo[0]+outline['width'],board_lo[1]+outline['height']]
            if any(lo[i]<board_lo[i]-1e-6 or hi[i]>board_hi[i]+1e-6 for i in (0,1)):
                fail('显式搜索区域必须完全位于板框包围盒内。')
            points=[p['at'] for f in before['footprints'] for p in f['pads'] if p['net'] in names]
            for track in before['tracks']:
                if track['net'] not in names:continue
                points.extend([track['at']] if track['type']=='via' else [track['start'],track['end']])
            outside=[p for p in points if any(p[i]<lo[i]-1e-6 or p[i]>hi[i]+1e-6 for i in (0,1))]
            if outside:fail('显式搜索区域必须覆盖所选网络的全部焊盘和既有铜端点。')
            cells=(math.floor(search_area['width']/params['grid'])+1)*(math.floor(search_area['height']/params['grid'])+1)
            if cells>400000:fail('显式搜索区域仍超过每层400000个栅格，请缩小区域或增大网格。')
        if basis.get('copperIntentPath') and not copper_matches(self.copper_intent(parent,verified=True),before):fail('PCB 基线铜对象已偏离保存的网络意图。')
        private=folder/'workspace';private.mkdir(parents=True);path=private/Path(parent['entrypoints']['board']).name
        shutil.copyfile(within(original,parent['entrypoints']['board']),path)
        def update(data):phase(f"自研搜索 {data['index']}/{data['total']} · {data['net']} · 已找到 {data['connections']} 处连接候选")
        removals=[dict(objectId=o['objectId'],type=next(t['type'] for t in before['tracks'] if t['id']==o['objectId']),net=next(t['net'] for t in before['tracks'] if t['id']==o['objectId'])) for o in prefix]
        local_request=None
        if local:
            fps={f['id']:f for f in before['footprints']}
            local_request={**local,'references':sorted(local['references']),
                'footprintHashes':{footprints[r]['id']:footprints[r]['sha256'] for r in all_refs},
                'copperOwners':{id:sorted(owners) for id,owners in local_owners.items()},'linkCopperIds':pair['objectIds'],
                'sourcePoses':{footprints[r]['id']:{k:footprints[r][k] for k in ('at','rotation')} for r in all_refs}}
            if shared_node:local_request['nodeLink']=pair
        common=dict(board=str(path),outline=basis['outline'],rules=rules,removeCopper=removals,bodyAvoidance=params.get('bodyAvoidance'),routingStyle=params.get('routingStyle'),
            maxPairAttemptsPerConnection=params.get('maxPairAttemptsPerConnection') or 12,searchHeuristicWeight=params.get('searchHeuristicWeight') or 1.,
            **{k:params[k] for k in ('nets','grid','maxConnections','viaCost','maxExpansions')})
        if search_area:common['searchBounds']=search_area
        if copper_rework:common['localCopperRework']=copper_rework
        if contact_rework:common['localContactRework']=contact_rework
        if local:
            poses=self.worker(dict(action='local_candidates',localPlacement=local_request,**common),folder/'local-candidates',check_cancel,20)
            explicit_rework=copper_rework or contact_rework
            common['qualityScopeIds']=[r['objectId'] for r in removals]+(explicit_rework['sourceObjectIds'] if explicit_rework else [])
            common['searchBounds']=local_search_bounds(basis['outline'],local['area'],
                [t for t in before['tracks'] if t['id'] in set(common['qualityScopeIds'])],params['grid'],
                [m['at'] for row in poses['candidates'] for m in row.get('moves',[])]+
                ([p['at']for fp in before['footprints']for p in fp['pads']if p['net']in names]if shared_node or escape_cluster else []))
            deadline=time.monotonic()+params['searchSeconds'];evaluated=[];selected_nets=set(names);source_at={f['id']:f['at'] for f in before['footprints']}
            def candidate_score(candidate,moves,replacement_base):
                groups=candidate['predictedPhysicalGroupsAfter']
                preserved=all(any(g['net']==old['net'] and set(old['pads'])<=set(g['pads']) for g in groups) for old in before['physicalGroups'])
                inaccessible=candidate['layoutFeedback'].get('connectorAccess',{}).get('failedCount',0)
                unfinished=sum(max(0,sum(g['net']==net for g in groups)-1) for net in selected_nets)
                measure=dict(replacement_base)
                for op in candidate['operations']:
                    if op['kind']=='add_via':measure['viaCount']+=1
                    elif op['kind']=='add_track':measure['lengthMm']+=sum(math.dist(a,b) for a,b in zip(op['points'],op['points'][1:]))
                quality=candidate['comparisonReadability'] or candidate['readability']['after'];movement=sum(math.dist(source_at[m['objectId']],m['at']) for m in moves)
                if contact_rework:
                    movement+=sum(math.dist(contact_rework['sourceVia']['at'],op['at']) for op in candidate['operations'] if op['kind']=='move_via')
                # Completion and via count stay hard ordering terms.  Among
                # otherwise usable candidates, reuse the router's established
                # readability order before total copper length; these are
                # ranking signals, not extra per-candidate acceptance gates.
                return dict(key=[not preserved,unfinished,inaccessible,measure['viaCount'],quality['backtrackCount'],quality['shortClusterCount'],quality['bendCount'],round(measure['lengthMm'],6),round(movement,6)],sourceGroupsPreserved=preserved,inaccessibleConnectorCount=inaccessible,
                    selectedNetUnfinishedComponentCount=unfinished,boardCopperLengthMm=round(measure['lengthMm'],6),boardViaCount=measure['viaCount'],
                    newRouteLengthMm=round(candidate['readability']['after']['lengthMm'],6),newRouteBendCount=candidate['readability']['after']['bendCount'],
                    comparisonReadability=quality,moveDistanceMm=round(movement,6))
            for row in poses['candidates']:
                if row['status']!='viable':continue
                remaining=deadline-time.monotonic()
                if remaining<1:break
                seconds=max(1,min(local['candidateSearchSeconds'],math.floor(remaining)))
                active=local_rework_for_pose(removals,local_request['copperOwners'],local_request['linkCopperIds'],local_request['sourcePoses'],row['moves'],copper_rework,row.get('preCopperOperations',[]),contact_rework)
                current=self.worker(dict(action='route_plan',preMoves=row['moves'],searchSeconds=seconds,
                    **({'preCopperOperations':row['preCopperOperations']} if row.get('preCopperOperations') else {}),**{**common,'removeCopper':active}),
                    folder/f"candidate-{row['index']}",check_cancel,seconds+15,update)
                score=candidate_score(current,row['moves'],row['replacementBase']);summary={**row,'status':'evaluated','score':score,
                    'removedCopperCount':len(active),
                    'plannedConnectionCount':current['plannedConnectionCount'],'completedNetCount':current['completedNetCount'],'budgetExhausted':current['budgetExhausted']}
                evaluated.append((tuple(score['key']),row['index'],current,row['moves'],score,summary))
                phase(f"局部布局/布线候选 {len(evaluated)}/{len([r for r in poses['candidates'] if r['status']=='viable'])} · {local['linkNet']}")
            if not evaluated:
                prefix=[];reworked=[]
                plan=dict(strategy='bounded-series-pair-placement-routing-v1',operations=[],connections=[],physicalGroupsBefore=before['physicalGroups'],predictedPhysicalGroupsAfter=before['physicalGroups'],
                    attempts=[],completedNetCount=0,plannedConnectionCount=0,readability=dict(strategy='none',pathCount=0,improvedPathCount=0,maxAddedLengthMm=0,
                    before=dict(lengthMm=0,bendCount=0,backtrackCount=0,shortClusterCount=0),after=dict(lengthMm=0,bendCount=0,backtrackCount=0,shortClusterCount=0),paths=[],scope='No evaluated candidate.'),
                    layoutFeedback=dict(enforced=False,footprints=[],unresolvedConnections=[],unfinishedNets=[]),routingStyle=params.get('routingStyle'),
                    searchHeuristicWeight=params.get('searchHeuristicWeight')or 1.,maxPairAttemptsPerConnection=params.get('maxPairAttemptsPerConnection')or 12,
                    elapsedSeconds=round(params['searchSeconds']-max(0,deadline-time.monotonic()),3),budgetExhausted=True,manufacturingApproved=False,
                    localPlacement={**poses,'selectedCandidate':None,'selectedMoved':False,
                        'sourceComparison':dict(preferred=False,reason='source_retained_no_better_candidate',cause='no_evaluated_candidate')},
                    scope='No local candidate finished inside the declared search budget; source board is unchanged.')
            else:
                evaluated.sort(key=lambda item:(item[0],item[1]))
                fixed=next((item for item in evaluated if not item[3]),None)
                source_score=dict(selectedNetUnfinishedComponentCount=sum(max(0,sum(g['net']==net for g in before['physicalGroups'])-1) for net in selected_nets),
                    inaccessibleConnectorCount=before['layoutFeedback'].get('connectorAccess',{}).get('failedCount',0),
                    boardCopperLengthMm=poses['source']['lengthMm'],boardViaCount=poses['source']['viaCount'],moveDistanceMm=0)
                from .local_rework import comparison_quality
                source_score['comparisonReadability']=comparison_quality(before['footprints'],before['tracks'],[t['id'] for t in before['tracks']],common['qualityScopeIds'])
                for item in evaluated:item[5]['sourceComparison']=local_source_comparison(source_score,item[4],local['minCopperImprovementMm'],params.get('routingStyle'))
                choice=next((item for item in evaluated if item[5]['sourceComparison']['preferred']),None)
                if choice:
                    _,selected_index,plan,moves,score,summary=choice;comparison=summary['sourceComparison']
                    plan['operations']=moves+plan['operations']
                    active=local_rework_for_pose(removals,local_request['copperOwners'],local_request['linkCopperIds'],local_request['sourcePoses'],moves,copper_rework,summary.get('preCopperOperations',[]),contact_rework)
                    active_ids={r['objectId'] for r in active}
                    prefix=[op for op in prefix if op['objectId'] in active_ids]
                    reworked=[{**r,'removedObjectIds':[id for id in r['removedObjectIds'] if id in active_ids]} for r in reworked]
                    reworked=[r for r in reworked if r['removedObjectIds']]
                    if summary.get('preCopperOperations'):
                        explicit_rework=copper_rework or contact_rework
                        prefix+=explicit_rework['removals']
                        reworked.append(dict(net=explicit_rework['net'],reason=explicit_rework['reason'],removedObjectIds=[r['objectId'] for r in explicit_rework['removals']]))
                        plan['preCopperOperations']=summary['preCopperOperations']
                        if copper_rework:plan['localCopperRework']=copper_rework
                        if contact_rework:plan['localContactRework']=contact_rework
                else:
                    _,_,plan,_,_,_=evaluated[0];selected_index=None;moves=[];score=source_score
                    comparison=dict(preferred=False,reason='source_retained_no_better_candidate')
                    plan=local_retained_plan(plan,before,selected_nets)
                    prefix=[];reworked=[]
                plan['physicalGroupsBefore']=before['physicalGroups']
                summaries=[r for r in poses['candidates'] if r['status']!='viable']+[item[5] for item in sorted(evaluated,key=lambda item:item[1])]
                placement_policy='bounded-escape-cluster-placement-routing-v1' if escape_cluster else 'bounded-shared-node-placement-routing-v1' if shared_node else 'bounded-series-pair-placement-routing-v1'
                plan['strategy']=placement_policy+'+'+plan['strategy']
                plan['localPlacement']=dict(policy=placement_policy,references=refs,linkNet=local['linkNet'],
                    linkTopology=local.get('linkTopology','series-pair'),
                    **({'nodeLink':pair}if shared_node else {}),
                    companionReferences=local.get('companionReferences',[]),
                    selectedCandidate=selected_index,selectedMoved=bool(moves),source=poses['source'],selectedScore=score,
                    sourceScore=source_score,sourceComparison=comparison,
                    searchBounds=common['searchBounds'],
                    fixedScore=fixed[4] if fixed else None,candidates=summaries,candidateValidation=poses['candidateValidation'],
                    candidateOrdering=poses['candidateOrdering'],
                    finalValidation='Only the selected candidate continues to the existing native save, DRC, connectivity and adoption checks.',
                    scope=('Explicit bounded footprint poses with all affected source connections retained' if escape_cluster else 'One explicitly declared pair on a shared node with all external terminals retained'if shared_node else'One declared simple series pair in the area')+' and one optional explicit companion; no global placement, SI/PI or manufacturing proof.')
                plan['budgetExhausted']=time.monotonic()>=deadline
                plan['scope']=('Bounded local '+('escape-cluster' if escape_cluster else 'shared-node' if shared_node else 'series-pair')+' placement plus selected ordinary-net routing proposal; one final native adoption check is still required.' if choice else
                    'The untouched source remains preferable under the declared local search budget and improvement setting; no candidate or new engineering acceptance was created.')
        else:
            plan=self.worker(dict(action='route_plan',searchSeconds=params['searchSeconds'],**common),folder/'engine',check_cancel,params['searchSeconds']+15,update)
        if plan['physicalGroupsBefore']!=before['physicalGroups']:fail('普通规划的源物理分组与拆除前输入不一致。')
        if reworked:
            plan['copperReplacement']=dict(nets=reworked,removedObjectIds=[o['objectId'] for o in prefix])
            if plan['operations']:plan['operations']=prefix+plan['operations']
        if len(plan['operations'])>500:fail('本次规划超过500项铜操作上限，需缩小批次。')
        plan.update(revisionId=parent['id'],boardSha256=before['boardSha256'],request={k:v for k,v in params.items() if k!='requestId'})
        plan['sourceCopperIds']=sorted(t['id'] for t in before['tracks'])
        feedback=plan['layoutFeedback'];feedback.update(revisionId=parent['id'],boardSha256=before['boardSha256'])
        from .pcb_planning import route_followup
        feedback['nextActions']=route_followup(before,plan,params['nets'])
        footprints={f['id']:f for f in before['footprints']}
        for row in feedback['footprints']:row['footprintSha256']=footprints[row['footprintId']]['sha256']
        verify_snapshot(original,parent['manifest']);return plan

    def pair_plan(self,parent,job,folder,check_cancel,phase,read_snapshot=None):
        original,basis=self.edit_basis(parent,job);params=job['params'];req=params['pair'];rules=basis['rules']
        if req['width']<rules['trackWidth'] or req['clearance']<rules['clearance'] or req['gap']<req['clearance']:
            fail('成组线宽/净距/线间隙低于板规则或指定净距。')
        if req['positive']['net']==req['negative']['net']:fail('成组两网络不能相同。')
        before=(read_snapshot or self.read)(parent,folder/'before',check_cancel)
        if basis.get('copperIntentPath') and not copper_matches(self.copper_intent(parent,verified=True),before):fail('基线铜对象已偏离意图。')
        prefix=pair_replacement(basis,before,req,params.get('replaceSection'))
        private=folder/'workspace';private.mkdir(parents=True);path=private/Path(parent['entrypoints']['board']).name
        shutil.copyfile(within(original,parent['entrypoints']['board']),path)
        plan=self.worker(dict(action='pair_plan',board=str(path),outline=basis['outline'],rules=rules,pair=req,removeTrackIds=[o['objectId'] for o in prefix if o['kind']=='remove_track'],preTracks=[o for o in prefix if o['kind']=='add_track'],searchSeconds=params['searchSeconds']),
            folder/'engine',check_cancel,params['searchSeconds']+15,lambda data:phase('检查成组公共路径、扇出及原生障碍 · '+req['name']))
        if plan['physicalGroupsBefore']!=before['physicalGroups']:fail('成组规划的源物理分量与输入快照不一致。')
        if prefix and plan['operations']:
            if len(plan['operations'])!=2 or any(o['kind']!='add_track' for o in plan['operations']):fail('成组重布规划未返回预期的两条新路径。')
            old=next(c for c in basis['pairedSections'] if c['request']['name']==req['name'])
            if old['request']==json.loads(json.dumps(req)):
                from .routing_rules import on_segment
                def canonical(points):
                    result=[]
                    for p in points:
                        p=[round(v,6)for v in p]
                        if result and p==result[-1]:continue
                        while len(result)>1 and on_segment(result[-1],result[-2],p):result.pop()
                        result.append(p)
                    return result
                old_paths=[canonical([r['segments'][0]['start']]+[s['end']for s in r['segments']])for r in old['rails']]
                new_paths=[canonical(r['points'])for r in plan['pairContract']['rails']]
                if old_paths==new_paths:fail('重布请求与原生扇出几何均未变化，无需生成相同版本。')
            for i,rail in enumerate(plan['pairContract']['rails']):rail['operationIndex']=len(prefix)+i
            plan['operations']=prefix+plan['operations']
            plan['replacement']=dict(name=req['name'],**params['replaceSection'],removedTrackIds=[o['objectId'] for o in prefix if o['kind']=='remove_track'])
        if len(plan['operations'])>500:fail('成组重布超过500操作上限。')
        plan.update(revisionId=parent['id'],boardSha256=before['boardSha256'],request={k:v for k,v in params.items() if k!='requestId'})
        verify_snapshot(original,parent['manifest']);return plan

    def read_planning(self,revision,folder):
        """Use native pin/placement facts without unrelated engineering audits."""
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        relative=revision['entrypoints'].get('board')
        if not relative:fail('此版本没有 PCB 入口。')
        path=within(source,relative)
        expected=next(r['sha256']for r in revision['manifest']if within(source,r['path'])==path)
        folder.mkdir(parents=True,exist_ok=True);private=folder/path.name;shutil.copyfile(path,private)
        if digest(private)!=expected:raise DomainError('input_changed','PCB规划读取副本与版本清单不一致。',409)
        snapshot=self.worker(dict(action='planning_read',board=str(private)),folder/'engine',lambda:None)
        mechanical={f['id']for f in (revision.get('pcbDesign')or{}).get('mountingFeatures',[])}
        for fp in snapshot['footprints']:
            if fp['id']in mechanical:fp['mechanicalFeature']=True
        snapshot.update(revisionId=revision['id'],boardSha256=expected)
        verify_snapshot(source,revision['manifest']);return snapshot

    def read(self,revision,folder=None,check_cancel=lambda:None):
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        relative=revision['entrypoints'].get('board')
        if not relative:fail('此版本没有 PCB 入口。')
        path=within(source,relative)
        if path.stat().st_size>24*1024*1024:fail('首版 PCB 编辑上限为24MB。')
        expected_hash=next((r['sha256'] for r in revision['manifest'] if within(source,r['path'])==path),None)
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected_hash:
            raise DomainError('input_changed','PCB 解析字节与版本清单不一致。',409)
        # Preserve read_text's universal-newline object hashes while validating raw bytes.
        tree=parse(raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n'))
        if tree.kind!='kicad_pcb':fail('输入不是原生PCB。')
        folder=folder or self.config.local/'pcb-read'/uid('read')
        folder.mkdir(parents=True,exist_ok=True)
        private=folder/path.name;shutil.copyfile(path,private)
        if digest(private)!=expected_hash:
            raise DomainError('input_changed','PCB 原生读取副本与版本清单不一致。',409)
        snapshot=self.worker(dict(action='read',board=str(private)),folder/'engine',check_cancel)
        features=pcb_mounting.verify(tree,revision.get('pcbDesign') or {},source)
        ids={f['id'] for f in features}
        for fp in snapshot['footprints']:
            if fp['id'] in ids:fp['mechanicalFeature']=True
        snapshot['mountingFeatures']=features
        from .mounting_review import review as mounting_review
        snapshot['layoutFeedback']['mountingReview']=mounting_review(tree,features)
        from .mounting_plan import proof as mounting_proof
        basis=revision.get('pcbDesign') or {}
        snapshot['layoutFeedback']['mountingPlan']=mounting_proof(snapshot,basis.get('outline',{}),basis.get('rules',{}),basis.get('mountingPlan'))
        nodes={str(n.one('uuid').values[1]):n for kind in ('footprint','segment','via','arc','zone','gr_text') for n in tree.children(kind)}
        objects=[]
        for obj in snapshot['footprints']+snapshot['tracks']+snapshot.get('zones',[])+snapshot.get('boardTexts',[]):
            node=nodes.get(obj['id'])
            if node is None:fail('原生对象 UUID 与文件内容不一致。')
            obj['sha256']=hashlib.sha256(node.raw.encode('utf8')).hexdigest()
            obj.setdefault('type','footprint');objects.append(obj)
        objects.extend(pcb_silkscreen.add_native_evidence(tree,snapshot))
        snapshot.update(revisionId=revision['id'],board=relative,boardSha256=expected_hash,objects=objects,
                        padGraphHash=pad_graph(snapshot)['hash'])
        snapshot['layoutFeedback'].update(revisionId=revision['id'],boardSha256=snapshot['boardSha256'])
        from .routing_rules import power_feed_report
        snapshot['powerFeeds']=power_feed_report(snapshot,(revision.get('pcbDesign') or {}).get('powerFeeds',[]))
        from .pcb_interface_legends import report as legend_report
        snapshot['layoutFeedback']['interfaceLegends']=legend_report(snapshot,(revision.get('pcbDesign') or {}).get('interfaceLegends',[]))
        verify_snapshot(source,revision['manifest']);return snapshot

    def reference_check(self,revision,params,folder,check_cancel):
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        if not revision['entrypoints'].get('board'):fail('此版本没有PCB。')
        folder.mkdir(parents=True);path=folder/Path(revision['entrypoints']['board']).name
        shutil.copyfile(within(source,revision['entrypoints']['board']),path)
        result=self.worker({**params,'action':'reference_check','board':str(path)},folder/'engine',check_cancel,params['timeoutSeconds'])
        result.update(revisionId=revision['id'],boardSha256=digest(path));verify_snapshot(source,revision['manifest']);return result

    def mutate_native(self,source,relative,params,folder,check_cancel,timeout):
        # SaveBoard also writes adjacent project settings. Keep those native side
        # effects private: only the explicitly requested board returns to source.
        private=folder/'workspace';private.mkdir(parents=True)
        board=within(source,relative);working=private/board.name
        if board.is_file():shutil.copyfile(board,working)
        project=board.with_suffix('.kicad_pro')
        if project.is_file():shutil.copyfile(project,working.with_suffix('.kicad_pro'))
        result=self.worker({**params,'board':str(working)},folder/'engine',check_cancel,timeout)
        check_cancel();shutil.copyfile(working,board);return result

    def candidate(self,parent,job,output,handoff_job,handoff_root,check_cancel,board_base=None,baseline_out=None,read_snapshot=None):
        params=job['params'];initializing=job['kind']=='pcb.initialize'
        # Persist effective defaults alongside the native arcs, including for
        # direct business callers. Keep the original submitted job immutable.
        from ..schemas import BoardOutline,PcbMountingPlan
        if initializing:params={**params,'outline':BoardOutline.model_validate(params['outline']).model_dump(),
                                'mountingPlan':PcbMountingPlan.model_validate(params.get('mountingPlan') or {}).model_dump()}
        elif any(o['kind']=='set_outline' for o in params.get('operations',[])):
            params={**params,'operations':[{**o,'outline':BoardOutline.model_validate(o['outline']).model_dump()} if o['kind']=='set_outline' else o for o in params['operations']]}
        if params.get('mountingPlan') is not None:params={**params,'mountingPlan':PcbMountingPlan.model_validate(params['mountingPlan']).model_dump()}
        cleanup=None
        original=within(self.config.data,parent['relativePath'])
        revision_id=uid('r');source=self.config.data/'projects'/parent['projectId']/'revisions'/revision_id/'source'
        shutil.copytree(original,source);verify_snapshot(source,parent['manifest'])
        if initializing:
            replaced=initialization_replacement(parent,params,original)
            if not handoff_job or handoff_job['status']!='completed' or handoff_job['kind']!='pcb.handoff' or not (handoff_job.get('result') or {}).get('checksPassed'):
                fail('创建 PCB 需要已通过的连接交接任务。')
            hand=handoff_job['result']
            if handoff_job['projectId']!=parent['projectId'] or hand['revisionId']!=parent['id'] or handoff_job['contextSnapshot']['version']!=job['contextSnapshot']['version']:
                raise DomainError('version_conflict','PCB 交接与当前工程版本不一致。',409)
            verify_snapshot(handoff_root,hand['manifest'])
            expected=read_graph(handoff_root/'netlist.xml')
            if expected['hash']!=hand['graphHash']:fail('交接网表摘要不一致。')
            refs={c['reference'] for c in expected['components']}
            placements=params['placements']
            if len({p['reference'] for p in placements})!=len(placements) or {p['reference'] for p in placements}!=refs:
                fail('初始位置必须恰好覆盖交接中的全部器件一次。')
            if params['rules']['viaDrill']>=params['rules']['viaDiameter']:fail('过孔孔径必须小于外径。')
            footprints=install_handoff_footprints(source,handoff_root,hand['mapping'],revision_id if replaced else None)
            board_relative=str(Path(parent['entrypoints']['schematic']).with_suffix('.kicad_pcb')).replace('\\','/')
            project=within(source,str(Path(board_relative).with_suffix('.kicad_pro')))
            settings=json.loads(project.read_text(encoding='utf8'))
            rules=params['rules']
            settings.setdefault('board',{}).setdefault('design_settings',{}).setdefault('rules',{}).update(
                min_clearance=rules['clearance'],min_track_width=rules['trackWidth'],min_via_diameter=rules['viaDiameter'],
                min_through_hole_diameter=rules['viaDrill'],min_copper_edge_clearance=rules['copperEdge'],min_hole_clearance=rules['holeClearance'])
            classes=settings.setdefault('net_settings',{}).setdefault('classes',[])
            default=next((c for c in classes if c.get('name')=='Default'),None)
            if default is None:default=dict(name='Default');classes.append(default)
            default.setdefault('wire_width',6)  # Native schematic default, in mil; preserve custom widths.
            default.update(clearance=rules['clearance'],track_width=rules['trackWidth'],via_diameter=rules['viaDiameter'],via_drill=rules['viaDrill'])
            from ..pcb_rule_settings import apply_native_vias
            apply_native_vias(settings,rules['viaDiameter'],rules['viaDrill'])
            project.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf8')
            components=[]
            for c in ET.parse(handoff_root/'netlist.xml').findall('components/comp'):
                components.append(dict(reference=c.attrib['ref'],value=c.findtext('value'),footprint=c.findtext('footprint'),
                    path=c.find('sheetpath').attrib['tstamps']+c.findtext('tstamps'),sheetname=c.find('sheetpath').attrib['names'],sheetfile=parent['entrypoints']['schematic'],
                    fields={f.attrib['name']:f.text or '' for f in c.findall('fields/field')}))
            named=pcb_netlist.named_graph(expected,handoff_root/'netlist.net')
            self.mutate_native(source,board_relative,dict(action='initialize',thickness=params['thickness'],outline=params['outline'],
                footprintRoot=str(footprints),netlist=named,components=components,placements=placements),output/'native-create',check_cancel,params['timeoutSeconds'])
            expected_copper=[]
            basis=dict(schemaVersion=1,schematicRevisionId=parent['id'],schematicBinding=schematic_binding(original,parent['manifest']),
                contextBinding=context_binding(job),handoffJobId=handoff_job['id'],reviewJobId=hand['reviewJobId'],expectedGraph=expected,
                outline=params['outline'],rules=rules,thickness=params['thickness'],footprintSource=hand.get('footprintSource'),
                manufacturingApproved=False,hardwareVerified=False)
            changes=[dict(kind='initialize_pcb',before=None,after=dict(entrypoint=board_relative,componentCount=len(refs),outline=params['outline'],placements=placements))]
            if replaced:
                basis['reinitializedFrom']=replaced
                changes[0]['before']=replaced
        elif job['kind']=='pcb.sync':
            if not board_base or not board_base.get('pcbDesign'):fail('同步需要本服务创建的明确板基线。')
            base_source=within(self.config.data,board_base['relativePath']);verify_snapshot(base_source,board_base['manifest'])
            board_relative=parent['entrypoints'].get('board')
            if not board_relative or board_base['entrypoints'].get('board')!=board_relative:fail('同步的PCB入口必须保持。')
            if digest(within(original,board_relative))!=params['expectedBoardHash'] or digest(within(base_source,board_relative))!=params['expectedBoardHash']:
                raise DomainError('version_conflict','同步板基线与当前版本的板内容不一致。',409)
            if not handoff_job or handoff_job['projectId']!=parent['projectId'] or handoff_job['kind']!='pcb.handoff' or handoff_job['status']!='completed' or not (handoff_job.get('result') or {}).get('checksPassed'):
                fail('同步需要重新审查并通过的当前原理图交接。')
            hand=handoff_job['result']
            if hand['revisionId']!=parent['id'] or handoff_job['contextSnapshot']['version']!=job['contextSnapshot']['version']:
                raise DomainError('version_conflict','新交接与当前原理图/上下文不一致。',409)
            verify_snapshot(handoff_root,hand['manifest']);expected=read_graph(handoff_root/'netlist.xml')
            if expected['hash']!=hand['graphHash']:fail('新交接网表摘要不一致。')
            before=(read_snapshot or self.read)(board_base,output/'before-sync',check_cancel)
            if pad_graph(before)['hash']!=board_base['pcbDesign']['expectedGraph']['hash']:fail('旧板焊盘网络已偏离其原理图基础。')
            if not copper_matches(self.copper_intent(board_base,verified=True),before):fail('旧板铜对象已偏离其保存意图。')
            footprints={f['reference']:f for f in before['footprints'] if not f.get('mechanicalFeature')}
            refs={m['reference'] for m in hand['mapping']}
            if not set(footprints)<=refs:fail('同步不支持删除器件。')
            added=refs-set(footprints);placements=params.get('addedPlacements',[])
            if len({p['reference'] for p in placements})!=len(placements) or {p['reference'] for p in placements}!=added:fail('新增位置必须恰好覆盖全部新增器件一次，不能移动旧器件。')
            replacements=pcb_replacement.validate(before,hand['mapping'],params.get('footprintReplacements',[]),handoff_root)
            base_table=parse((base_source/'fp-lib-table').read_text(encoding='utf-8-sig'))
            old_libraries={n.one('name').values[1]:n.one('uri').values[1] for n in base_table.children('lib')}
            table_path=source/'fp-lib-table';table=parse(table_path.read_text(encoding='utf-8-sig'))
            current_libraries={n.one('name').values[1]:n.one('uri').values[1] for n in table.children('lib')}
            declarations=[]
            for m in hand['mapping']:
                lib,name=m['footprint'].split(':');frozen=source/'formforge-footprints'/(lib+'.pretty')/(name+'.kicad_mod')
                if m['reference'] in footprints and footprints[m['reference']]['footprint']==m['footprint']:
                    uri=old_libraries.get(lib,'')
                    if not uri.startswith('${KIPRJMOD}/'):fail('旧封装必须绑定工程内冻结库。')
                    active=within(base_source,uri[len('${KIPRJMOD}/'):]+'/'+name+'.kicad_mod')
                    if digest(active)!=m['sha256']:fail('旧封装定义变化，必须换新库身份并明确替换。')
                    continue
                if frozen.exists():
                    if digest(frozen)!=m['sha256']:fail('封装库版本变化，须单独迁移和复核。')
                else:
                    original_footprint=handoff_root/'footprints'/(lib+'.pretty')/(name+'.kicad_mod')
                    if digest(original_footprint)!=m['sha256']:fail('新增封装摘要与冻结交接不一致。')
                    frozen.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original_footprint,frozen)
                if lib in current_libraries:
                    uri=current_libraries[lib]
                    if not uri.startswith('${KIPRJMOD}/') or digest(within(source,uri[len('${KIPRJMOD}/'):]+'/'+name+'.kicad_mod'))!=m['sha256']:fail('当前封装库表未准确绑定新增或替换的定义。')
                else:
                    current_libraries[lib]='${KIPRJMOD}/formforge-footprints/'+lib+'.pretty'
                    declarations.append(' (lib (name '+json.dumps(lib)+') (type "KiCad") (uri '+json.dumps(current_libraries[lib])+') (options "") (descr "FormForge frozen PCB input"))\n')
            table_path.write_text(table.source[:table.end-1]+''.join(declarations)+table.source[table.end-1:],encoding='utf8')
            pcb_mounting.append_libraries(source,board_base['pcbDesign'].get('mountingFeatures',[]))
            named=pcb_netlist.named_graph(expected,handoff_root/'netlist.net')
            changed=pad_changes(before,named,params['expectedPadChanges'])
            components=[]
            for c in ET.parse(handoff_root/'netlist.xml').findall('components/comp'):
                components.append(dict(reference=c.attrib['ref'],value=c.findtext('value'),footprint=c.findtext('footprint'),path=c.find('sheetpath').attrib['tstamps']+c.findtext('tstamps'),
                    sheetname=c.find('sheetpath').attrib['names'],sheetfile=parent['entrypoints']['schematic'],fields={f.attrib['name']:f.text or '' for f in c.findall('fields/field')}))
            self.mutate_native(source,board_relative,dict(action='sync',netlist=named,components=components,footprintRoot=str(source/'formforge-footprints'),placements=placements,replacements=replacements),output/'native-sync',check_cancel,params['timeoutSeconds'])
            expected_copper=copper_target(before,[],[]);basis=json.loads(json.dumps(board_base['pcbDesign']))
            basis.update(schematicRevisionId=parent['id'],schematicBinding=schematic_binding(original,parent['manifest']),contextBinding=context_binding(job),
                handoffJobId=handoff_job['id'],reviewJobId=hand['reviewJobId'],expectedGraph=expected,footprintSource=hand.get('footprintSource'),sourceBoardRevisionId=board_base['id'])
            basis.pop('routePlanPath',None);basis.pop('routePlanHash',None)
            basis.setdefault('footprintReplacementHistory',[]).extend(dict(**r,sourceBoardRevisionId=board_base['id'],sourceSchematicRevisionId=parent['id'],handoffJobId=handoff_job['id']) for r in replacements)
            changes=[dict(kind='sync_pcb',boardBaseRevisionId=board_base['id'],padChanges=changed,addedComponents=placements,footprintReplacements=replacements,sourceSchematicRevisionId=parent['id'])]
            verify_snapshot(base_source,board_base['manifest'])
        else:
            _,basis=self.edit_basis(parent,job,verified=True)
            basis=release_sections(basis,params['operations'],parent['id'])
            board_relative=parent['entrypoints']['board']
            before=(read_snapshot or self.read)(parent,output/'before',check_cancel)
            if basis.get('copperIntentPath') and not copper_matches(self.copper_intent(parent,verified=True),before):
                fail('PCB 基线铜对象已偏离保存的网络意图。')
            if any(op['kind']=='cleanup_tracks' for op in params['operations']):
                if len(params['operations'])!=1:fail('自动冗余铜清理须单独成批。')
                from .pcb_cleanup import plan,locked_ids
                cleanup=plan(before,basis,params['operations'][0]['nets'],locked_ids(within(original,board_relative)),check_cancel)
                params={**params,'operations':cleanup['operations']}
            validate_operations(before,params['operations'],basis['rules'])
            numeric=next((o for o in params['operations'] if o['kind']=='set_board_rules'),None)
            if numeric:
                from ..pcb_rule_settings import resolve, apply_native
                basis=json.loads(json.dumps(basis));basis['rules']=resolve({},numeric['rules'])
                basis['rulesSource']={'kind':'project-override','reason':numeric['reason'],'jobId':job['id']}
                project=within(source,str(Path(board_relative).with_suffix('.kicad_pro')))
                settings=json.loads(project.read_text(encoding='utf8'))
                apply_native(settings,basis['rules'])
                project.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf8')
            via_rule=next((o for o in params['operations']if o['kind']=='set_via_rules'),None)
            if via_rule:
                basis=json.loads(json.dumps(basis));basis['rules'].update(viaDiameter=via_rule['diameter'],viaDrill=via_rule['drill'])
                project=within(source,str(Path(board_relative).with_suffix('.kicad_pro')))
                settings=json.loads(project.read_text(encoding='utf8'))
                from ..pcb_rule_settings import apply_native_vias
                apply_native_vias(settings,via_rule['diameter'],via_rule['drill'])
                project.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf8')
            expected=pad_graph(before)
            contract=basis.get('nativeSourcePadGraph') if basis.get('acceptanceScope')=='native-source-preserving' else basis.get('expectedGraph')
            if not contract or expected['hash']!=contract['hash']:fail('基线焊盘网络已偏离保存的连接契约。')
            operations=json.loads(json.dumps(params['operations']));additions={}
            features={f['id']:f for f in basis.get('mountingFeatures',[])}
            for index,op in enumerate(operations):
                if op['kind']=='add_mounting_hole':
                    frozen=pcb_mounting.freeze(source,op['source'],op['drill'])
                    additions[index]={**frozen,'reference':op['reference'],'at':op['at'],'rotation':0}
                    op.update(frozenFootprint=frozen['footprint'],footprintRoot=str(source/'formforge-footprints'))
                if op['kind']=='remove_mounting_hole':features.pop(op['objectId'])
                if op['kind']=='move_footprint' and op['objectId'] in features:features[op['objectId']]={**features[op['objectId']],'at':op['at'],'rotation':op['rotation']}
            pcb_mounting.append_libraries(source,[*features.values(),*additions.values()])
            native=(self.mutate_native(source,board_relative,dict(action='edit',operations=operations),output/'native-edit',check_cancel,params['timeoutSeconds'])
                    if operations or cleanup is None else dict(createdObjects=[],createdMechanicalFeatures=[]))
            variants=derive_footprints(source,params['operations'],before,{**basis,'board':board_relative},self.worker,output/'derived-footprints',check_cancel)
            if variants:
                basis['footprintVariants']=basis.get('footprintVariants',[])+variants
            expected_copper=copper_target(before,params['operations'],native['createdObjects'])
            basis=json.loads(json.dumps(basis))
            records=native['createdMechanicalFeatures']
            if {r['operation'] for r in records}!=set(additions) or len(records)!=len(additions):fail('原生机械孔创建数量与目标不同。')
            for r in records:
                if r['id'] in features:fail('新增机械孔UUID重复。')
                features[r['id']]={**additions[r['operation']],**{k:r[k] for k in ['id','padId']}}
            if features or 'mountingFeatures' in basis:basis['mountingFeatures']=list(features.values())
            for op in params['operations']:
                if op['kind']=='set_outline':basis['outline']=op['outline']
                if op['kind']=='set_zone_rules':basis['rules']={**basis['rules'],'zoneMinimumThickness':op['minimumThickness'],'zoneThermalSpoke':op['thermalSpoke']}
                if op['kind']=='set_stackup':
                    profile=json.loads(json.dumps(op['profile']));record=profile['source'];path=Path(record['path']).expanduser().resolve()
                    if not path.is_file() or path.stat().st_size>8*1024*1024 or digest(path)!=record['sha256']:fail('叠层来源不存在、超过8MB或摘要不符。')
                    relative='formforge-stackup-source/'+record['sha256']+path.suffix
                    target=within(source,relative);target.parent.mkdir(exist_ok=True);shutil.copyfile(path,target)
                    if digest(target)!=record['sha256'] or digest(path)!=record['sha256']:fail('叠层来源在复制期间发生变化。')
                    record['path']=relative;basis.update(stackup=profile,thickness=profile['nominalThickness'])
            changes=[dict(**op) for op in params['operations']]
            if cleanup:changes.append({k:v for k,v in cleanup.items() if k!='operations'})
        if baseline_out is not None and not initializing:baseline_out.update(before)
        if params.get('_trackOptimization'):
            basis['trackOptimization']=params['_trackOptimization']
            changes.append(dict(kind='existing_track_optimization',plan=params['_trackOptimization']))
        if params.get('_silkscreenPlan'):
            basis['silkscreenPlacement']=params['_silkscreenPlan']
            changes.append(dict(kind='automatic_silkscreen_placement',plan=params['_silkscreenPlan']))
        if params.get('powerFeeds') is not None:
            basis=json.loads(json.dumps(basis));basis['powerFeeds']=params['powerFeeds']
            changes.append(dict(kind='set_power_feeds',rules=params['powerFeeds']))
        if params.get('interfaceLegends') is not None:
            basis=json.loads(json.dumps(basis));basis['interfaceLegends']=params['interfaceLegends']
            changes.append(dict(kind='set_interface_legends',rules=params['interfaceLegends']))
        if params.get('mountingPlan') is not None:
            from .mounting_plan import plan as plan_mounts
            from .board_outline import read_native as read_outline
            current=self.worker(dict(action='read',board=str(within(source,board_relative))),output/'mounting-input',check_cancel)
            current['mountingFeatures']=basis.get('mountingFeatures',[])
            native_outline=read_outline(parse(within(source,board_relative).read_text(encoding='utf-8-sig')))
            mounting=plan_mounts(current,native_outline,basis['rules'],params['mountingPlan'],self.config)
            additions={};mount_ops=json.loads(json.dumps(mounting.pop('operations')))
            for index,op in enumerate(mount_ops):
                frozen=pcb_mounting.freeze(source,op['source'],op['drill'])
                additions[index]={**frozen,'reference':op['reference'],'at':op['at'],'rotation':0}
                op.update(frozenFootprint=frozen['footprint'],footprintRoot=str(source/'formforge-footprints'))
            pcb_mounting.append_libraries(source,[*basis.get('mountingFeatures',[]),*additions.values()])
            if mount_ops:
                generated=self.mutate_native(source,board_relative,dict(action='edit',operations=mount_ops),output/'mounting-create',check_cancel,params['timeoutSeconds'])
                records=generated['createdMechanicalFeatures']
                if len(records)!=len(additions) or {r['operation'] for r in records}!=set(additions):fail('自动固定孔原生数量与计划不符。')
                basis.setdefault('mountingFeatures',[]).extend({**additions[r['operation']],**{k:r[k] for k in ('id','padId')}} for r in records)
            mounting['holeIds']=[f['id'] for f in basis.get('mountingFeatures',[]) if any(math.dist(f['at'],p)<1e-6 for p in mounting['positions'])]
            mounting.update(sourceRevisionId=parent['id'])
            basis['mountingPlan']=mounting
            changes.append(dict(kind='automatic_mounting_plan',plan=mounting,operations=[{k:v for k,v in op.items() if k not in ('footprintRoot','frozenFootprint')} for op in mount_ops]))
        pcb_mounting.append_libraries(source,basis.get('mountingFeatures',[]))
        restore_libraries(source,basis)
        basis['copperIntentPath']='formforge-copper-intent.json';basis['copperIntentHash']=fingerprint(expected_copper)
        (source/basis['copperIntentPath']).write_text(json.dumps(expected_copper,ensure_ascii=False,indent=2),encoding='utf8')
        paths={r['path'] for r in parent['manifest']}|{board_relative,'fp-lib-table','formforge-pcb.json',basis['copperIntentPath']}
        if basis.get('stackup'):paths.add(basis['stackup']['source']['path'])
        if params.get('_routePlan'):
            plan=params['_routePlan'];basis['routePlanPath']='formforge-routing.json';basis['routePlanHash']=fingerprint(plan)
            (source/basis['routePlanPath']).write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8');paths.add(basis['routePlanPath'])
            if plan.get('pairContract'):
                contract=json.loads(json.dumps(plan['pairContract']));created={(r['operation'],r['segment']):r['id'] for r in native['createdObjects']}
                for i,rail in enumerate(contract['rails']):
                    operation=rail.pop('operationIndex',i)
                    rail['segments']=[dict(id=created[(operation,j)],start=a,end=b) for j,(a,b) in enumerate(zip(rail['points'],rail['points'][1:]))]
                    rail.pop('points')
                basis.setdefault('pairedSections',[]).append(contract)
        if basis.get('acceptanceScope')=='native-source-preserving':
            basis['sourceBoardSha256']=digest(within(source,board_relative));basis['nativeSourcePadGraph']=expected
        (source/'formforge-pcb.json').write_text(json.dumps(basis,ensure_ascii=False,indent=2),encoding='utf8')
        paths.update(p.relative_to(source).as_posix() for p in (source/'formforge-footprints').rglob('*.kicad_mod'))
        paths.update(p.relative_to(source).as_posix() for p in (source/'formforge-footprint-variants').rglob('*.kicad_mod'))
        manifest=[dict(path=p,sha256=digest(within(source,p)),size=within(source,p).stat().st_size) for p in sorted(paths)]
        verify_snapshot(original,parent['manifest']);check_cancel()
        candidate=dict(id=revision_id,projectId=parent['projectId'],backend='kicad10',origin='pcb_initialized' if initializing else 'pcb_routed' if params.get('_routePlan') else 'pcb_edited',
            parentRevisionId=parent['id'],createdByJobId=job['id'],relativePath=source.relative_to(self.config.data).as_posix(),
            entrypoints={**parent['entrypoints'],'board':board_relative},manifest=manifest,totalBytes=sum(r['size'] for r in manifest),
            createdAt=now(),message=params['message'],changes=changes,validation=dict(status='pending'),pcbDesign=basis,
            symbolLibraries=parent.get('symbolLibraries',[]),footprintLibraries=parent.get('footprintLibraries',[]),designSpecification=parent.get('designSpecification'),functionalGroups=parent.get('functionalGroups'),
            downstreamRequiresReview=['mechanical','manufacturing'])
        return candidate,expected

    def copper_intent(self,revision,*,verified=False):
        basis=revision['pcbDesign'];source=within(self.config.data,revision['relativePath'])
        if not verified:verify_snapshot(source,revision['manifest'])
        intent=json.loads(within(source,basis['copperIntentPath']).read_text(encoding='utf8'))
        if fingerprint(intent)!=basis['copperIntentHash']:fail('铜对象意图摘要与版本记录不一致。')
        return intent


def finalize_checks(result):
    """A complete pass cannot survive a failed post-save adoption proof."""
    result['checksPassed']=bool(result['checksPassed'] and result['draftChecksPassed'])
    return result


def draft_result(inspection,expected,actual,raw_drc,outline=None,expected_copper=None,previous=None,*,native_source=False,baseline_drc=None):
    matched=expected['hash']==pad_graph(actual)['hash']
    blockers=[]
    for check in inspection['checks']:
        if check['kind']=='erc' and check['status']!='passed':blockers.append(dict(type='erc',description='原理图原生检查未通过。'))
        if not native_source and check['kind']=='drc' and not check.get('schematicParityChecked'):blockers.append(dict(type='parity_missing',description='未执行原理图/PCB一致性检查。'))
    if 'violations' not in raw_drc or (not native_source and 'schematic_parity' not in raw_drc):fail('缺少完整 DRC 或原理图一致性报告。')
    # Never use the UI's first-80-finding summary to decide adoption.
    if native_source:
        if baseline_drc is None or 'violations'not in baseline_drc:fail('原生源保持范围需要同源DRC基线。')
        blockers.extend(new_drc_errors(baseline_drc,raw_drc))
        if len(raw_drc.get('unconnected_items',[]))>len(baseline_drc.get('unconnected_items',[])):
            blockers.append(dict(type='native_unconnected_increase',severity='error',description='原生未连接项增加。'))
    else:
        blockers.extend(i for i in raw_drc['violations'] if i.get('severity')=='error' and not i.get('excluded',False))
        blockers.extend(i for i in raw_drc['schematic_parity'] if not i.get('excluded',False))
    copper_matched=expected_copper is None or copper_matches(expected_copper,actual)
    if not copper_matched:blockers.append(dict(type='copper_intent_mismatch',severity='error',description='实际铜对象的网络或几何与明确提交目标不同；不能沿用原生自动归网结果。'))
    if outline:
        from .board_outline import contains
        x,y=outline['origin'];right=x+outline['width'];bottom=y+outline['height']
        inside=lambda p:contains(outline,p)
        for footprint in actual['footprints']:
            if any(not inside(p['at']) for p in footprint['pads']):
                blockers.append(dict(type='outside_outline',severity='error',description=footprint['reference']+' 含位于板框外的焊盘。'))
        for track in actual['tracks']:
            if not all(inside(p) for p in ([track['at']] if track['type']=='via' else [track['start'],track['end']])):
                blockers.append(dict(type='outside_outline',severity='error',description='铜对象超出板框。'))
        for zone in actual.get('zones',[]):
            if zone['ruleArea'] or not zone['filled']:blockers.append(dict(type='zone_unfilled',severity='error',description='铜区未完成原生填充。'))
            # Zone control polygons can span rounded corners; native filled
            # copper is independently checked against the actual board edge.
            if any(not(x<=p[0]<=right and y<=p[1]<=bottom) for o in zone['outlines'] for p in o['outer']):blockers.append(dict(type='outside_outline',severity='error',description='铜区边界超出板框。'))
    mounting=actual.get('layoutFeedback',{}).get('mountingPlan',dict(passed=True,status='not_planned'))
    if not mounting['passed']:blockers.append(dict(type='mounting_plan_failed',severity='error',description='已保存固定规划的孔位或预留空间被破坏，须明确重新规划。'))
    corner={'mountingPlan':mounting}
    if previous is not None:
        from .connector_access import changed_failures
        inaccessible=changed_failures(previous,actual)
        corner['connectorAccessPolicy']=dict(passed=not inaccessible,findings=inaccessible)
        if inaccessible:blockers.append(dict(type='connector_access_violation',severity='error',description='连接器安装基准未对齐朝外板边；拒绝移动后朝内或埋入板内的接口。'))
        from .routing_rules import corner_proof
        from .pad_exits import proof as pad_exit_proof
        check=corner_proof(previous,actual);corner['cornerPolicy']=check
        if not check['passed']:blockers.append(dict(type='routing_corner_violation',severity='error',description='新增或重做的裸露铜线含直角/锐角；按cornerPolicy中的网络、层和坐标改为45度过渡，旧版本保持。'))
        exits=pad_exit_proof(previous,actual);corner['padExitPolicy']=exits
        if not exits['passed']:blockers.append(dict(type='pad_exit_violation',severity='error',description='新增或重做的焊盘出线须从原生中心连续直出；真实圆形允许45度，其余沿上下左右，焊盘外至少直行0.15mm再转弯；按padExitPolicy中的端子、层和坐标修正，旧版本保持。'))
    return {**inspection,**corner,'checksPassed':not native_source and inspection['checksPassed']and not blockers,'draftChecksPassed':matched and not blockers,'connectivityMatched':matched,'connectivityPreserved':matched,
                'pcbPadGraphHash':pad_graph(actual)['hash'],'copperIntentMatched':copper_matched,'blockingFindings':blockers[:80],'blockingFindingCount':len(blockers),
                **(dict(nativeSourceChecksPassed=matched and not blockers,acceptanceScope='native-source-preserving',ercChecked=False,schematicParityChecked=False)if native_source else{}),
                'manufacturingApproved':False,'scope':'原生PCB源保持草稿；未执行ERC或原理图一致性，不等同完整电气或制造验收。'if native_source else'PCB 草稿结构、精确焊盘网络与原生检查；未连单列，不等同布线或制造完成。'}
