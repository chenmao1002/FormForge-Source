"""Analytic PCB frames shared by native geometry and routing."""
import math


def edges(box):
    x,y=box['origin'];w,h=box['width'],box['height'];r=box.get('cornerRadius',0)
    points=[([x+r,y],[x+w-r,y]),([x+w,y+r],[x+w,y+h-r]),([x+w-r,y+h],[x+r,y+h]),([x,y+h-r],[x,y+r])]
    result=[];k=math.sqrt(.5)
    arcs=[([x+w-r,y],[x+w-r+r*k,y+r-r*k],[x+w,y+r]),([x+w,y+h-r],[x+w-r+r*k,y+h-r+r*k],[x+w-r,y+h]),
          ([x+r,y+h],[x+r-r*k,y+h-r+r*k],[x,y+h-r]),([x,y+r],[x+r-r*k,y+r-r*k],[x+r,y])]
    for (a,b),(start,mid,end) in zip(points,arcs):
        if a!=b:result.append(dict(kind='line',start=a,end=b))
        if r:result.append(dict(kind='arc',start=start,mid=mid,end=end))
    return result


def contains(box,point,inset=0):
    x,y=box['origin'];w,h=box['width'],box['height'];px,py=point
    if not x+inset<=px<=x+w-inset or not y+inset<=py<=y+h-inset:return False
    r=max(0,box.get('cornerRadius',0)-inset)
    cx=min(max(px,x+inset+r),x+w-inset-r);cy=min(max(py,y+inset+r),y+h-inset-r)
    return (px-cx)**2+(py-cy)**2<=r*r+1e-12


def read_native(root):
    """Match every native edge, including arc midpoints, to one closed frame."""
    actual=[]
    for kind in ['gr_line','gr_arc','gr_rect','gr_poly','gr_circle','gr_curve']:
        for node in root.children(kind):
            if node.one('layer').values[1]!='Edge.Cuts':continue
            if kind not in ['gr_line','gr_arc']:raise ValueError('板体交接支持矩形或四角等半径圆角板框，不支持此切口。')
            row=dict(kind='arc' if kind=='gr_arc' else 'line')
            for key in ['start','end']+(['mid'] if kind=='gr_arc' else []):row[key]=[float(v) for v in node.one(key).values[1:3]]
            actual.append(row)
    if not actual:raise ValueError('缺少闭合板框。')
    pts=[p for e in actual for p in [e['start'],e['end']]];xs=[p[0] for p in pts];ys=[p[1] for p in pts]
    box=dict(origin=[min(xs),min(ys)],width=max(xs)-min(xs),height=max(ys)-min(ys),cornerRadius=0.)
    arcs=[e for e in actual if e['kind']=='arc']
    if arcs:
        if len(arcs)!=4:raise ValueError('圆角板框需要四段四分之一圆弧。')
        radii=[abs(e['start'][0]-e['end'][0]) for e in arcs];r=radii[0]
        if r<=0 or r>min(box['width'],box['height'])/2+2e-6 or any(abs(v-r)>2e-6 for v in radii):raise ValueError('板框圆角半径不一致或超出板尺寸。')
        box['cornerRadius']=r
    remaining=list(actual)
    def close(a,b):return all(abs(v-w)<=2e-6 for v,w in zip(a,b))
    for expected in edges(box):
        found=next((e for e in remaining if e['kind']==expected['kind'] and
            ((close(e['start'],expected['start']) and close(e['end'],expected['end'])) or (close(e['end'],expected['start']) and close(e['start'],expected['end']))) and
            (e['kind']=='line' or close(e['mid'],expected['mid']))),None)
        if found is None:raise ValueError('原生边线/圆弧不匹配闭合矩形或圆角矩形板框。')
        remaining.remove(found)
    if remaining:raise ValueError('板框含重复边线或额外切口。')
    return box
