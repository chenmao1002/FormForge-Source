"""Declared functional pages, repeated motifs and bounded functional-page hierarchy."""
import json
import math
import uuid
import time
from collections import defaultdict

from .schematic import load_pages,properties,location,object_id,safe_string,number,fail
from .topology import symbol_pins
from .schematic_fields import symbol_box,text_box
from .schematic_branches import transformed
from .schematic_cleanup import components
from .connectivity import read_graph
from .native_syntax import parse,Node

GRID=1.27


def translated(node,delta,rename=None):
    node=parse(node.raw);edits=[]
    def visit(n):
        if n.kind in {'at','xy'}:
            values=n.values[1:]
            edits.append((n.start,n.end,'('+n.kind+' '+number(float(values[0])+delta[0])+' '+number(float(values[1])+delta[1])+(' '+' '.join(map(str,values[2:])) if len(values)>2 else '')+')'))
        elif n.kind=='uuid':edits.append((n.start,n.end,'(uuid "'+str(uuid.uuid4())+'")'))
        else:
            for c in n.values:
                if isinstance(c,Node):visit(c)
    visit(node);text=node.raw
    for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
    if rename is not None and node.kind in {'label','global_label'}:
        text=text.replace(safe_string(node.values[1]),safe_string(rename),1)
    return parse(text)


def bounds(page,nodes):
    boxes=[]
    for n in nodes:
        if n.kind=='symbol':
            boxes.append(symbol_box(page,n,True))
            boxes.extend(v for f in properties(n).values() if (v:=text_box(f,symbol_rotation=location(n)[2])))
        elif n.kind in {'global_label','label','text'}:
            v=text_box(n,margin=2.54 if n.kind=='global_label' else .5)
            if v:boxes.append(v)
        elif n.kind=='wire':
            pts=[tuple(map(float,p.values[1:3])) for p in n.one('pts').children('xy')]
            boxes.extend((x-.2,y-.2,x+.2,y+.2) for x,y in pts)
        else:
            x,y=location(n)[:2];boxes.append((x-1,y-1,x+1,y+1))
    return min(v[0] for v in boxes),min(v[1] for v in boxes),max(v[2] for v in boxes),max(v[3] for v in boxes)


def signature(group,nets):
    local=defaultdict(list)
    for i,ref in enumerate(group['references']):
        for pin,net in nets[ref].items():local[net].append((i,pin))
    return sorted(sorted(v) for v in local.values())


def rebind(node,root,sheet):
    node=parse(node.raw);edits=[]
    def visit(n):
        if n.kind=='path':edits.append((n.start,n.end,n.raw.replace(safe_string(n.values[1]),safe_string('/'+root+'/'+sheet),1)))
        else:
            for c in n.values:
                if isinstance(c,Node):visit(c)
    for n in node.children('instances'):visit(n)
    text=node.raw
    for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
    return text


def organize(config,source,entry,op,cancel):
    started=time.monotonic()
    from .schematic_layout import layout
    org=op['organization'];groups=org['groups'];by_id={g['id']:g for g in groups}
    if len(by_id)!=len(groups):fail('功能块身份重复。')
    refs=[g['references'] for g in groups]
    if op.get('groups') and op['groups']!=refs:fail('groups与organization的器件角色顺序不一致。')
    page_names=list(dict.fromkeys(g.get('page') for g in groups))
    planning=org.get('pagePlanning')
    if not planning and None in page_names:fail('未声明归属的功能块需要pagePlanning。')
    if len([name for name in page_names if name is not None])>9:fail('命名功能页最多支持九页。')
    modern=bool(planning) or len(page_names)!=2
    if modern and not planning:
        planning=dict(version=1,maxPages=9,pagePrefix='Functions',samePage=[],netWeights=[],maxCandidates=2000)
    if op['layoutStrategy']!='readable':fail('功能块排版使用易读策略。')
    replica_ids={gid for r in org['repetitions'] for gid in r['groups'][1:]}
    replica_refs={ref for g in groups if g['id'] in replica_ids for ref in g['references']}
    base=dict(op,groups=refs,organization=None,_localGroupsOnly=True,_skipRefinement=True,_deferNetclasses=True)
    if op.get('intent'):
        base['intent']=dict(op['intent'],branches=[b for b in op['intent']['branches'] if b['target']['reference'] not in replica_refs])
    preliminary,generated=layout(config,source,entry,base,cancel)
    pages=load_pages(source,entry);directory=pages[entry];root_id=object_id(directory['tree'])
    definitions=directory['tree'].one('lib_symbols').raw
    titles=' '.join(n.raw for n in directory['tree'].children('title_block'))
    symbols={};netfile=source/'organization-input.xml'
    # The already exported source graph is in the edit report only as a hash;
    # read the fully checked native intermediate to retain every exact net name.
    import subprocess
    subprocess.run([config.kicad_cli,'sch','export','netlist','--format','kicadxml','--output',str(netfile),str(source/entry)],check=True,capture_output=True,timeout=120)
    graph=read_graph(netfile);netfile.unlink();nets=defaultdict(dict)
    for net in graph['groups']:
        for p in net['pins']:nets[p['reference']][p['pin']]=net['name']
    membership={r:g['id'] for g in groups for r in g['references']};objects=defaultdict(list);group_pages={}
    for relative,page in pages.items():
        if relative==entry:continue
        root,_,_=components(page);owners=defaultdict(set)
        for n in page['tree'].children('symbol'):
            ref=properties(n)['Reference'].values[2];symbols[ref]=n
            for pin in symbol_pins(page,n):owners[root(tuple(pin['at']))].add(membership[ref])
        for n in page['objects']:
            if n.kind=='symbol':ids={membership[properties(n)['Reference'].values[2]]}
            elif n.kind=='wire':ids=owners[root(tuple(map(float,n.one('pts').children('xy')[0].values[1:3])))]
            elif n.kind in {'label','global_label','junction','no_connect'}:ids=owners[root(tuple(location(n)[:2]))]
            else:fail('功能块中存在未支持归属对象：'+n.kind)
            if len(ids)!=1:fail('功能块几何跨组或无器件归属，不能猜测拆分。')
            gid=next(iter(ids));objects[gid].append(n);group_pages[gid]=page
    repeated=[];claimed=set();clusters=[]
    for repeat in org['repetitions']:
        ids=repeat['groups']
        if len(set(ids))!=len(ids) or any(i not in by_id or i in claimed for i in ids):fail('重复模式的功能块不存在或重复归属。')
        claimed.update(ids);prototype=by_id[ids[0]];first=prototype['references']
        if not planning and len({by_id[i]['page'] for i in ids})!=1:fail('同一重复模式须在一个声明功能页内。')
        for gid in ids[1:]:
            target=by_id[gid];second=target['references']
            if len(first)!=len(second) or signature(prototype,nets)!=signature(target,nets):fail('重复通道角色拓扑不一致，不得为了对称改接。')
            mapping={}
            for a,b in zip(first,second):
                if symbols[a].one('lib_id').values[1]!=symbols[b].one('lib_id').values[1]:fail('重复角色的原生符号库身份不匹配。')
                for pin,net in nets[a].items():
                    other=nets[b][pin]
                    if net in mapping and mapping[net]!=other:fail('重复通道网络映射冲突。')
                    mapping[net]=other
            copies=[]
            for n in objects[ids[0]]:
                if n.kind=='symbol':
                    index=first.index(properties(n)['Reference'].values[2]);original=symbols[second[index]]
                    delta=tuple(location(n)[i]-location(original)[i] for i in (0,1))
                    moved=transformed(group_pages[gid],original,int(location(n)[2]),delta)
                    # Position corresponding fields identically while preserving
                    # the target's values, UUID, flags and all other properties.
                    text=moved.raw;edits=[]
                    for key,f in properties(moved).items():
                        if key in properties(n):
                            at=f.one('at');target_at=properties(n)[key].one('at').raw
                            edits.append((at.start,at.end,target_at))
                    for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
                    copies.append(parse(text))
                else:copies.append(translated(n,(0,0),mapping.get(str(n.values[1])) if n.kind in {'label','global_label'} else None))
            objects[gid]=copies
            repeated.append(dict(pattern=repeat['id'],prototype=ids[0],instance=gid,roleReferences=second,topologyMatched=True))
        clusters.append((ids,repeat['columns']))
    clusters.extend(([g['id']],1) for g in groups if g['id'] not in claimed)
    page_plan=None;planned_placements=None
    if planning:
        from .schematic_page_planning import plan_pages
        initial_envelopes={gid:bounds(group_pages[gid],nodes) for gid,nodes in objects.items()}
        assignments,planned_placements,page_plan=plan_pages(groups,initial_envelopes,nets,org['repetitions'],planning,op['paper'],op['maxSymbolsPerSheet'],cancel)
        page_plan['mode']='automatic' if org.get('pagePlanning') else 'declared'
        groups=[dict(g,page=assignments[g['id']]) for g in groups];by_id={g['id']:g for g in groups}
        page_names=list(dict.fromkeys(g['page'] for g in groups))
    net_pages=defaultdict(set)
    for g in groups:
        for ref in g['references']:
            for net in nets[ref].values():
                if net!='(isolated)' and not net.startswith('unconnected-'):net_pages[net].add(g['page'])
    cross=sorted(net for net,ps in net_pages.items() if len(ps)>1)
    prefix='H_'
    while any(prefix+net.lstrip('/') in net_pages for net in cross):prefix='H_'+prefix
    aliases={net:prefix+net.lstrip('/') for net in cross}
    if org['hierarchicalInterfaces']:
        for gid,nodes in objects.items():
            converted=[]
            for n in nodes:
                if n.kind=='global_label' and n.values[1] in aliases:
                    converted.append(parse(f'(label {safe_string(aliases[n.values[1]])} {n.one("at").raw} {n.one("effects").raw} (uuid "{object_id(n)}"))'))
                else:converted.append(n)
            objects[gid]=converted
    width,height={'A4':(297,210),'A3':(420,297),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[op['paper']]
    if not modern and org['hierarchicalInterfaces'] and width<330:fail('首批双功能页接口总览要求A3或更大纸张。')
    placements={};envelopes={gid:bounds(group_pages[gid],nodes) for gid,nodes in objects.items()}
    page_rows={p:[80.01,25.4,0.] for p in page_names}
    for ids,columns in ([] if planning else clusters):
        cancel();p=by_id[ids[0]]['page'];x,y,rowheight=page_rows[p]
        cell_w=math.ceil((max(max(envelopes[i][2]-envelopes[i][0],len(by_id[i]['name'])*1.52*.85) for i in ids)+10.16)/GRID)*GRID
        cell_h=math.ceil((max(envelopes[i][3]-envelopes[i][1] for i in ids)+13.97)/GRID)*GRID
        columns=min(columns,len(ids));cluster_w=columns*cell_w;cluster_h=math.ceil(len(ids)/columns)*cell_h
        if x+cluster_w>width-15:x=80.01;y+=rowheight;rowheight=0
        if x+cluster_w>width-15 or y+cluster_h>height-30:fail('功能页实际包络超出纸张，需减少列数、显式拆页或选择更大纸张。')
        for i,gid in enumerate(ids):
            env=envelopes[gid];target=(x+(i%columns)*cell_w,y+(i//columns)*cell_h)
            delta=tuple(round(round((target[k]-env[k])/GRID)*GRID,6) for k in (0,1))
            placements[gid]=delta
        page_rows[p]=[x+cluster_w,y,max(rowheight,cluster_h)]
    if planned_placements is not None:placements=planned_placements
    page_ports={p:[net for net in cross if p in net_pages[net]] for p in page_names}
    # Exact cross-page memberships drive interfaces; names are not role guesses.
    if len(cross)>32:fail('首批层次总览最多32条显式跨页网络。')
    child_records=[];new_files=[];interfaces=[]
    for ordinal,p in enumerate(page_names,1):
        sid=str(uuid.uuid4());cid=str(uuid.uuid4());filename=f'ff_function_{ordinal}.kicad_sch';chunks=[];page_refs=[]
        if (source/filename).exists():fail('功能页输出文件已存在，不能覆盖原文件。')
        for g in groups:
            if g['page']!=p:continue
            gid=g['id'];delta=placements[gid];page_refs.extend(g['references'])
            if len(page_refs)>op['maxSymbolsPerSheet']:fail('声明功能页超过每页器件上限。')
            env=envelopes[gid]
            chunks.append(f'(text {safe_string(g["name"])} (at {number(env[0]+delta[0])} {number(env[1]+delta[1]-5.08)} 0) (effects (font (size 1.52 1.52)) (justify left bottom)) (uuid "{uuid.uuid4()}"))')
            for n in objects[gid]:
                if n.kind=='symbol':
                    node=transformed(group_pages[gid],n,int(location(n)[2]),delta)
                    chunks.append(rebind(node,root_id,sid))
                else:
                    moved=translated(n,delta)
                    if org['hierarchicalInterfaces'] and moved.kind=='global_label' and moved.values[1] in cross:
                        chunks.append(f'(label {safe_string(moved.values[1])} {moved.one("at").raw} {moved.one("effects").raw} (uuid "{object_id(moved)}"))')
                    else:chunks.append(moved.raw)
        ports=page_ports[p] if org['hierarchicalInterfaces'] else []
        port_width=max((len(aliases[n])*1.27*.85 for n in ports),default=0)
        interface_x=math.ceil(max(25.4,port_width+15.24)/GRID)*GRID
        if ports and interface_x+10.16+port_width+5.08>=80.01:fail('层次接口文字超出保留区；需调整名称或功能表达，不能压住功能块。')
        for index,net in enumerate(ports):
            y=round(30.48+index*6.35,6)
            port=aliases[net]
            chunks.extend([f'(hierarchical_label {safe_string(port)} (shape passive) (at {number(interface_x)} {number(y)} 0) (effects (font (size 1.27 1.27)) (justify right)) (uuid "{uuid.uuid4()}"))',
                f'(wire (pts (xy {number(interface_x)} {number(y)}) (xy {number(interface_x+10.16)} {number(y)})) (stroke (width 0.1524) (type default)) (uuid "{uuid.uuid4()}"))',
                f'(label {safe_string(port)} (at {number(interface_x+10.16)} {number(y)} 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uuid.uuid4()}"))'])
            interfaces.append(dict(page=p,net=net,port=port,childLabel=True,parentPin=True))
        # Keep original title metadata and add a page-specific title.
        page_titles=titles
        if directory['tree'].children('title_block'):
            old=directory['tree'].one('title_block').children('title')
            if old:page_titles=page_titles.replace(old[0].raw,'(title '+safe_string(p)+')',1)
            else:page_titles=page_titles[:-1]+' (title '+safe_string(p)+'))'
        else:page_titles='(title_block (title '+safe_string(p)+'))'
        (source/filename).write_text(f'(kicad_sch (version 20250114) (generator "formforge") (uuid "{cid}") (paper "{op["paper"]}") {page_titles} {definitions}\n'+ '\n'.join(chunks)+' (embedded_fonts no))','utf8')
        child_records.append(dict(name=p,sheet=filename,sheetId=sid,references=page_refs,symbols=len(page_refs)))
        new_files.append(filename)
    if modern:
        from .schematic_page_overview import overview
        parent,bottom=overview(directory,child_records,page_ports,aliases,root_id,entry,width,height,org['hierarchicalInterfaces'])
    else:
        parent=[];top=25.4;h=max(25.4,16.51+len(cross)*6.35)
        if top+h>height-65:fail('层次总览接口高度超过纸张。')
        for index,record in enumerate(child_records):
            x=30.48 if index==0 else 220.98;sw=76.2
            pinx=x+sw if index==0 else x
            pins=[]
            for i,net in enumerate(cross if org['hierarchicalInterfaces'] else []):
                y=round(top+10.16+i*6.35,6)
                pins.append(f'(pin {safe_string(aliases[net])} passive (at {number(pinx)} {number(y)} {0 if index==0 else 180}) (effects (font (size 1.27 1.27)) (justify {"right" if index==0 else "left"})) (uuid "{uuid.uuid4()}"))')
            parent.append(f'(sheet (at {number(x)} {number(top)}) (size {number(sw)} {number(h)}) (stroke (width 0.15) (type default)) (fill (color 0 0 0 0)) (uuid "{record["sheetId"]}") (property "Sheetname" {safe_string(record["name"])} (at {number(x)} {number(top-2.54)} 0) (effects (font (size 1.52 1.52)) (justify left bottom))) (property "Sheetfile" "{record["sheet"]}" (at {number(x)} {number(top+h+2.54)} 0) (effects (font (size 1.27 1.27)) (justify left))) '+ '\n'.join(pins)+f' (instances (project {safe_string(__import__("pathlib").Path(entry).stem)} (path "/{root_id}" (page "{index+2}")))))')
        if org['hierarchicalInterfaces']:
            for i,net in enumerate(cross):
                y=round(top+10.16+i*6.35,6)
                parent.extend([f'(wire (pts (xy 106.68 {number(y)}) (xy 157.48 {number(y)})) (stroke (width 0.1524) (type default)) (uuid "{uuid.uuid4()}"))',
                    f'(wire (pts (xy 157.48 {number(y)}) (xy 220.98 {number(y)})) (stroke (width 0.1524) (type default)) (uuid "{uuid.uuid4()}"))',
                    f'(wire (pts (xy 157.48 {number(y)}) (xy 157.48 {number(y-2.54)})) (stroke (width 0.1524) (type default)) (uuid "{uuid.uuid4()}"))',
                    f'(junction (at 157.48 {number(y)}) (diameter 0) (color 0 0 0 0) (uuid "{uuid.uuid4()}"))',
                    f'(global_label {safe_string(net)} (shape bidirectional) (at 157.48 {number(y-2.54)} 0) (effects (font (size 1.27 1.27)) (justify left)) (uuid "{uuid.uuid4()}"))'])
        bottom=top+h
    note_y=bottom+20.32
    for n in directory['objects']:
        if n.kind=='sheet':continue
        if n.kind=='text':
            at=location(n);moved=translated(n,(0,note_y-at[1]));parent.append(moved.raw);note_y+=max(6.35,3.81*len(str(n.values[1]).splitlines()))
        else:parent.append(n.raw)
    if note_y>height-25:fail('目录说明超出页面，不能静默裁切。')
    (source/entry).write_text(f'(kicad_sch (version 20250114) (generator "formforge") (uuid "{root_id}") (paper "{op["paper"]}") {titles} {definitions}\n'+'\n'.join(parent)+' (embedded_fonts no))','utf8')
    for old in generated:
        if old.endswith('.kicad_sch'):(source/old).unlink()
    from .schematic_netclasses import apply as apply_netclasses
    netclass_report=apply_netclasses(source,entry,preliminary['netclassPlan'],cancel)
    report=dict(preliminary,netclassAssignments=netclass_report,planningSeconds=round(time.monotonic()-started,6),branchLayout=None,fieldLayout=[],localConnections=[],presentationCleanup=None,
        preparation=dict(preliminary,phase='intermediate-before-functional-packing'),organization=dict(version=1,pages=child_records,repeatedInstances=repeated,
        placements=[dict(group=g['id'],page=g['page'],delta=placements[g['id']],envelope=envelopes[g['id']]) for g in groups],
        interfaces=interfaces,crossPageNets=cross,hierarchical=org['hierarchicalInterfaces'],pagePlanning=page_plan,scope='Declared roles and bounded functional pages; identities retained, no shared-file coalescing.'),pages=child_records)
    (source/'formforge-schematic-layout.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    return report,[*new_files,'formforge-schematic-layout.json']
