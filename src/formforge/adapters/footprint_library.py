"""Freeze original footprint bytes without changing any existing library alias."""
import hashlib
import json
import re
import shutil
from pathlib import Path

from .importer import digest,reparse,within
from .native_syntax import parse
from .schematic import child_value,fail,load_pages,properties,safe_string
from ..store import DomainError,now


def import_footprint(config,source,entry,op,existing,job_id):
    if Path(entry).parent!=Path('.'):fail('首版封装导入仅支持根目录原理图，与PCB交接范围一致。')
    requested=Path(op['sourcePath']).expanduser()
    if not requested.is_absolute() or not requested.is_file() or requested.suffix.lower()!='.kicad_mod':
        fail('封装来源需要本机已有 .kicad_mod 文件绝对路径。')
    if any(reparse(p) for p in [requested,*requested.parents]):fail('封装来源不能经过符号链接或目录联接。')
    if requested.stat().st_size>8*1024*1024:fail('封装超过8MB上限。')
    raw=requested.read_bytes();actual=hashlib.sha256(raw).hexdigest()
    if actual!=op['expectedSha256']:raise DomainError('input_changed','封装字节与预期SHA不符。',409)
    tree=parse(raw.decode('utf-8-sig'))
    if tree.kind!='footprint' or len(tree.values)<2:fail('需要KiCad原生footprint定义。')
    name=tree.values[1]
    if not re.fullmatch(r'[A-Za-z0-9_+.,() -]{1,200}',name) or name in {'.','..'}:fail('封装名不支持路径、控制字符或特殊分隔符。')
    lib='FF_FP_'+actual[:16];footprint=lib+':'+name
    if any(r['id']==lib for r in existing):fail('封装已冻结，请复用；新内容须使用新摘要。')
    table_path=source/'fp-lib-table';base=[];tracked=[]
    if table_path.exists():
        table=parse(table_path.read_text(encoding='utf-8-sig'))
        if table.kind!='fp_lib_table':fail('现有封装表格式不正确。')
    else:
        # Creating a project table must not hide formerly global definitions.
        used=set()
        for page in load_pages(source,entry).values():
            for component in page['tree'].children('symbol'):
                fields=properties(component);field=fields.get('Footprint')
                if field and field.values[2]:used.add(field.values[2])
        aliases=set()
        for selected in sorted(used):
            if selected.count(':')!=1:fail('已有器件封装名缺少库名。')
            alias,item=selected.split(':')
            if any(c in alias+item for c in '/\\') or alias in {'.','..'} or item in {'.','..'}:fail('已有封装路径无效。')
            if not config.kicad_cli:fail('冻结已有封装需要KiCad安装。')
            installed=Path(config.kicad_cli).resolve().parent.parent/'share/kicad/footprints'
            original=within(installed,alias+'.pretty/'+item+'.kicad_mod')
            if not original.is_file():fail('已有封装未找到，不能创建会遮蔽它的工程表。')
            rel='formforge-footprints/'+alias+'.pretty/'+item+'.kicad_mod';target=within(source,rel)
            if target.exists():fail('未登记的旧封装目标已存在，拒绝覆盖。')
            expected=digest(original);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
            if digest(original)!=expected or digest(target)!=expected:raise DomainError('input_changed','已有安装封装在冻结期间变化。',409)
            base.append(dict(footprint=selected,path=rel,sha256=expected,sourcePath=str(original),sourceVersion='installed-KiCad10; exact bytes frozen'))
            tracked.append(rel);aliases.add(alias)
        text='(fp_lib_table (version 7)'+''.join('\n (lib (name '+safe_string(a)+') (type "KiCad") (uri '+safe_string('${KIPRJMOD}/formforge-footprints/'+a+'.pretty')+') (options "") (descr "FormForge frozen installed definition"))' for a in sorted(aliases))+')'
        table=parse(text)
    if any(str(child_value(n,'name','')).casefold()==lib.casefold() for n in table.children('lib')):fail('封装库别名已存在，拒绝覆盖。')
    relative='formforge-imported-footprints/'+lib+'.pretty';folder=within(source,relative)
    if folder.exists():fail('封装目标已存在，拒绝覆盖。')
    record=dict(id=lib,footprint=footprint,path=relative+'/'+name+'.kicad_mod',sha256=actual,
        provenancePath=relative+'/provenance.json',sourcePath=str(requested.resolve()),sourceUri=op['sourceUri'],
        sourceVersion=op['sourceVersion'],description=op['description'],license=op.get('license','未声明'),
        provenanceTrust='caller-declared; content-sha256-verified',importedAt=now(),importedByJobId=job_id,
        formatVersion=child_value(tree,'version'),pads=[dict(number=p.values[1],at=[float(v) for v in p.one('at').values[1:3]]) for p in tree.children('pad')],
        modelReferences=[m.values[1] for m in tree.children('model')],modelsFrozen=False,baselineFootprints=base)
    if op.get('registeredSource'):record['registeredSource']=op['registeredSource']
    folder.mkdir(parents=True);within(source,record['path']).write_bytes(raw)
    within(source,record['provenancePath']).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
    declaration='\n (lib (name '+safe_string(lib)+') (type "KiCad") (uri '+safe_string('${KIPRJMOD}/'+relative)+') (options "") (descr "FormForge immutable imported footprint"))\n'
    updated=table.source[:table.end-1]+declaration+table.source[table.end-1:];parse(updated);table_path.write_text(updated,encoding='utf8')
    if digest(requested)!=actual:raise DomainError('input_changed','封装来源在导入期间变化。',409)
    return record,[*tracked,record['path'],record['provenancePath'],'fp-lib-table']
