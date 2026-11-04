"""Explicit physical-contact replacement contracts, independent of native writer."""
from .importer import digest,within
from .native_syntax import parse
from ..store import DomainError


def fail(message):raise DomainError('invalid_input',message)


def key(number,at):return number,tuple(round(float(v),6) for v in at)


def validate(before,mapping,replacements,handoff_root):
    old={f['reference']:f for f in before['footprints'] if not f.get('mechanicalFeature')}
    target={m['reference']:m for m in mapping}
    changed={r for r in old.keys()&target.keys() if old[r]['footprint']!=target[r]['footprint']}
    if len({r['reference'] for r in replacements})!=len(replacements) or {r['reference'] for r in replacements}!=changed:
        fail('封装替换声明必须完整且仅覆盖实际改变封装身份的旧器件。')
    for row in replacements:
        fp=old[row['reference']];m=target[row['reference']]
        if fp['id']!=row['objectId'] or fp['sha256']!=row['expectedHash']:
            raise DomainError('version_conflict','待替换器件UUID或对象摘要已变化。',409)
        if row['footprint']!=m['footprint'] or row['footprintSha256']!=m['sha256']:fail('替换目标与当前冻结交接的封装或SHA不一致。')
        lib,name=m['footprint'].split(':')
        # A new alias avoids changing other instances or restoring an old silk fork over it.
        if lib in {f['footprint'].split(':')[0] for f in old.values()}:fail('换封装须使用新的库别名；请先导入为独立冻结库。')
        path=within(handoff_root,'footprints/'+lib+'.pretty/'+name+'.kicad_mod')
        if digest(path)!=m['sha256']:fail('替换封装冻结字节不符。')
        tree=parse(path.read_text(encoding='utf-8-sig'))
        new_pads=[p for p in tree.children('pad') if p.values[1]]
        targets=[key(p.values[1],p.one('at').values[1:3]) for p in new_pads]
        contacts=row['padMap'];old_pads={p['id']:p for p in fp['pads'] if p['number']}
        if len(contacts)!=len(old_pads) or {p['padId'] for p in contacts}!=old_pads.keys():fail('接点映射必须恰好覆盖全部旧电气焊盘一次。')
        selected=[key(p['number'],p['targetAt']) for p in contacts]
        if len(targets)!=len(set(targets)) or len(selected)!=len(set(selected)) or set(selected)!=set(targets):
            fail('映射必须逐一覆盖全部新封装实体接点；重复针号也按局部坐标区分。')
        if any(old_pads[p['padId']]['number']!=p['number'] for p in contacts):fail('本阶段换封装不允许暗改电气针号。')
        released=row['releasedPadIds']
        if len(released)!=len(set(released)) or not set(released)<=old_pads.keys():fail('释放范围只能包含本器件明确映射的旧电气焊盘。')
    return replacements


def proof(before,after,replacements):
    old={f['id']:f for f in before['footprints']};new={f['id']:f for f in after['footprints']};rows=[]
    for op in replacements:
        fp=new.get(op['objectId']);prior=old[op['objectId']]
        valid=bool(fp) and fp['reference']==op['reference'] and fp['footprint']==op['footprint'] and fp['at']==list(op['at']) and fp['rotation']==op['rotation'] and fp['layer']==prior['layer'] and fp['texts']==prior['texts'] and fp['instanceAttributes']==prior['instanceAttributes'] and fp['locked']==prior['locked']
        pads={p['id']:p for p in fp['pads'] if p['number']} if fp else {}
        valid=valid and pads.keys()=={p['padId'] for p in op['padMap']}
        # Native inventory supplies local pad positions, including mirrored boards.
        valid=valid and all(p['padId'] in pads and key(pads[p['padId']]['number'],pads[p['padId']]['localAt'])==key(p['number'],p['targetAt']) for p in op['padMap'])
        rows.append(dict(reference=op['reference'],beforeFootprint=prior['footprint'],afterFootprint=op['footprint'],
            footprintSha256=op['footprintSha256'],passed=bool(valid),releasedPadIds=op['releasedPadIds'],reason=op['reason']))
    return dict(passed=all(r['passed'] for r in rows),replacements=rows)
