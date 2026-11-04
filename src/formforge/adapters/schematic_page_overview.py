"""Native multi-page overview with a physical stub for every hierarchical pin."""
import math,uuid
from pathlib import Path
from .schematic import number,safe_string,location,fail
from .schematic_blocks import box
from .schematic_fields import overlaps

GRID=1.27


def overview(directory,records,ports,aliases,root_id,entry,width,height,hierarchical):
    def wire(a,b):return f'(wire (pts (xy {number(a[0])} {number(a[1])}) (xy {number(b[0])} {number(b[1])})) (stroke (width 0.1524) (type default)) (uuid "{uuid.uuid4()}"))'
    max_port=max((len(aliases[n])*1.27*.85 for items in ports.values() for n in items),default=0)
    max_net=max((len(n)*1.27*.85 for items in ports.values() for n in items),default=0)
    sw=math.ceil(max(76.2,max_port+12.7)/GRID)*GRID
    need=sw+12.7+max_net+15.24
    columns=2 if width>=400 and 2*need<=width-40.64 else 1
    cell_w=math.floor((width-40.64)/columns/GRID)*GRID
    if need>cell_w:fail('顶层接口文字宽度超出纸张；请调整纸张或明确网络命名，不能缩小文字。')
    static=[box(directory,n) for n in directory['objects'] if n.kind not in {'sheet','text'}]
    parent=[];bottom=25.4;column_y=[25.4]*columns
    def sheet_height(record):
        return max(25.4,16.51+len(ports[record['name']] if hierarchical else [])*6.35)
    # Place tall interfaces first, then choose the lowest available column.
    # Page numbers remain the declared traversal order, independently of position.
    ordered=sorted(enumerate(records,2),key=lambda item:(-sheet_height(item[1]),item[0]))
    for ordinal,record in ordered:
        nets=ports[record['name']] if hierarchical else []
        sh=sheet_height(record);slot_h=sh+13.97;candidates=[]
        for column,start_y in enumerate(column_y):
            x=20.32+column*cell_w;y=start_y
            while True:
                footprint=(x-1.27,y-6.35,x+need,y+sh+7.62)
                colliders=[b for b in static if overlaps(footprint,b)]
                if not colliders:break
                y=math.ceil((max(b[3] for b in colliders)+7.62)/GRID)*GRID
            if y+sh<=height-45:candidates.append((y,column,x))
        if not candidates:fail('顶层功能页及接口总览超出纸张；需减少页面或选择更大纸张。')
        y,column,x=min(candidates);column_y[column]=y+slot_h
        pins=[];pinx=x+sw
        for i,net in enumerate(nets):
            py=round(y+10.16+i*6.35,6);end=[round(pinx+12.7,6),py]
            pins.append(f'(pin {safe_string(aliases[net])} passive (at {number(pinx)} {number(py)} 0) (effects (font (size 1.27 1.27)) (justify right)) (uuid "{uuid.uuid4()}"))')
            parent.extend([wire([pinx,py],end),f'(global_label {safe_string(net)} (shape bidirectional) (at {number(end[0])} {number(py)} 0) (effects (font (size 1.27 1.27)) (justify left)) (uuid "{uuid.uuid4()}"))'])
        parent.append(f'(sheet (at {number(x)} {number(y)}) (size {number(sw)} {number(sh)} ) (stroke (width 0.15) (type default)) (fill (color 0 0 0 0)) (uuid "{record["sheetId"]}") (property "Sheetname" {safe_string(record["name"])} (at {number(x)} {number(y-2.54)} 0) (effects (font (size 1.52 1.52)) (justify left bottom))) (property "Sheetfile" {safe_string(record["sheet"])} (at {number(x)} {number(y+sh+2.54)} 0) (effects (font (size 1.27 1.27)) (justify left))) '+ ' '.join(pins)+f' (instances (project {safe_string(Path(entry).stem)} (path "/{root_id}" (page "{ordinal}")))))')
        bottom=max(bottom,y+sh)
    return parent,bottom
