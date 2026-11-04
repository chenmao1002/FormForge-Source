"""Native PCB drawing geometry, including saved filled copper and drill voids."""
import math
from .primitives import Drawing,Node,child,value,nums,identity,primitive,line,text,graphic,matrix,transform,point,path_points

COLORS={'F.Cu':'#dc7653','B.Cu':'#688ce6','F.SilkS':'#e8eee5','B.SilkS':'#c3daf1','Edge.Cuts':'#e5c66a','F.Mask':'#6fa57d','B.Mask':'#6fa57d'}


def summary(tree):
    """Count native objects once, independent of per-layer drawing copies."""
    layers=child(tree,'layers')
    copper=[str(n.values[1])for n in layers.values if isinstance(n,Node)and str(n.values[1]).endswith('.Cu')]if layers else ['F.Cu','B.Cu']
    footprints=tree.children('footprint')
    tracks=[n for n in tree.values if isinstance(n,Node)and n.kind in ('segment','arc')]
    widths=[float(value(n,'width'))for n in tracks if value(n,'width')is not None]
    return dict(copperLayers=copper,componentCount=len(footprints),
        padCount=sum(1 for f in footprints for p in f.children('pad')if len(p.values)>2 and p.values[2]!='np_thru_hole'),
        viaCount=len(tree.children('via')),trackCount=len(tracks),minTrackWidthMm=min(widths)if widths else None)


def build(tree):
    d=Drawing();layers=[str(n.values[1])for n in child(tree,'layers').values if isinstance(n,Node)] if child(tree,'layers') else ['F.Cu','B.Cu']
    copper=[l for l in layers if l.endswith('.Cu')];layer=lambda n:str(value(n,'layer','F.Cu'))
    def add_graphic(n,prefix='',m=None):
        name=layer(n);g=graphic(n,COLORS.get(name,'#ac99ce'))
        if g:
            pts=[nums(n,k)for k in ('start','end','center','mid')if nums(n,k)]
            d.add(prefix+identity(n,n.kind),[g],name,transform(m)if m else None,[point(m,p)if m else p for p in pts])
        else:d.omit(n,'Unsupported native board graphic')
    for index,n in enumerate(tree.values):
        if not isinstance(n,Node):continue
        kind=n.kind;key=identity(n,f'{kind}:{index}');name=layer(n);color=COLORS.get(name,'#ac99ce')
        if kind in ('segment','arc'):
            p=graphic(n,color);d.add(key,[p],name,points=[nums(n,'start'),nums(n,'end')],role='track')
        elif kind=='via':
            at=nums(n,'at');size=float(value(n,'size'));drill=float(value(n,'drill'));via_layers=child(n,'layers');names=[str(v)for v in via_layers.values[1:]]if via_layers else copper
            names=copper[copper.index(names[0]):copper.index(names[-1])+1]if names and names[0]in copper and names[-1]in copper else names
            for name in names:d.add(key+':'+name,[primitive('circle',cx=at[0],cy=at[1],r=size/2,fill=COLORS.get(name,'#ac99ce')),primitive('circle',cx=at[0],cy=at[1],r=drill/2,fill='#132b25')],name,points=[at],role='via')
        elif kind=='footprint':
            at=nums(n,'at',[0,0,0]);m=matrix(at);fields={p.values[1]:p.values[2]for p in n.children('property')if len(p.values)>2}
            for j,g in enumerate(n.values):
                if not isinstance(g,Node):continue
                gid=key+':'+identity(g,f'{g.kind}:{j}')
                if g.kind=='pad':
                    p=nums(g,'at',[0,0,0]);center=point(m,p);size=nums(g,'size');drillnode=child(g,'drill');drill=[float(v)for v in drillnode.values[1:]if not isinstance(v,Node)and v!='oval']if drillnode else []
                    offset=nums(drillnode,'offset',[0,0])if drillnode else [0,0]
                    if any(abs(v)>1e-9 for v in offset):d.omit(g,'Offset copper pad requires native shape expansion');continue
                    if len(size)!=2 or len(g.values)<4:d.omit(g,'Malformed pad');continue
                    shape=g.values[3];angle=p[2]if len(p)>2 else 0
                    # Pad angle is already absolute in the native board file.
                    pad_transform=f'translate({center[0]} {center[1]}) rotate({-angle})'
                    shapes=[];w,h=size;radius=0
                    if shape=='circle':shapes=[primitive('ellipse',cx=0,cy=0,rx=w/2,ry=h/2)]
                    elif shape in ('rect','roundrect','oval'):
                        radius=min(w,h)/2 if shape=='oval' else min(w,h)*float(value(g,'roundrect_rratio',.25))if shape=='roundrect' else 0
                        shapes=[primitive('rect',x=-w/2,y=-h/2,width=w,height=h,rx=radius,ry=radius)]
                    elif shape=='custom':d.omit(g,'Custom copper pad needs native polygon expansion');continue
                    else:d.omit(g,'Unsupported pad shape: '+str(shape));continue
                    padlayers=child(g,'layers');names=[str(v)for v in padlayers.values[1:]]if padlayers else []
                    if '*.Cu'in names:names=[v for v in names if v!='*.Cu']+copper
                    names=[v for v in names if v.endswith('.Cu')]
                    if g.values[2]=='np_thru_hole':names=['Edge.Cuts'];shapes=[]
                    for pname in names:
                        import copy
                        out=copy.deepcopy(shapes)
                        for s in out:s['attrs']['fill']=COLORS.get(pname,'#ac99ce')
                        if drill:
                            a,b=drill[0],drill[-1]
                            out.append(primitive('rect',x=-a/2+offset[0],y=-b/2+offset[1],width=a,height=b,rx=min(a,b)/2,ry=min(a,b)/2,fill='#132b25',
                                **(dict(stroke=COLORS['Edge.Cuts'],stroke_width=.12) if g.values[2]=='np_thru_hole' else {})))
                        d.add(gid+':'+pname,out,pname,pad_transform,[center],role='pad')
                elif g.kind in ('fp_line','fp_arc','fp_circle','fp_rect','fp_poly'):add_graphic(g,key+':',m)
                elif g.kind in ('property','fp_text'):
                    content=g.values[2]if len(g.values)>2 else '';pos=nums(g,'at',[0,0,0]);absolute=point(m,pos)
                    if g.kind=='property'and g.values[1]not in ('Reference','Value'):continue
                    content=str(content).replace('${REFERENCE}',fields.get('Reference','')).replace('${VALUE}',fields.get('Value',''))
                    # Native field angles are absolute as well.
                    d.add(gid,[text(g,content,[*absolute,pos[2]if len(pos)>2 else 0],COLORS.get(layer(g),'#e8eee5'))],layer(g),points=[absolute])
                elif g.kind.startswith('fp_')or g.kind in ('zone','image'):d.omit(g,'Unsupported footprint graphic')
        elif kind=='zone':
            fills=n.children('filled_polygon');outline=child(n,'polygon');names=[str(value(f,'layer',name))for f in fills]
            if not fills and outline:
                points=[[float(v)for v in p.values[1:]]for p in outline.one('pts').children('xy')]
                d.add(key+':unfilled',[primitive('path',d=path_points(points,True),fill='none',stroke=color,stroke_width=.12,stroke_dasharray='.5 .5')],name,points=points,role='unfilled-zone')
            for j,f in enumerate(fills):
                points=[[float(v)for v in p.values[1:]]for p in f.one('pts').children('xy')]
                # KiCad saved fill polygons use bridged contours for cutouts;
                # even-odd fill retains those voids instead of painting them.
                lname=names[j];d.add(key+':fill:'+str(j),[primitive('path',d=path_points(points,True),fill=COLORS.get(lname,'#ac99ce'),fill_rule='evenodd',opacity=.38)],lname,points=points,role='zone')
        elif kind in ('gr_line','gr_arc','gr_circle','gr_rect','gr_poly'):add_graphic(n)
        elif kind=='gr_text':d.add(key,[text(n,n.values[1],color=color)],name,points=[nums(n,'at')[:2]])
        elif kind.startswith('gr_')or kind in ('image','dimension','target'):d.omit(n,'Unsupported board annotation')
    d.objects.sort(key=lambda o:0 if o.get('role')=='zone'else 2 if o.get('role')in ('pad','via')else 1)
    return d
