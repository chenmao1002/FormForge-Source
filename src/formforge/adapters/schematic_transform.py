"""Explicit schematic transformations; electrical acceptance stays native."""
import math

from .native_syntax import parse
from .schematic import fail, location, number, object_id, properties, wire_points
from .topology import symbol_pins
from .schematic_connections import on_segment


def position_patch(node,xy,angle=None):
    if not all(math.isfinite(v) and abs(v)<=10000 for v in xy):fail('变换后坐标超出范围。')
    suffix='' if angle is None else ' '+number(angle)
    return node.start,node.end,'('+node.kind+' '+' '.join(number(round(v,6)) for v in xy)+suffix+')'


def rotate_symbol(page,node,rotation):
    if rotation not in (0,90,180,270):fail('器件只支持正交旋转。')
    origin=location(node);symbol_pins(page,node)
    if rotation==origin[2]:fail('器件角度没有改变。')
    angle=math.radians(rotation-origin[2]);c,s=math.cos(angle),math.sin(angle)
    edits=[position_patch(node.one('at'),origin[:2],rotation)]
    for field in node.children('property'):
        at=location(field);x,y=at[0]-origin[0],at[1]-origin[1]
        # KiCad symbol angles rotate in inverted page Y; field angle is relative.
        edits.append(position_patch(field.one('at'),[origin[0]+x*c+y*s,origin[1]-x*s+y*c],at[2]))
    edits.extend((n.start,n.end,'') for n in node.children('fields_autoplaced'))
    text=node.raw
    for start,end,replacement in sorted(edits,reverse=True):text=text[:start-node.start]+replacement+text[end-node.start:]
    return edits,symbol_pins(page,parse(text))


def move_group(page,op,check_cancel=lambda:None):
    ids=op['objectIds'];delta=op['delta']
    if len(set(ids))!=len(ids):fail('成组移动的对象 UUID 不能重复。')
    if delta==[0,0] or tuple(delta)==(0,0):fail('成组移动位移不能为零。')
    objects={object_id(n):n for n in page['objects']}
    if any(i not in objects for i in ids):fail('成组移动对象不存在。')
    selected=set(ids);allowed={'symbol','wire','junction','no_connect','label','global_label','text'}
    if any(objects[i].kind not in allowed for i in ids):fail('成组移动暂不支持所选对象类型。')
    static_points=[];static_segments=[];pins={}
    for identity,node in objects.items():
        check_cancel()
        if node.kind=='symbol':
            pins[identity]=symbol_pins(page,node,allow_power_flag=identity not in selected)
            if identity in selected:
                if properties(node)['Reference'].values[2].startswith('#'):fail('成组移动暂不支持电源符号。')
            else:static_points.extend(tuple(p['at']) for p in pins[identity])
        elif identity not in selected:
            if node.kind=='wire':
                points=wire_points(node);static_segments.extend(zip(points,points[1:]));static_points.extend(tuple(p) for p in points)
            elif node.kind in {'junction','no_connect','label','global_label','hierarchical_label'}:
                static_points.append(tuple(location(node)[:2]))
            elif node.kind=='sheet':static_points.extend(tuple(location(p)[:2]) for p in node.children('pin'))
    def fixed(p):
        return tuple(p) in static_points or any(on_segment(p,a,b) for a,b in static_segments)
    def moved(p):return [round(p[i]+delta[i],6) for i in range(2)]
    edits=[];fixed_ends=[];changes=[]
    for identity in ids:
        check_cancel();node=objects[identity]
        if node.kind=='wire':
            pts=node.one('pts').children('xy');before=wire_points(node)
            # A stationary contact in a segment interior cannot be stretched safely.
            for a,b in zip(before,before[1:]):
                if any(on_segment(p,a,b) and tuple(p) not in (tuple(a),tuple(b)) for p in static_points):
                    fail('导线内部存在未选择的连接点，请拆分并明确边界。')
            after=[]
            for i,p in enumerate(before):
                if fixed(p):
                    after.append(p);fixed_ends.append(dict(objectId=identity,at=p))
                else:after.append(moved(p))
                edits.append(position_patch(pts[i],after[-1]))
            if any(a==b or (a[0]!=b[0] and a[1]!=b[1]) for a,b in zip(after,after[1:])):
                fail('固定外部端点后导线不再正交或长度为零，请先明确重布线路径。')
            changes.append(dict(objectId=identity,before=before,after=after))
        else:
            anchors=[p['at'] for p in pins[identity]] if node.kind=='symbol' else [location(node)[:2]] if node.kind!='text' else []
            if any(fixed(p) for p in anchors):fail('移动对象直接连接未选对象，请选入连接导线或明确断开边界。')
            at=location(node);edits.append(position_patch(node.one('at'),moved(at),at[2] if len(at)>2 else None))
            if node.kind in {'symbol','global_label'}:
                for field in node.children('property'):
                    pos=location(field);edits.append(position_patch(field.one('at'),moved(pos),pos[2]))
                edits.extend((n.start,n.end,'') for n in node.children('fields_autoplaced'))
            changes.append(dict(objectId=identity,before=at[:2],after=moved(at)))
    return edits,dict(kind='move_group',sheet=op['sheet'],objectIds=ids,delta=list(delta),objects=changes,
                      fixedEndpoints=fixed_ends,nativeConnectivityCheckRequired=True)
