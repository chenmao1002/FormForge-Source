"""Data invariants for presentation-only layout candidates, separate from ERC."""
from collections import Counter
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .native_syntax import Node,parse
from .schematic import load_pages,properties,object_id,location
from .topology import symbol_pins


def canonical(node,migrate_paths=False):
    return [('*' if migrate_paths and node.kind=='path' and i==1 else canonical(v,migrate_paths) if isinstance(v,Node) else v) for i,v in enumerate(node.values)]


def schematic_data(source,entry,preserve_hierarchy,cancel):
    symbols={};definitions={};nc=set();notes=Counter()
    for sheet,page in load_pages(source,entry).items():
        cancel();scope=sheet if preserve_hierarchy else ''
        for n in [n for library in page['tree'].children('lib_symbols') for n in library.children('symbol')]:
            key=scope+'|'+str(n.values[1]);value=canonical(n)
            if key in definitions and definitions[key]!=value:raise ValueError('Conflicting embedded symbol definitions: '+key)
            definitions[key]=value
        nc_points={tuple(location(n)[:2]) for n in page['tree'].children('no_connect')}
        for n in page['tree'].children('symbol'):
            key=scope+'|'+object_id(n);props=properties(n)
            if key in symbols:raise ValueError('Ambiguous physical symbol identity: '+key)
            symbols[key]=dict(reference=props['Reference'].values[2],properties=sorted((str(p.values[1]),str(p.values[2])) for p in n.children('property')),
                attributes={k:[canonical(c) for c in n.children(k)] for k in ['lib_id','unit','mirror','in_bom','on_board','dnp','exclude_from_sim','exclude_from_board','pin']},
                instances=[canonical(c,not preserve_hierarchy) for c in n.children('instances')],
                fieldVisibility={k:('hide' in p.one('effects').values or any(c.values[1:]==['yes'] for c in p.one('effects').children('hide'))) for k,p in props.items() if k in {'Reference','Value'}})
            if nc_points:
                for p in symbol_pins(page,n,allow_power_flag=True,allow_complex=True):
                    if tuple(p['at']) in nc_points:nc.add((key,p['number']))
        for n in page['tree'].children('text'):notes[''.join(str(n.values[1]).split())]+=1
    return dict(symbols=symbols,definitions=definitions,nc=nc,notes=notes)


def native_data(path):
    root=ET.parse(path).getroot();nets={};paths={};missing=[]
    for net in root.findall('nets/net'):
        pins=tuple(sorted((n.attrib['ref'],n.attrib['pin']) for n in net.findall('node')))
        if pins in nets:raise ValueError('Duplicate native pin partition')
        nets[pins]=dict(name=net.get('name'),netclass=net.get('class'))
        if 'class' not in net.attrib:missing.append(net.get('name'))
    for comp in root.findall('components/comp'):
        prefix=comp.find('sheetpath').get('tstamps')
        paths[comp.get('ref')]=sorted(prefix.rstrip('/')+'/'+p for p in (comp.findtext('tstamps') or '').split())
    return nets,paths,missing


def compare_layout(config,parent,candidate,before_xml,after_xml,preserve_hierarchy,cancel):
    a=schematic_data(config.data/parent['relativePath'],parent['entrypoints']['schematic'],preserve_hierarchy,cancel)
    b=schematic_data(config.data/candidate['relativePath'],candidate['entrypoints']['schematic'],preserve_hierarchy,cancel)
    old_nets,old_paths,old_missing=native_data(before_xml);new_nets,new_paths,new_missing=native_data(after_xml)
    issues=[]
    def issue(description,details):issues.append(dict(description=description,details=details))
    symbol_changes=[key for key in sorted(a['symbols'].keys()|b['symbols'].keys()) if a['symbols'].get(key)!=b['symbols'].get(key)]
    if symbol_changes:issue('排版改变了器件身份、属性、引脚、可见性或实例注释。',symbol_changes)
    definition_changes=[key for key in sorted(a['definitions'].keys()|b['definitions'].keys()) if a['definitions'].get(key)!=b['definitions'].get(key)]
    if definition_changes:issue('排版改变了内嵌符号库定义。',definition_changes)
    if a['nc']!=b['nc']:issue('原生NC所属器件与针号改变。',dict(removed=sorted(a['nc']-b['nc']),added=sorted(b['nc']-a['nc'])))
    missing_notes=a['notes']-b['notes']
    if missing_notes:issue('原始说明内容丢失。',dict(missing_notes))
    net_changes=[dict(pins=pins,before=old_nets.get(pins),after=new_nets.get(pins)) for pins in sorted(old_nets.keys()|new_nets.keys()) if old_nets.get(pins)!=new_nets.get(pins)]
    if net_changes:issue('原生网名或已解析网类改变。',net_changes)
    if old_missing or new_missing:issue('原生网表缺少网类结果，不能认定网类保持。',dict(before=old_missing,after=new_missing))
    protected=lambda r:{p['path']:p['sha256'] for p in r['manifest'] if Path(p['path']).suffix.lower() in {'.kicad_pro','.kicad_pcb','.kicad_dru'}}
    old_files,new_files=protected(parent),protected(candidate)
    file_changes=[p for p in sorted(old_files.keys()|new_files.keys()) if old_files.get(p)!=new_files.get(p)]
    if file_changes:issue('排版改变了原生项目设置、PCB或规则文件。',file_changes)
    path_changes=[dict(reference=ref,before=old_paths.get(ref),after=new_paths.get(ref)) for ref in sorted(old_paths.keys()|new_paths.keys()) if old_paths.get(ref)!=new_paths.get(ref)]
    if preserve_hierarchy and path_changes:issue('保留层次整理改变了原生器件实例路径。',path_changes)
    boards=[]
    for name in old_files:
        if Path(name).suffix.lower()!='.kicad_pcb':continue
        cancel();tree=parse((config.data/parent['relativePath']/name).read_text('utf-8-sig'));records=[];extras=[]
        for footprint in tree.children('footprint'):
            props=properties(footprint);ref=props.get('Reference');ref=ref.values[2] if ref else ''
            if ref not in old_paths:extras.append(ref);continue
            stored=[str(p.values[1]) for p in footprint.children('path')]
            records.append(dict(reference=ref,pcbPaths=stored,beforeMatches=bool(set(stored)&set(old_paths[ref])),afterMatches=bool(set(stored)&set(new_paths.get(ref,[])))))
        boards.append(dict(path=name,sha256=old_files[name],declaredEntrypoint=parent['entrypoints'].get('board')==name,components=records,boardOnlyReferences=sorted(extras),missingBoardReferences=sorted(old_paths.keys()-{r['reference'] for r in records})))
    downstream=candidate.get('downstreamRequiresReview',[])
    if boards and 'pcb' not in downstream:issue('已有PCB未标记下游重审。',downstream)
    return dict(version=1,policy='layout-data-preservation-v1',beforeRevisionId=parent['id'],afterRevisionId=candidate['id'],passed=not issues,
        symbolCount=len(a['symbols']),embeddedDefinitionCount=len(a['definitions']),noConnectPinCount=len(a['nc']),protectedFiles=old_files,
        netClasses=sorted({n['netclass'] for n in old_nets.values() if n['netclass'] is not None}),nativeNetCount=len(old_nets),
        instancePathChanges=path_changes,pcbAssociations=boards,downstreamRequiresReview=downstream,issues=issues,
        scope='Native names/classes, physical symbol semantics/NC, embedded definitions, original non-whitespace note characters and protected file manifests. Rebuild path migration is explicit; existing PCB copper is not updated or validated.')
