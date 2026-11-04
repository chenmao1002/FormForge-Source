"""Draw embedded native symbols and document objects without running KiCad."""
import math,re
from .primitives import Drawing,Node,child,value,nums,identity,primitive,line,text,graphic,matrix,transform,point,hidden


def net_label(drawing, n, key, at):
    """Readable anchored labels; text metrics remain an approximation of KiCad."""
    content = str(n.values[1]); kind = n.kind
    if hidden(n) or (child(n,'effects') and hidden(child(n,'effects'))):return True
    font = child(child(n,'effects'),'font')
    size = nums(font,'size',[1.27,1.27])[1]
    width = max(size, len(content)*size*.85)
    height = size*.8; tip = height*.65
    if kind != 'global_label':return False
    # Native text angles are 0/90; horizontal justification chooses the
    # anchor side. KiCad normalizes 180/270 text angles even in hand-written files.
    justify = child(child(n,'effects'),'justify')
    spin = (at[2] if len(at)>2 else 0)%180
    if justify and 'right' in justify.values:spin += 180
    m = matrix([*at[:2],spin])
    shape = value(n,'shape','input')
    if shape not in {'input','output','bidirectional','tri_state','passive'}:
        drawing.omit(n,'Unsupported label shape');return False
    span = width+2*size
    points = [(0,0),(tip,-height),(span-tip,-height),(span,0),(span-tip,height),(tip,height)]
    if shape == 'input':
        points = [(0,0),(tip,-height),(span,-height),(span,height),(tip,height)]
    elif shape == 'output':
        points = [(0,-height),(span-tip,-height),(span,0),(span-tip,height),(0,height)]
    elif shape == 'passive':
        points = [(0,-height),(span,-height),(span,height),(0,height)]
    outline = primitive('path',d=' '.join(('M' if i==0 else 'L')+f'{x} {y}' for i,(x,y) in enumerate(points))+' Z',
                        fill='none',stroke='#88434c',stroke_width=.1524)
    drawing.add(key+':outline',[outline],transform_=transform(m),points=[point(m,p) for p in points],role=kind)
    pos = point(m,[span/2,0])
    glyph = text(n,content,[*pos,(at[2] if len(at)>2 else 0)%180],color='#88434c')
    if glyph:
        glyph['attrs']['text-anchor']='middle'
        drawing.add(key,[glyph],role=kind)
    # Native glyph metrics and cross-reference substitution require native export.
    for prop in n.children('property'):
        if len(prop.values)>2 and prop.values[1]=='Intersheetrefs':
            glyph=text(prop,prop.values[2],color='#315ba5')
            if glyph and '${' in str(prop.values[2]):drawing.omit(n,'Intersheet reference expansion requires native export')
            elif glyph:drawing.add(key+':references',[glyph])
    return True


def build(page,references=None,reference_style=None):
    drawing=Drawing();tree=page['tree']
    lib=child(tree,'lib_symbols');definitions={str(n.values[1]):n for n in lib.children('symbol')} if lib else {}
    for index,n in enumerate(tree.values):
        if not isinstance(n,Node):continue
        key=identity(n,f'{n.kind}:{index}');at=nums(n,'at',[0,0,0]);kind=n.kind
        if kind=='symbol':
            definition=definitions.get(value(n,'lib_id'))
            if definition is None or definition.children('extends'):
                drawing.omit(n,'Missing or unresolved embedded symbol');continue
            unit=int(value(n,'unit',1));body=int(value(n,'body_style',value(n,'convert',1)));m=matrix(at,True,value(n,'mirror'))
            shapes=[];local_points=[]
            for part in definition.children('symbol'):
                match=re.search(r'_(\d+)_(\d+)$',str(part.values[1]))
                if not match or int(match[1])not in (0,unit) or int(match[2])not in (0,body):continue
                for g in part.values:
                    if not isinstance(g,Node):continue
                    if g.kind=='pin':
                        if hidden(g):continue
                        p=nums(g,'at',[0,0,0]);length=float(value(g,'length',0));a=math.radians(p[2]);end=[p[0]+length*math.cos(a),p[1]+length*math.sin(a)]
                        shapes.append(line(p,end,.15,'#88434c'));local_points.extend([p[:2],end])
                        # Pin labels are document text, avoiding reflected glyphs
                        # when the library-coordinate group reverses Y.
                        number=value(g,'number','');name=value(g,'name','');q=point(m,p);e=point(m,end)
                        pin_numbers=child(definition,'pin_numbers');pin_names=child(definition,'pin_names')
                        horizontal=abs(q[0]-e[0])>=abs(q[1]-e[1])
                        if not(pin_numbers and hidden(pin_numbers)):
                            pos=[(q[0]+e[0])/2,(q[1]+e[1])/2-(.6 if horizontal else 0),0]
                            if not horizontal:pos[0]-=.6
                            drawing.add(key+':pin:'+str(number)+':number',[text(g,number,pos,size=.85)])
                        if name not in ('','~') and not(pin_names and hidden(pin_names)):
                            dx,dy=e[0]-q[0],e[1]-q[1];norm=math.hypot(dx,dy) or 1
                            offset=float(value(pin_names,'offset',.5)) if pin_names else .5
                            pos=[e[0]+dx/norm*(offset+max(len(str(name))*.25,.5)),e[1]+dy/norm*(offset+.5),0]
                            drawing.add(key+':pin:'+str(number)+':name',[text(g,name,pos,size=.9)])
                        if len(g.values)>2 and g.values[2] not in ('line','inverted','clock','inverted_clock'):
                            drawing.omit(g,'Pin decoration not yet represented')
                        if len(g.values)>2 and g.values[2] in ('inverted','inverted_clock'):
                            shapes.append(primitive('circle',cx=end[0],cy=end[1],r=.45,fill='#fff4c9',stroke='#88434c',stroke_width=.15))
                        if len(g.values)>2 and g.values[2] in ('clock','inverted_clock'):
                            ux,uy=math.cos(a),math.sin(a);v=[-uy*.6,ux*.6]
                            shapes.append(primitive('path',d=f'M {end[0]+v[0]} {end[1]+v[1]} L {end[0]+ux*.9} {end[1]+uy*.9} L {end[0]-v[0]} {end[1]-v[1]}',fill='none',stroke='#88434c',stroke_width=.15))
                    elif g.kind in ('polyline','rectangle','circle','arc','bezier'):
                        shapes.append(graphic(g,library=True))
                        for name in ('start','end','center','mid'):
                            p=nums(g,name)
                            if p:local_points.append(p)
                        for pts in g.children('pts'):local_points.extend([float(v)for v in p.values[1:]]for p in pts.children('xy'))
                    elif g.kind in ('text','text_box'):drawing.omit(g,'Library body text needs native typography')
            drawing.add(key,shapes,transform_=transform(m),points=[point(m,p)for p in local_points]or[at[:2]],role='symbol')
            for p in n.children('property'):
                if len(p.values)<3 or p.values[1]not in ('Reference','Value'):continue
                content=p.values[2]
                if p.values[1]=='Reference':
                    from ..adapters.schematic_references import display_reference
                    content=display_reference(definition,(references or {}).get(key,content),unit,reference_style)
                field_at=nums(p,'at',[0,0,0])
                # Saved field angle is relative to the symbol's orientation.
                field_at[2]=(field_at[2]+at[2])%180
                drawing.add(key+':field:'+p.values[1],[text(p,content,field_at)])
        elif kind in ('wire','bus'):
            points=[[float(v)for v in p.values[1:]]for p in n.one('pts').children('xy')]
            stroke=child(n,'stroke');width=float(value(stroke,'width',0)) if stroke else 0
            drawing.add(key,[line(a,b,width or (.3 if kind=='bus' else .1524),'#3459a0' if kind=='bus' else '#287749')for a,b in zip(points,points[1:])],points=points,role=kind)
        elif kind=='junction':drawing.add(key,[primitive('circle',cx=at[0],cy=at[1],r=(float(value(n,'diameter',0))or .8)/2,fill='#287749')],points=[at[:2]])
        elif kind=='no_connect':drawing.add(key,[line([at[0]-.5,at[1]-.5],[at[0]+.5,at[1]+.5],.15,'#315ba5'),line([at[0]-.5,at[1]+.5],[at[0]+.5,at[1]-.5],.15,'#315ba5')],points=[at[:2]])
        elif kind in ('label','global_label','hierarchical_label','text'):
            if kind in ('global_label','hierarchical_label') and net_label(drawing,n,key,at):continue
            content=n.values[1];pos=[at[0],at[1]-.7,*at[2:]]
            if kind in ('label','hierarchical_label'):pos[2]=pos[2]%180
            drawing.add(key,[text(n,content,pos)],points=[[at[0],at[1]],[at[0]+len(str(content))*.8,at[1]-2]],role=kind)
            if kind in ('global_label','hierarchical_label'):
                drawing.omit(n,'Label text/anchor shown; unsupported label outline')
        elif kind=='sheet':
            size=nums(n,'size',[10,10]);shapes=[primitive('rect',x=at[0],y=at[1],width=size[0],height=size[1],fill='none',stroke='#88434c',stroke_width=.2)]
            shapes += [text(p,p.values[2])for p in n.children('property')]
            drawing.add(key,shapes,points=[at[:2],[at[0]+size[0],at[1]+size[1]]],role='sheet')
            for i,pin in enumerate(n.children('pin')):drawing.add(key+':sheet-pin:'+str(i),[text(pin,pin.values[1])],points=[nums(pin,'at')[:2]])
        elif kind in ('polyline','rectangle','circle','arc','bezier'):
            points=[nums(n,p) for p in ('start','end','center','mid') if nums(n,p)]
            for pts in n.children('pts'):points.extend([float(v) for v in p.values[1:]] for p in pts.children('xy'))
            drawing.add(key,[graphic(n,color='#315ba5')],points=points)
        elif kind not in ('version','generator','generator_version','uuid','paper','title_block','lib_symbols','embedded_fonts','instances','sheet_instances'):
            drawing.omit(n,'Unsupported document graphic')
    # Pins are behind bodies so native background fills do not cover labels.
    drawing.objects.sort(key=lambda o:0 if o.get('role')=='symbol' else 1)
    return drawing
