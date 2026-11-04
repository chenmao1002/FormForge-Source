"""Bounded single-source-sheet pagination; native pin partitions remain authoritative."""
import json
import time
import math
import subprocess
import uuid
from pathlib import Path
from .schematic import load_pages, properties, location, object_id, safe_string, number, fail
from .topology import symbol_pins
from .connectivity import read_graph
from .native_syntax import Node


def layout(config, source, entry, op, cancel):
    if op.get('preserveSheet'):
        from .schematic_flat_layout import layout_flat
        return layout_flat(config,source,entry,op,cancel)
    if op.get('blockLayout'):
        from .schematic_blocks import layout_blocks
        return layout_blocks(source,entry,op,cancel)
    if op.get('organization'):
        from .schematic_organization import organize
        return organize(config,source,entry,op,cancel)
    pages = load_pages(source, entry)
    if len(pages) != 1 or pages[entry]['tree'].children('sheet'):
        fail('首版自动排版只接受独立单页来源；已有多页工程须保留并另行规划。')
    page = pages[entry]
    # Rebuilding replaces the root. UUID-less metadata must be accounted for too.
    drawable={'symbol','wire','junction','label','global_label','no_connect','text'}
    rebuilt_metadata={'version','generator','generator_version','uuid','paper','title_block','lib_symbols','sheet_instances','embedded_fonts'}
    unsupported_root={n.kind for n in page['tree'].values if isinstance(n,Node)}-drawable-rebuilt_metadata
    if unsupported_root:fail('重建分页不支持原图根元数据，不能静默丢弃：'+', '.join(sorted(unsupported_root))+'；请使用保留层次的完整块整理或保留原图。')
    unsupported = {n.kind for n in page['objects']} - drawable
    if unsupported:
        fail('自动排版不能静默丢弃原图对象：'+', '.join(sorted(unsupported)))
    root_id = object_id(page['tree'])
    symbols = {}; flags=[]
    for node in page['tree'].children('symbol'):
        ref = properties(node)['Reference'].values[2]
        if ref.startswith('#'):
            if not ref.startswith('#FLG'):fail('首版自动排版仅支持有明确标签连接的电源标志。')
            flags.append((node,symbol_pins(page,node,allow_power_flag=True)));continue
        if ref in symbols:
            fail('首版自动排版不支持重复位号/多单元。')
        symbols[ref] = (node, symbol_pins(page, node))
    if not symbols:
        fail('没有可排版器件。')
    netfile = source/'layout-input.xml'
    result = subprocess.run([str(config.kicad_cli),'sch','export','netlist','--format','kicadxml','--output',str(netfile),str(source/entry)],capture_output=True,timeout=120)
    if result.returncode:
        fail('自动排版前原生网表导出失败。')
    from .schematic_netclasses import capture,apply as apply_netclasses
    netclass_plan=capture(source,entry,netfile)
    graph = read_graph(netfile); netfile.unlink()
    if {c['reference'] for c in graph['components']} != set(symbols):
        fail('符号与原生器件集合不一致。')
    nets = {}
    for group in graph['groups']:
        name = group['name'] if group['name'] != '(isolated)' else 'FF_'+group['id'][:16]
        for pin in group['pins']:
            nets[pin['reference'],pin['pin']] = name
    flag_chunks=[]
    note_y=110
    for note in page['tree'].children('text'):
        anchor=note.one('at');pos=location(note)
        text=note.raw;replacement=f'(at 15 {note_y} '+ ' '.join(str(v) for v in pos[2:])+')'
        text=text[:anchor.start-note.start]+replacement+text[anchor.end-note.start:]
        flag_chunks.append(text);note_y+=max(6,3*len(str(note.values[1]).splitlines()))

    for node,pins in flags:
        flag_chunks.append(node.raw)
        for pin in pins:
            labels=[n for n in page['objects'] if n.kind in {'label','global_label'} and location(n)[:2]==pin['at']]
            if len(labels)!=1:fail('电源标志需要唯一同锚点标签才能保真排版。')
            matches={g['name'] for g in graph['groups'] if g['name']==labels[0].values[1] or g['name'].endswith('/'+labels[0].values[1])}
            if len(matches)!=1:fail('电源标志标签无法唯一对应原生网络。')
            text=labels[0].raw
            if labels[0].kind=='label':
                text=text.replace('(label ', '(global_label ',1)
                text=text[:-1]+' (shape bidirectional))'
            text=text.replace(safe_string(labels[0].values[1]),safe_string(next(iter(matches))),1)
            flag_chunks.append(text)
    started=time.perf_counter()
    groups = op.get('groups') or [[r] for r in sorted(symbols,key=lambda r:(round(location(symbols[r][0])[1]/25.4),location(symbols[r][0])[0]))]
    flat = [r for g in groups for r in g]
    if len(set(flat)) != len(flat) or set(flat) != set(symbols):
        fail('排版分组须覆盖全部器件一次。')
    intent = op.get('intent')
    if intent:
        from .schematic_branches import validate
        validate(intent, symbols, graph, op.get('groups'), op['layoutStrategy'])
    limit = op['maxSymbolsPerSheet']; style = op['layoutStrategy']; paper = op['paper']
    width,height = {'A4':(297,210),'A3':(420,297),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[paper]
    spacing = {'readable':(62,42),'balanced':(50,34),'fast':(40,28)}[style]
    # Allocate cells from actual pin extents and label lengths; no shrinking symbols.
    cells = {}
    for ref,(node,pins) in symbols.items():
        extent = [location(node)[:2],*[p['at'] for p in pins],*[location(p)[:2] for p in node.children('property')]]
        xs,ys = zip(*extent)
        label = max(len(nets[ref,p['number']]) for p in pins)*.85
        cells[ref] = (max(spacing[0],max(xs)-min(xs)+2*label+16),max(spacing[1],max(ys)-min(ys)+18))
    allocations=[]; current=[]; x=y=15.; rowheight=0.
    def pack(group,x,y,rowheight):
        positions=[]
        for ref in group:
            cw,ch=cells[ref]
            if cw>width-30 or ch>height-45:fail('器件包络超出纸张可用范围，请选更大纸张。')
            if x+cw>width-15:x=15.;y+=rowheight;rowheight=0.
            positions.append((ref,[x+cw/2,y+ch/2]));x+=cw;rowheight=max(rowheight,ch)
        return positions,x,y,rowheight
    for group in groups:
        if not group:fail('排版分组不能为空。')
        if len(group)>limit:fail('功能组器件数超过每页上限，请明确拆分该组。')
        if op.get('groups') and current and x>15:x=15.;y+=rowheight+8;rowheight=0.
        proposed,nx,ny,nrh=pack(group,x,y,rowheight)
        if len(current)+len(group)>limit or ny+nrh>height-30:
            if current:allocations.append(current)
            current=[];x=y=15.;rowheight=0.
            proposed,nx,ny,nrh=pack(group,x,y,rowheight)
        if ny+nrh>height-30:fail('功能组超出单页可用面积，请拆组或选更大纸张。')
        current.extend(proposed);x,y,rowheight=nx,ny,nrh
    if current:allocations.append(current)
    if len(allocations)>9:fail('首版目录页最多容纳9张电路页，请增大纸张或每页上限。')
    if note_y>height-30:fail('原图说明超出目录页可用范围，不能静默裁切。')
    definitions=page['tree'].one('lib_symbols').raw
    titles=' '.join(n.raw for n in page['tree'].children('title_block'))
    no_connects={tuple(location(n)[:2]) for n in page['tree'].children('no_connect')}
    nc_keys={(ref,p['number']) for ref,(_,pins) in symbols.items() for p in pins if tuple(p['at']) in no_connects}
    refinement=None
    if style=='readable' and op.get('groups') and not op.get('_skipRefinement'):
        from .schematic_group_layout import refine
        allocations,refinement=refine(page,symbols,nets,groups,allocations,cells,cancel)
    paths=[]; reports=[]; sheet_nodes=[]
    for ordinal,items in enumerate(allocations,2):
        cancel(); filename=f'ff_layout_{ordinal-1}.kicad_sch'
        if (source/filename).exists():fail('自动排版输出文件已存在。')
        sheet_id=str(uuid.uuid4()); child_id=str(uuid.uuid4()); chunks=[]
        for ref,at in items:
            node,pins=symbols[ref];old=location(node);dx,dy=[round((at[i]-old[i])/1.27)*1.27 for i in (0,1)]
            patches=[]
            for target in [node,*node.children('property')]:
                anchor=target.one('at');p=location(target)
                patches.append((anchor.start,anchor.end,f'(at {number(p[0]+dx)} {number(p[1]+dy)} '+ ' '.join(str(v) for v in p[2:])+')'))
            # Rebind each instance path while retaining the project and symbol UUID.
            def walk(n):
                for c in n.values:
                    if isinstance(c,Node):
                        if c.kind=='path':
                            patches.append((c.start,c.end,c.raw.replace(safe_string(c.values[1]),safe_string('/'+root_id+'/'+sheet_id),1)))
                        else:walk(c)
            for instance in node.children('instances'):walk(instance)
            text=node.raw
            for a,b,replacement in sorted(patches,reverse=True):text=text[:a-node.start]+replacement+text[b-node.start:]
            chunks.append(text)
            for pin in pins:
                px,py=pin['at'][0]+dx,pin['at'][1]+dy; pos=f'{number(px)} {number(py)}';identity=str(uuid.uuid4())
                if (ref,pin['number']) in nc_keys:
                    chunks.append(f'(no_connect (at {pos}) (uuid "{identity}"))');continue
                angle=(pin['orientation']+180)%360;text_angle=angle%180;justify='right' if angle>=180 else 'left'
                chunks.append(f'(global_label {safe_string(nets[ref,pin["number"]])} (shape bidirectional) (at {pos} {text_angle}) (effects (font (size 1.27 1.27)) (justify {justify})) (uuid "{identity}") (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {pos} {text_angle}) (effects (font (size 1.27 1.27)) (hide yes))))')
        child=f'(kicad_sch (version 20250114) (generator "formforge") (uuid "{child_id}") (paper "{paper}") {titles} {definitions}\n'+ '\n'.join(chunks)+'\n(embedded_fonts no))'
        (source/filename).write_text(child,encoding='utf8');paths.append(filename)
        x0=20+((ordinal-2)%3)*65;y0=25+((ordinal-2)//3)*30
        sheet_nodes.append(f'(sheet (at {x0} {y0}) (size 50 15) (stroke (width 0.15) (type default)) (fill (color 0 0 0 0)) (uuid "{sheet_id}") (property "Sheetname" "Circuit {ordinal-1}" (at {x0} {y0-2.54} 0) (effects (font (size 1.27 1.27)) (justify left bottom))) (property "Sheetfile" "{filename}" (at {x0} {y0+17.54} 0) (effects (font (size 1.27 1.27)) (justify left))) (instances (project {safe_string(Path(entry).stem)} (path "/{root_id}" (page "{ordinal}")))))')
        reports.append(dict(sheet=filename,symbols=len(items),references=[r for r,_ in items]))
    flag_text=''.join(flag_chunks)
    (source/entry).write_text(f'(kicad_sch (version 20250114) (generator "formforge") (uuid "{root_id}") (paper "{paper}") {titles} {definitions} {flag_text} '+ '\n'.join(sheet_nodes)+' (embedded_fonts no))',encoding='utf8')
    branch_report = None
    if intent:
        from .schematic_branches import apply
        branch_report = apply(source, entry, intent, reports, cancel, nets)
    wire_reports=[]
    if style!='fast':
        from .schematic_connections import plan
        from .schematic import sha
        for relative,p in load_pages(source,entry).items():
            reserved={i for b in (branch_report or {}).get('branches',[]) if b['sheet']==relative for i in b['reservedLabelIds']}
            ids=[object_id(n) for n in p['tree'].children('global_label') if object_id(n) not in reserved]
            if len(ids)<2:continue
            if op.get('_localGroupsOnly'):
                by_ref={properties(n)['Reference'].values[2]:n for n in p['tree'].children('symbol')}
                batches=[]
                for group in groups:
                    anchors={tuple(pin['at']) for ref in group if ref in by_ref for pin in symbol_pins(p,by_ref[ref])}
                    batches.append([object_id(n) for n in p['tree'].children('global_label') if object_id(n) in ids and tuple(location(n)[:2]) in anchors])
                for batch in batches:
                    if len(batch)<2:continue
                    current=load_pages(source,entry)[relative]
                    changes,row=plan(current,dict(kind='connect_local_labels',sheet=relative,labelIds=batch,routingGridMm=1.27,maxLengthMm=80),cancel)
                    content=current['text']
                    for a,b,replacement in sorted(changes,reverse=True):content=content[:a]+replacement+content[b:]
                    current['path'].write_text(content,'utf8');wire_reports.append(row)
                continue
            edits,report=plan(p,dict(kind='connect_local_labels',sheet=relative,labelIds=ids,routingGridMm=1.27,maxLengthMm=80 if style=='readable' else 25.4),cancel)
            text=p['text']
            for a,b,replacement in sorted(edits,reverse=True):text=text[:a]+replacement+text[b:]
            p['path'].write_text(text,encoding='utf8');wire_reports.append(report)
    field_reports = []
    if intent:
        from .schematic_branches import tidy_pages
        field_reports = tidy_pages(source, entry, cancel)
    cleanup_report=None
    if intent and intent.get('cleanup'):
        from .schematic_cleanup import cleanup
        cleanup_report=cleanup(source,entry,cancel)
    netclass_report=None if op.get('_deferNetclasses') else apply_netclasses(source,entry,netclass_plan,cancel)
    report=dict(netclassPlan=netclass_plan,netclassAssignments=netclass_report,kind='auto_layout',planningSeconds=round(time.perf_counter()-started,6),strategy=style,paper=paper,maxSymbolsPerSheet=limit,pages=reports,localConnections=wire_reports,
                branchLayout=branch_report,fieldLayout=field_reports,presentationCleanup=cleanup_report,
                inputGraphHash=graph['hash'],groupRefinement=refinement,scope='Single-unit single-sheet source; deterministic cells and declared groups, not circuit-function inference or global aesthetic optimality.')
    reportfile='formforge-schematic-layout.json';(source/reportfile).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return report,[*paths,reportfile]
