"""Repack declared complete electrical blocks without rebuilding sheet hierarchy."""
import math
import time

from pathlib import Path
from .importer import digest
from .schematic_references import reference_style,reference_labels
from .native_syntax import parse
from .schematic import load_pages,object_id,location,properties,wire_points,number,fail,child_value
from .schematic_fields import symbol_box,text_box,overlaps
from .schematic_connections import on_segment
from .topology import symbol_pins

GRID=1.27
MOVABLE={'symbol','wire','junction','no_connect','label','global_label','hierarchical_label','text'}


def union_boxes(boxes):
    return min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)


def box(page,node,references=None):
    if node.kind=='symbol':
        pins=symbol_pins(page,node,allow_power_flag=True,allow_complex=True)
        boxes=[symbol_box(page,node,allow_complex=True)]
        for key,f in properties(node).items():
            texts=(references or {}).get(object_id(node),[]) if key=='Reference' else []
            for content in texts or [None]:
                if b:=text_box(f,symbol_rotation=location(node)[2],content=content):boxes.append(b)
        # Include pin names/numbers conservatively, not only the symbol body.
        for pin in pins:
            x,y=pin['at'];r=1.27
            boxes.append((x-r,y-r,x+r,y+r))
        return union_boxes(boxes)
    if node.kind=='wire':
        return union_boxes([(x-.35,y-.35,x+.35,y+.35) for x,y in wire_points(node)])
    if node.kind in {'text','label','global_label','hierarchical_label'}:
        at=location(node)
        if node.kind!='text':at=[*at[:2],at[2]%180]
        result=text_box(node,at=at,margin=2.54 if node.kind in {'global_label','hierarchical_label'} else .5)
        if result:return result
        x,y=location(node)[:2];return x,y,x,y
    if node.kind in {'junction','no_connect'}:
        x,y=location(node)[:2];return x-.8,y-.8,x+.8,y+.8
    if node.kind=='sheet':
        x,y=location(node)[:2];w,h=map(float,node.one('size').values[1:3])
        return union_boxes([(x,y,x+w,y+h),*[b for f in properties(node).values() if (b:=text_box(f))],
                            *[b for p in node.children('pin') if (b:=text_box(p,margin=2.54))]])
    fail('现有图页整理尚不能计算此对象包络：'+node.kind)


def anchors(page,node):
    if node.kind=='symbol':return [tuple(p['at']) for p in symbol_pins(page,node,allow_power_flag=True,allow_complex=True)]
    if node.kind=='wire':return [tuple(p) for p in wire_points(node)]
    if node.kind=='sheet':return [tuple(location(p)[:2]) for p in node.children('pin')]
    if node.kind in {'label','global_label','hierarchical_label','no_connect','junction'}:return [tuple(location(node)[:2])]
    return []


def validate_closure(page,membership,cancel):
    """Equal text never joins roots; real pin/endpoint/junction contacts do."""
    parent={};segments=[];points={};objects={object_id(n):n for n in page['objects']}
    def root(p):
        parent.setdefault(p,p)
        while parent[p]!=p:parent[p]=parent[parent[p]];p=parent[p]
        return p
    def union(a,b):parent[root(a)]=root(b)
    for identity,n in objects.items():
        points[identity]=anchors(page,n)
        for p in points[identity]:root(p)
        if n.kind=='wire':
            for a,b in zip(points[identity],points[identity][1:]):union(a,b);segments.append((a,b))
    if len(parent)*len(segments)>2_000_000:fail('功能块连接边界超过本轮几何检查预算。')
    for p in list(parent):
        cancel()
        for a,b in segments:
            if on_segment(p,a,b):union(p,a)
    owners={}
    for identity,ps in points.items():
        for p in ps:owners.setdefault(root(p),set()).add(membership.get(identity,'__static__'))
    if any(len(ids)>1 and ids!={'__static__'} for ids in owners.values()):
        fail('声明功能块仍有实体连接跨越边界；须把相连对象纳入同一块，不能隐式断线。')


def translate(node,delta):
    """Only absolute document anchors move. UUIDs, instances and local pins survive."""
    node=parse(node.raw);targets=[]
    if node.kind=='wire':targets=node.one('pts').children('xy')
    else:
        targets=[node.one('at')]
        if node.kind in {'symbol','global_label'}:targets.extend(p.one('at') for p in node.children('property'))
    text=node.raw
    for p in sorted(targets,key=lambda p:p.start,reverse=True):
        values=p.values[1:]
        moved='('+p.kind+' '+number(float(values[0])+delta[0])+' '+number(float(values[1])+delta[1])+(' '+' '.join(map(str,values[2:])) if len(values)>2 else '')+')'
        text=text[:p.start]+moved+text[p.end:]
    return text


def layout_blocks(source,entry,op,cancel):
    started=time.monotonic();spec=op['blockLayout']
    if op.get('organization') or op.get('intent') or op.get('groups'):
        fail('保留层次的块整理与重建分页/支路意图分开提交；原有块内支路保持。')
    if op['layoutStrategy']!='readable':fail('现有图页功能块整理要求易读策略。')
    pages=load_pages(source,entry);selected=[s['sheet'] for s in spec['sheets']]
    if len(set(selected))!=len(selected) or any(s not in pages for s in selected):fail('整理图页重复或不在原层次中。')
    project=source/Path(entry).with_suffix('.kicad_pro')
    style=reference_style(project if project.is_file() else None)
    writes=[];records=[]
    for selection in spec['sheets']:
        cancel();name=selection['sheet'];page=pages[name]
        if digest(page['path'])!=selection['expectedSheetHash']:fail('现有图页摘要不匹配，请重新读取。')
        if page['instanceCount']>1 and not selection['affectSharedInstances']:
            fail('共享图页整理会同时影响所有实例，须明确affectSharedInstances并核对实例列表。')
        groups=selection['groups'];ids=[g['id'] for g in groups]
        if len(set(ids))!=len(ids) or '__static__' in ids:fail('功能块身份重复或使用保留身份。')
        objects={object_id(n):n for n in page['objects']};membership={}
        for group in groups:
            for identity in group['objectIds']:
                if identity in membership or identity not in objects:fail('功能块对象重复或不存在。')
                if objects[identity].kind not in MOVABLE:fail('此对象暂不支持随功能块整理：'+objects[identity].kind)
                membership[identity]=group['id']
        validate_closure(page,membership,cancel)
        references=reference_labels(page,style,Path(entry).stem)
        envelopes={g['id']:union_boxes([box(page,objects[i],references) for i in g['objectIds']]) for g in groups}
        static=[box(page,n,references) for identity,n in objects.items() if identity not in membership]
        original_paper=child_value(page['tree'],'paper')
        paper=spec.get('paper') or original_paper
        if original_paper not in {'A4','A3','A2','A1','A0'} or page['tree'].one('paper').values[2:]:fail('现有图页整理首批仅支持标准横向纸张。')
        width,height={'A4':(297,210),'A3':(420,297),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[paper]
        columns=min(selection['columns'],len(groups))
        cell_w=math.ceil((max(b[2]-b[0] for b in envelopes.values())+10.16)/GRID)*GRID
        cell_h=math.ceil((max(b[3]-b[1] for b in envelopes.values())+10.16)/GRID)*GRID
        if not spec.get('preservePositions') and 15.24+columns*cell_w-10.16>width-15:fail('现有图页列数与实际功能块包络不适合原纸张。')
        placed=[];changes=[];index=0
        for group in groups:
            b=envelopes[group['id']]
            if spec.get('preservePositions'):
                delta=[0.,0.];moved=list(b)
            while not spec.get('preservePositions'):
                cancel();x=15.24+(index%columns)*cell_w;y=20.32+(index//columns)*cell_h;index+=1
                delta=[round(round((x-b[0])/GRID)*GRID,6),round(round((y-b[1])/GRID)*GRID,6)]
                moved=[b[i]+delta[i%2] for i in range(4)]
                if moved[2]>width-15 or moved[3]>height-30:fail('原图页可读区域放不下完整功能块；需调整列数或显式改页，不缩小文字。')
                if not any(overlaps(moved,s) for s in static+placed):break
            placed.append(moved)
            for identity in group['objectIds']:
                n=objects[identity];changes.append((n.start,n.end,translate(n,delta)))
            group_refs=[dict(objectId=i,reference=properties(objects[i])['Reference'].values[2],unit=child_value(objects[i],'unit')) for i in group['objectIds'] if objects[i].kind=='symbol']
            records.append(dict(sheet=name,id=group['id'],name=group['name'],objectIds=group['objectIds'],
                symbols=group_refs,delta=delta,beforeEnvelope=list(b),afterEnvelope=moved,
                instancePaths=page['instancePaths'],instanceCount=page['instanceCount']))
        if paper!=original_paper:
            node=page['tree'].one('paper');changes.append((node.start,node.end,'(paper "'+paper+'")'))
        text=page['text']
        for a,b,value in sorted(changes,reverse=True):text=text[:a]+value+text[b:]
        parse(text);writes.append((page['path'],text))
    # Validate every selected page before writing any of them.
    for path,text in writes:path.write_bytes(text.encode('utf8'))
    local_reports=[];field_reports=[]
    if spec.get('localConnections'):
        from .schematic_connections import plan
        for selection in spec['sheets']:
            for group in selection['groups']:
                page=load_pages(source,entry)[selection['sheet']]
                selected_ids=set(group['objectIds'])
                labels=sorted([n for n in page['objects'] if object_id(n) in selected_ids and n.kind in {'label','global_label'}],key=lambda n:(n.kind,str(n.values[1]),object_id(n)))
                for start in range(0,len(labels),80):
                    batch=labels[start:start+80]
                    if len(batch)<2:continue
                    cancel();page=load_pages(source,entry)[selection['sheet']]
                    edits,row=plan(page,dict(kind='connect_local_labels',sheet=selection['sheet'],labelIds=[object_id(n) for n in batch],maxLengthMm=80,routingGridMm=1.27),cancel)
                    text=page['text']
                    for a,b,value in sorted(edits,reverse=True):text=text[:a]+value+text[b:]
                    page['path'].write_text(text,'utf8');local_reports.append(dict(row,sheet=selection['sheet'],groupId=group['id']))
    if spec.get('tidyFields'):
        from .schematic_fields import plan
        for selection in spec['sheets']:
            page=load_pages(source,entry)[selection['sheet']]
            selected_ids={i for g in selection['groups'] for i in g['objectIds']}
            ids=[object_id(n) for n in page['tree'].children('symbol') if object_id(n) in selected_ids and not properties(n)['Reference'].values[2].startswith('#')]
            for start in range(0,len(ids),50):
                cancel();page=load_pages(source,entry)[selection['sheet']]
                edits,row=plan(page,dict(sheet=selection['sheet'],symbolIds=ids[start:start+50],maxShiftMm=12.7),cancel)
                text=page['text']
                for a,b,value in sorted(edits,reverse=True):text=text[:a]+value+text[b:]
                page['path'].write_text(text,'utf8');field_reports.append(row)
    cleanup_report=None
    if spec.get('cleanupLabels'):
        from .schematic_cleanup import cleanup
        cleanup_report=cleanup(source,entry,cancel,tidy_fields=False)
    if spec.get('paper'):
        for name,page in load_pages(source,entry).items():
            if name not in selected:continue
            width,height={'A4':(297,210),'A3':(420,297),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[spec['paper']]
            references=reference_labels(page,style,Path(entry).stem)
            for node in page['objects']:
                b=box(page,node,references)
                if b[0]<10 or b[1]<10 or b[2]>width-10 or b[3]>height-20:
                    fail('显式纸张尺寸放不下原有内容；不能裁切器件、文字或原生引脚。')
    return dict(kind='auto_layout',mode='preserve-hierarchy-blocks',strategy='readable',planningSeconds=round(time.monotonic()-started,6),
                localConnections=local_reports,fieldLayout=field_reports,presentationCleanup=cleanup_report,
                blockLayout=dict(version=1,groups=records,changedSheets=selected,
                    unchangedSheets=[p for p in pages if p not in selected],preservesHierarchy=True,
                    paperPolicy='explicit-'+spec['paper'] if spec.get('paper') else 'preserve-source',symbolLimitPolicy='no-pagination',
                    gridPolicy='1.27mm translation; original relative geometry retained',
                    scope='Whole declared blocks; no internal reroute, reannotation, sheet cloning or instance splitting.'),
                pages=[dict(sheet=p,instanceCount=pg['instanceCount'],symbols=len(pg['tree'].children('symbol'))) for p,pg in pages.items()]),[]
