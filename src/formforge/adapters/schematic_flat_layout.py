"""Reflow a flat source without migrating symbol paths or local net names."""
import itertools
import json
import math
import textwrap
import uuid

from .native_syntax import parse,Node
from .schematic import load_pages,properties,object_id,location,safe_string,number,fail
from .schematic_organization import bounds

GRID=1.27


def physical_units(page,nodes):
    """Keep complete physical branches rigid; label equality never joins geometry."""
    from .schematic_cleanup import components
    from .topology import symbol_pins
    local=dict(page,objects=nodes)
    root,_,_=components(local);parent={};refs={}
    def find(k):
        parent.setdefault(k,k)
        if parent[k]!=k:parent[k]=find(parent[k])
        return parent[k]
    for n in nodes:
        if n.kind!='symbol':continue
        keys=[root(tuple(p['at'])) for p in symbol_pins(local,n)]
        for k in keys:parent[find(k)]=find(keys[0])
        refs[properties(n)['Reference'].values[2]]=keys[0]
    units={}
    for n in nodes:
        if n.kind=='symbol':point=symbol_pins(local,n)[0]['at']
        elif n.kind=='wire':point=n.one('pts').children('xy')[0].values[1:3]
        else:point=location(n)[:2]
        key=find(root(tuple(map(float,point))))
        units.setdefault(key,[]).append(n)
    return [(items,{r for r,k in refs.items() if find(k)==key}) for key,items in units.items()]


def translate(node,delta):
    """Translate drawing coordinates, retaining every identity and instance path."""
    node=parse(node.raw);edits=[]
    def visit(n):
        if n.kind in {'at','xy'}:
            tail=' '.join(map(str,n.values[3:]))
            edits.append((n.start,n.end,f'({n.kind} {number(float(n.values[1])+delta[0])} {number(float(n.values[2])+delta[1])}'+(' '+tail if tail else '')+')'))
        else:
            for c in n.values:
                if isinstance(c,Node):visit(c)
    visit(node);text=node.raw
    for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
    return parse(text)


def pack_rectangles(sizes,width,height):
    """Bounded bottom-left search, no shrinking or rotation of circuit drawings."""
    best=None
    for order in itertools.islice(itertools.permutations(range(len(sizes))),5000):
        placed={}
        for i in order:
            w,h=sizes[i];candidates=[]
            xs={0,*[r[0]+r[2] for r in placed.values()]}
            ys={0,*[r[1]+r[3] for r in placed.values()]}
            for x in xs:
                for y in ys:
                    if x+w>width or y+h>height:continue
                    if any(x<a+c and x+w>a and y<b+d and y+h>b for a,b,c,d in placed.values()):continue
                    candidates.append((y+h,x+w,y,x))
            if not candidates:break
            _,_,y,x=min(candidates);placed[i]=(x,y,w,h)
        if len(placed)==len(sizes):
            inversions=sum((placed[a][1],placed[a][0])>(placed[b][1],placed[b][0]) for a in placed for b in placed if a<b)
            score=(round(max(y+h for x,y,w,h in placed.values()),6),round(max(x+w for x,y,w,h in placed.values()),6),inversions)
            if best is None or score<best[0]:best=score,placed
    if best is None:fail('保留单页的功能块放不下，请选择更大纸张；不会缩小字号或拆页迁移。')
    return best[1]


def layout_flat(config,source,entry,op,cancel):
    if op.get('blockLayout') or op.get('organization'):fail('保留单页排版不能同时请求分页或完整块变换。')
    pages=load_pages(source,entry)
    if len(pages)!=1:fail('保留单页排版要求已有独立单页。')
    original=pages[entry];original_nodes=original['tree']
    # The native root-local name is /TEXT. Never strip an arbitrary slash from
    # a global label: use only names explicitly present in this source.
    labels={}
    for n in original['objects']:
        if n.kind in {'label','global_label'}:
            name=('/' if n.kind=='label' else '')+str(n.values[1])
            value=(n.kind,str(n.values[1]))
            if name in labels and labels[name]!=value:fail('源标签作用域有歧义。')
            labels[name]=value
    original_symbols={object_id(n):n for n in original_nodes.children('symbol')}
    from .schematic_layout import layout
    generated_op=dict(op,preserveSheet=False,paper=original_nodes.one('paper').values[1])
    report,added=layout(config,source,entry,generated_op,cancel)
    generated=load_pages(source,entry)
    width,height={'A4':(297,210),'A3':(420,297),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[op['paper']]
    blocks=[];boxes=[];titles=[];units_by_group=[[] for _ in op['groups']];intermediate_positions={}
    group_names=op.get('groupTitles') or [f'Group {i+1}' for i in range(len(op['groups']))]
    if len(group_names)!=len(op['groups']):fail('功能标题须与明确功能组逐一对应。')
    for sheet,p in generated.items():
        if sheet==entry:continue
        nodes=[];refs=[]
        for node in p['objects']:
            text=node.raw
            if node.kind=='symbol':
                old=original_symbols[object_id(node)]
                instances=node.children('instances')
                for inst in reversed(instances):
                    # Restore the full original project/instance annotation.
                    text=text[:inst.start-node.start]+''.join(n.raw for n in old.children('instances'))+text[inst.end-node.start:]
                refs.append(properties(node)['Reference'].values[2])
                intermediate_positions[refs[-1]]=location(node)
            elif node.kind in {'label','global_label'}:
                key=str(node.values[1])
                if key not in labels:fail('生成标签没有明确的源作用域：'+key)
                kind,name=labels[key]
                # A local label has no global arrow or intersheet property.
                if kind=='global_label':text=text.replace(safe_string(key),safe_string(name),1)
                else:text=f'({kind} {safe_string(name)} '+ ' '.join(c.raw for c in node.values if isinstance(c,Node) and c.kind not in {'shape','property'})+')'
            nodes.append(parse(text))
        for items,members in physical_units(p,nodes):
            owners=[i for i,g in enumerate(op['groups']) if members and members<=set(g)]
            if len(owners)!=1:fail('真实连线跨越功能组；保留单页不能拆开物理支路。')
            units_by_group[owners[0]].append((items,bounds(p,items)))
    for title,units in zip(group_names,units_by_group):
        sizes=[(math.ceil((b[2]-b[0]+5.08)/GRID)*GRID,math.ceil((b[3]-b[1]+5.08)/GRID)*GRID) for _,b in units]
        candidates=[]
        for trial in sorted({max(w for w,h in sizes),152.4,177.8,203.2,228.6}):
            if trial<max(w for w,h in sizes):continue
            packed=pack_rectangles(sizes,trial,sum(h for w,h in sizes))
            w=max(x+a for x,y,a,b in packed.values());h=max(y+b for x,y,a,b in packed.values())
            score=w*h*(1+.2*abs(math.log(w/h/1.6)))
            candidates.append((score,packed))
        packed=min(candidates,key=lambda v:v[0])[1];nodes=[]
        for i,(items,b) in enumerate(units):
            x,y,_,_=packed[i];delta=tuple(round(v/GRID)*GRID for v in (x-b[0],y-b[1]))
            nodes.extend(translate(n,delta) for n in items)
        blocks.append(nodes);boxes.append(bounds(original,nodes));titles.append(title)
    if len(blocks)>9:fail('保留单页功能块超过有界打包范围。')
    # Notes remain legible and separate from circuits, in two balanced columns.
    note_columns=[[],[]];heights=[0.,0.]
    for n in original_nodes.children('text'):
        content='\n'.join(textwrap.fill(line,width=100,break_long_words=False,break_on_hyphens=False) for line in str(n.values[1]).splitlines())
        col=min(range(2),key=lambda i:heights[i]);note_columns[col].append((n,content))
        heights[col]+=len(content.splitlines())*2.54+3.81
    footer=max(heights)+6.35
    sizes=[(math.ceil((b[2]-b[0]+10.16)/GRID)*GRID,math.ceil((b[3]-b[1]+15.24)/GRID)*GRID) for b in boxes]
    placements=pack_rectangles(sizes,width-30,height-45-footer)
    chunks=[];packed=[]
    for i,nodes in enumerate(blocks):
        cancel();x,y,w,h=placements[i];b=boxes[i]
        dx=round((15+x+5.08-b[0])/GRID)*GRID;dy=round((15+y+10.16-b[1])/GRID)*GRID
        chunks.extend(translate(n,(dx,dy)).raw for n in nodes)
        tx=15+x+5.08;ty=15+y+2.54
        chunks.append(f'(text {safe_string(titles[i])} (at {number(tx)} {number(ty)} 0) (effects (font (size 1.524 1.524)) (justify left top)) (uuid "{uuid.uuid4()}"))')
        packed.append(dict(sheet=entry,title=titles[i],at=[x,y],size=[w,h]))
    bottom=15+max(y+h for x,y,w,h in placements.values())+6.35
    for col,notes in enumerate(note_columns):
        y=bottom
        for n,content in notes:
            text=translate(n,(15+col*(width-30)/2-location(n)[0],y-location(n)[1])).raw
            text=text.replace(safe_string(str(n.values[1])),safe_string(content),1)
            parsed=parse(text);effects=parsed.one('effects');just=effects.children('justify')
            if just:text=text.replace(just[0].raw,'(justify left top)',1)
            chunks.append(text);y+=len(content.splitlines())*2.54+3.81
    # Keep original flags and their original local labels away from new blocks.
    flags=[n for n in original_nodes.children('symbol') if properties(n)['Reference'].values[2].startswith('#')]
    for i,n in enumerate(flags):
        at=location(n);delta=tuple(round(v/GRID)*GRID for v in (25.4+i*25.4-at[0],height-30.48-at[1]));chunks.append(translate(n,delta).raw)
        from .topology import symbol_pins
        anchors={tuple(p['at']) for p in symbol_pins(original,n,allow_power_flag=True)}
        chunks.extend(translate(label,delta).raw for label in original['objects'] if label.kind in {'label','global_label'} and tuple(location(label)[:2]) in anchors)
    metadata=[n.raw for n in original_nodes.values if isinstance(n,Node) and n.kind not in {'symbol','wire','junction','label','global_label','no_connect','text','paper'}]
    (source/entry).write_text('(kicad_sch '+ ' '.join(metadata)+f' (paper "{op["paper"]}")\n'+'\n'.join(chunks)+')','utf8')
    for path in added:
        if path.endswith('.kicad_sch'):(source/path).unlink()
    final_symbols={properties(n)['Reference'].values[2]:n for n in load_pages(source,entry)[entry]['tree'].children('symbol')}
    branches=[]
    for branch in (report.get('branchLayout') or {}).get('branches',[]):
        branch=dict(branch);ref=branch['source']['reference'];old=intermediate_positions[ref];now=location(final_symbols[ref]);delta=[now[i]-old[i] for i in (0,1)]
        branch['sheet']=entry
        branch['points']=[[round(p[i]+delta[i],6) for i in (0,1)] for p in branch['points']]
        branch['afterAt']=location(final_symbols[branch['target']['reference']])
        branch.pop('beforeAt',None);branch.pop('nonConnectingCrossings',None)
        branches.append(branch)
    report=dict(kind='auto_layout',preserveSheet=True,paper=op['paper'],strategy=op['layoutStrategy'],packing=packed,
        inputGraphHash=report['inputGraphHash'],branchLayout=dict(version=1,branches=branches),
        pages=[dict(sheet=entry,symbols=len(original_symbols),references=list(final_symbols))],
        intermediatePlanning=dict(coordinateScope='Temporary pages before rigid component packing; not final sheet coordinates.',report=report),
        scope='Single original sheet, original instance paths and source label scopes. No PCB or network redesign.')
    (source/'formforge-schematic-layout.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    return report,['formforge-schematic-layout.json']
