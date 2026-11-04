"""Revision-local derived footprints retain originals and native library checks."""
import hashlib
import json
from pathlib import Path
import shutil

from .importer import digest,within
from .native_syntax import parse
from .pcb_silkscreen import KINDS,editable_shapes
from ..store import DomainError


def derive(source,operations,before,basis,worker,folder,cancel):
    edits=[op for op in operations if op['kind'] in KINDS]
    if not edits:return []
    lines={g['id']:g for g in editable_shapes(before)};ids={lines[op['objectId']]['footprintId'] for op in edits}
    footprints={fp['id']:fp for fp in before['footprints']}
    selected={footprints[id]['footprint'] for id in ids}
    if any(fp['footprint'] in selected and fp['id'] not in ids for fp in footprints.values()):
        raise DomainError('unsupported_capability','同库定义有多个实例时，须一起修整为一致定义；逐实例派生料号/原图同步尚未接入。')
    table=source/'fp-lib-table';tree=parse(table.read_text(encoding='utf-8-sig'))
    libraries={n.one('name').values[1]:n for n in tree.children('lib')}
    key=hashlib.sha256(json.dumps(edits,sort_keys=True,ensure_ascii=False).encode('utf8')).hexdigest()[:16]
    destinations={};records=[];requests=[]
    for id in sorted(ids):
        cancel();fp=footprints[id];lib,name=fp['footprint'].split(':',1);entry=libraries[lib];uri=entry.one('uri').values[1]
        if not uri.startswith('${KIPRJMOD}/'):raise DomainError('invalid_input','丝印派生要求工程内已冻结的封装库。')
        old=within(source,uri.split('}/',1)[1]);base=within(old,name+'.kicad_mod')
        if not base.is_file():raise DomainError('dependency_missing','派生封装缺少冻结原始定义。')
        relative=f'formforge-footprint-variants/{key}/{lib}.pretty';target=within(source,relative)
        if lib not in destinations:shutil.copytree(old,target);destinations[lib]=relative
        requests.append(dict(footprintId=id,libraryDirectory=str(target)))
        records.append(dict(reference=fp['reference'],footprintId=id,footprint=fp['footprint'],
            basePath=base.relative_to(source).as_posix(),baseSha256=digest(base),path=relative+'/'+name+'.kicad_mod',
            reason='Revision-local silk-only derivative; original frozen definition retained',
            operations=[op for op in edits if lines[op['objectId']]['footprintId']==id]))
    # A native writer normalizes orientation/side/position to library coordinates.
    worker(dict(action='export_footprint_variants',board=str(source/basis['board']),variants=requests),folder,cancel)
    replacements=[]
    for lib,relative in destinations.items():
        node=libraries[lib].one('uri');replacements.append((node.start,node.end,'(uri '+json.dumps('${KIPRJMOD}/'+relative)+')'))
    text=tree.source
    for a,b,value in sorted(replacements,reverse=True):text=text[:a]+value+text[b:]
    table.write_text(text,encoding='utf8')
    for row in records:
        row['sha256']=digest(within(source,row['path']))
        if digest(within(source,row['basePath']))!=row['baseSha256']:raise DomainError('input_changed','原始封装在派生时改变。',409)
    return records


def restore_libraries(source,basis):
    """A schematic sync may rebuild the table; keep the explicit local forks."""
    records=basis.get('footprintVariants',[])
    if not records:return
    latest={}
    for row in records:
        if digest(within(source,row['basePath']))!=row['baseSha256'] or digest(within(source,row['path']))!=row['sha256']:
            raise DomainError('input_changed','派生封装或其冻结来源摘要改变。',409)
        latest[row['footprint'].split(':',1)[0]]=str(Path(row['path']).parent).replace('\\','/')
    path=source/'fp-lib-table';tree=parse(path.read_text(encoding='utf8'));edits=[]
    for node in tree.children('lib'):
        lib=node.one('name').values[1]
        if lib in latest:
            uri=node.one('uri');edits.append((uri.start,uri.end,'(uri '+json.dumps('${KIPRJMOD}/'+latest.pop(lib))+')'))
    if latest:raise DomainError('dependency_missing','派生封装的库别名未保留。')
    text=tree.source
    for a,b,value in sorted(edits,reverse=True):text=text[:a]+value+text[b:]
    path.write_text(text,encoding='utf8')
