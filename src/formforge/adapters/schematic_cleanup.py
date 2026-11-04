"""Presentation-only cleanup of physically connected label groups and fields."""
from collections import defaultdict
import math
import textwrap
import uuid

from .schematic import load_pages, properties, location, object_id, number, safe_string
from .schematic_fields import text_box, symbol_box, crosses, overlaps
from .schematic_branches import plan_stub, label_box, tidy_pages
from .schematic_connections import on_segment
from .topology import symbol_pins
from .native_syntax import parse


def wire(a,b):
    return '(wire (pts (xy '+number(a[0])+' '+number(a[1])+') (xy '+number(b[0])+' '+number(b[1])+')) (stroke (width 0.1524) (type default)) (uuid "'+str(uuid.uuid4())+'"))'


def label_crosses(node,segments):
    anchor=tuple(location(node)[:2]);box=label_box(node)
    for a,b in segments:
        if anchor in (a,b):
            far=b if a==anchor else a;length=math.dist(anchor,far)
            if length<=4:continue
            a=tuple(anchor[i]+(far[i]-anchor[i])*4/length for i in (0,1));b=far
        if crosses(box,a,b):return True
    return False


def components(page):
    parent={}
    def root(p):
        parent.setdefault(p,p)
        if parent[p]!=p:parent[p]=root(parent[p])
        return parent[p]
    def union(a,b):parent[root(a)]=root(b)
    segments=[];pinpoints=set()
    for n in page['objects']:
        if n.kind=='wire':
            pts=[tuple(map(float,p.values[1:3])) for p in n.one('pts').children('xy')]
            segments.extend((n,a,b) for a,b in zip(pts,pts[1:]));union(pts[0],pts[-1])
        elif n.kind=='symbol':
            for p in symbol_pins(page,n,allow_power_flag=True):
                xy=tuple(p['at']);root(xy);pinpoints.add(xy)
        elif n.kind in {'label','global_label','junction'}:root(tuple(location(n)[:2]))
    # Interior contact is joined only at an actual endpoint, pin or junction;
    # a bare X crossing has no vertex here.
    for p in list(parent):
        for _,a,b in segments:
            if on_segment(p,a,b):union(p,a)
    return root,segments,pinpoints


def normalize(source,entry):
    changes=[]
    for name,page in load_pages(source,entry).items():
        edits=[];note_y=110.
        for node in page['tree'].children('symbol'):
            if properties(node)['Reference'].values[2].startswith('#'):continue
            angle=(-location(node)[2])%180
            for key in ('Reference','Value'):
                f=properties(node).get(key)
                if not f or location(f)[2]==angle:continue
                at=location(f);a=f.one('at')
                edits.append((a.start,a.end,'(at '+number(at[0])+' '+number(at[1])+' '+number(angle)+')'))
                changes.append(dict(sheet=name,kind='field-angle',reference=properties(node)['Reference'].values[2],field=key))
        # Notes live on the generated directory page. Keep paragraph and word
        # content, but wrap and space by the actual resulting line count.
        if page['tree'].children('sheet'):
            for n in page['tree'].children('text'):
                original=str(n.values[1]);wrapped='\n'.join(textwrap.fill(line,width=88,break_long_words=False,break_on_hyphens=False) for line in original.splitlines())
                raw=n.raw;at=n.one('at');old=location(n)
                raw=raw[:at.start-n.start]+'(at 15 '+number(note_y)+' '+number(old[2])+')'+raw[at.end-n.start:]
                raw=raw.replace(safe_string(original),safe_string(wrapped),1)
                justify=n.one('effects').children('justify')
                if justify:raw=raw.replace(justify[0].raw,'(justify left top)',1)
                if raw!=n.raw:edits.append((n.start,n.end,raw));changes.append(dict(sheet=name,kind='note-wrap',objectId=object_id(n),lines=len(wrapped.splitlines())))
                note_y+=max(6.35,len(wrapped.splitlines())*2.54+3.81)
        text=page['text']
        for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
        page['path'].write_text(text,'utf8')
    return changes


def prune_label_stubs(source,entry,name,seeds):
    page=load_pages(source,entry)[name]
    _,segments,pins=components(page)
    protected=pins|{tuple(location(n)[:2]) for n in page['objects'] if n.kind in {'label','global_label','no_connect'}}
    active={object_id(n):(n,a,b) for n,a,b in segments};deleted=[];pending=list(seeds)
    while pending:
        p=pending.pop()
        if p in protected:continue
        touching=[v for v in active.values() if on_segment(p,v[1],v[2])]
        if len(touching)!=1:continue
        node,a,b=touching[0]
        if p not in (a,b):continue
        active.pop(object_id(node));deleted.append(node);pending.append(b if p==a else a)
    edits=[(n.start,n.end,'') for n in deleted]
    for n in page['tree'].children('junction'):
        p=tuple(location(n)[:2]);degree=sum((1 if p in (a,b) else 2) for _,a,b in active.values() if on_segment(p,a,b))
        if degree<3:edits.append((n.start,n.end,''))
    text=page['text']
    for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
    if edits:page['path'].write_text(text,'utf8')
    return [object_id(n) for n in deleted]


def nearby_fields(source,entry,cancel):
    changes=[]
    for name,page in load_pages(source,entry).items():
        boxes={};bodies={};fields={};wires=[]
        page_width,page_height={'A3':(420,297),'A4':(297,210),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[page['tree'].one('paper').values[1]]
        for node in page['objects']:
            identity=object_id(node)
            if node.kind=='symbol':
                body=symbol_box(page,node,False);boxes[identity,'body']=symbol_box(page,node,True);bodies[identity]=body
                for key,f in properties(node).items():
                    box=text_box(f,symbol_rotation=location(node)[2])
                    if box:
                        boxes[identity,key]=box
                        if key in {'Reference','Value'}:fields[identity,key]=(f,location(node)[2],properties(node)['Reference'].values[2])
            elif node.kind in {'label','global_label','text'}:
                box=label_box(node)
                if box:boxes[identity,'text']=box
            elif node.kind=='no_connect':
                x,y=location(node)[:2];boxes[identity,'nc']=(x-1,y-1,x+1,y+1)
            elif node.kind=='wire':
                pts=[tuple(map(float,n.values[1:3])) for n in node.one('pts').children('xy')];wires.extend(zip(pts,pts[1:]))
        edits=[]
        for key,(f,angle,ref) in fields.items():
            cancel();old=location(f);body=bodies[key[0]]
            distance=math.hypot(max(body[0]-old[0],0,old[0]-body[2]),max(body[1]-old[1],0,old[1]-body[3]))
            if distance<=5.08:continue
            center=((body[0]+body[2])/2,(body[1]+body[3])/2)
            width=boxes[key][2]-boxes[key][0];height=boxes[key][3]-boxes[key][1]
            candidates=[]
            for gap in (2.54,5.08,7.62,10.16):
                candidates.extend([(center[0],body[1]-gap-height/2),(center[0],body[3]+gap+height/2),
                    (body[0]-gap-width/2,center[1]),(body[2]+gap+width/2,center[1])])
            for x,y in candidates:
                at=[round(x,6),round(y,6),old[2]];box=text_box(f,at,symbol_rotation=angle)
                if box[0]<15 or box[1]<15 or box[2]>page_width-15 or box[3]>page_height-30:continue
                if any(overlaps(box,v) for k,v in boxes.items() if k!=key) or any(crosses(box,a,b) for a,b in wires):continue
                a=f.one('at');edits.append((a.start,a.end,'(at '+' '.join(number(v) for v in at)+')'));boxes[key]=box
                changes.append(dict(sheet=name,reference=ref,field=key[1],before=old,after=at));break
        text=page['text']
        for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
        if edits:page['path'].write_text(text,'utf8')
    return changes


def cleanup(source,entry,cancel,tidy_fields=True):
    normalized=normalize(source,entry) if tidy_fields else []
    initial_fields=tidy_pages(source,entry,cancel) if tidy_fields else []
    records=[]
    for name,page in load_pages(source,entry).items():
        root,segments,pinpoints=components(page)
        groups=defaultdict(list)
        for n in page['objects']:
            if n.kind in {'label','global_label'}:
                groups[n.kind,str(n.values[1]),root(tuple(location(n)[:2]))].append(object_id(n))
        for _,identities in groups.items():
            cancel();page=load_pages(source,entry)[name]
            labels={object_id(n):n for n in page['objects'] if n.kind in {'label','global_label'}}
            nodes=[labels[i] for i in identities if i in labels]
            if not nodes:continue
            _,segments,pinpoints=components(page);pairs=[(a,b) for _,a,b in segments]
            nodes.sort(key=lambda n:(label_crosses(n,pairs),tuple(location(n)[:2]) in pinpoints,object_id(n)))
            keep=nodes[0];remove=nodes[1:];edits=[(n.start,n.end,'') for n in remove]
            record=dict(sheet=name,net=str(keep.values[1]),removedDuplicateIds=[object_id(n) for n in remove],moved=False)
            if label_crosses(keep,pairs):
                root,_,_=components(page);component=root(tuple(location(keep)[:2]));connected=[(n,a,b) for n,a,b in segments if root(a)==component]
                occupied=[];excluded={object_id(n) for n in nodes}
                for n in page['objects']:
                    if object_id(n) in excluded:continue
                    if n.kind=='symbol':
                        occupied.append(symbol_box(page,n,include_pins=True))
                        occupied.extend(v for f in properties(n).values() if (v:=text_box(f,symbol_rotation=location(n)[2])))
                    elif n.kind in {'label','global_label','text'}:
                        v=label_box(n)
                        if v:occupied.append(v)
                    elif n.kind=='no_connect':
                        x,y=location(n)[:2];occupied.append((x-1,y-1,x+1,y+1))
                w,h={'A3':(420,297),'A4':(297,210),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}[page['tree'].one('paper').values[1]]
                stub=None
                for main,a,b in sorted(connected,key=lambda row:-math.dist(row[1],row[2])):
                    stub=plan_stub([a,b],str(keep.values[1]),occupied,pairs,w,h)
                    if stub:break
                if stub:
                    mid,end,label,a,b=stub
                    if keep.kind=='label':label=label.replace('(global_label ','(label ',1).replace('(shape bidirectional) ','')
                    edits.append((keep.start,keep.end,''))
                    edits.append((main.start,main.end,wire(a,mid)+'\n'+wire(mid,b)))
                    insert=wire(mid,end)+'\n'+label+'\n(junction (at '+number(mid[0])+' '+number(mid[1])+') (diameter 0) (color 0 0 0 0) (uuid "'+str(uuid.uuid4())+'"))\n'
                    edits.append((page['tree'].end-1,page['tree'].end-1,insert));record.update(moved=True,fromAt=location(keep)[:2],toAt=end)
                else:record['unresolved']='no_clear_label_stub'
            if edits:
                text=page['text']
                for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
                page['path'].write_text(text,'utf8')
                seeds=[tuple(location(n)[:2]) for n in remove]
                if record['moved']:seeds.append(tuple(location(keep)[:2]))
                record['removedStubWireIds']=prune_label_stubs(source,entry,name,seeds)
            if edits or record.get('unresolved'):records.append(record)
    nearby=nearby_fields(source,entry,cancel) if tidy_fields else []
    final_fields=tidy_pages(source,entry,cancel) if tidy_fields else []
    return dict(policy='physical-label-cleanup-v1',normalized=normalized,labels=records,nearbyFields=nearby,initialFields=initial_fields,finalFields=final_fields,
        scope='Physical same-name label groups only; exact net equivalence and native visual review remain mandatory.')
