"""Small, data-only SVG vocabulary. No executable SVG or external resources."""
import math,re
from ..adapters.native_syntax import Node


def child(n, name):
    return next(iter(n.children(name)), None) if n else None


def value(n, name, default=None):
    c=child(n,name)
    return c.values[1] if c and len(c.values)>1 else default


def nums(n, name, default=()):
    c=child(n,name)
    return [float(v) for v in c.values[1:] if not isinstance(v,Node)] if c else list(default)


def hidden(n):
    return 'hide' in n.values or value(n,'hide')=='yes'


def identity(n, fallback):
    return str(value(n,'uuid',fallback))


def primitive(tag, **attrs):
    return dict(tag=tag,attrs={k.replace('_','-'):v for k,v in attrs.items()})


def line(a,b,width=.15,color='#287749'):
    return primitive('line',x1=a[0],y1=a[1],x2=b[0],y2=b[1],stroke=color,stroke_width=width,stroke_linecap='round')


def text(n, content, at=None, color='#24523b', size=None):
    effects=child(n,'effects');font=child(effects,'font') if effects else None
    if hidden(n) or (effects and hidden(effects)):return None
    sizes=nums(font,'size',[1.27,1.27]) if font else [1.27,1.27]
    pos=at or nums(n,'at',[0,0,0]);angle=pos[2] if len(pos)>2 else 0
    justify=child(effects,'justify') if effects else None
    anchor='start' if justify and 'left' in justify.values else 'end' if justify and 'right' in justify.values else 'middle'
    p=primitive('text',x=pos[0],y=pos[1],fill=color,font_size=size or sizes[1],font_family='Arial, sans-serif',text_anchor=anchor,dominant_baseline='central',transform=f'rotate({-angle} {pos[0]} {pos[1]})')
    p['text']=str(content)
    return p


def path_points(points, close=False):
    return ' '.join(('M' if i==0 else 'L')+f'{p[0]:.8g} {p[1]:.8g}' for i,p in enumerate(points))+(' Z' if close else '')


def arc_path(start,mid,end):
    """Circle through three points, including the side containing the midpoint."""
    ax,ay=start;bx,by=mid;cx,cy=end
    det=2*(ax*(by-cy)+bx*(cy-ay)+cx*(ay-by))
    if abs(det)<1e-12:return path_points([start,mid,end])
    a=ax*ax+ay*ay;b=bx*bx+by*by;c=cx*cx+cy*cy
    x=(a*(by-cy)+b*(cy-ay)+c*(ay-by))/det
    y=(a*(cx-bx)+b*(ax-cx)+c*(bx-ax))/det
    t0=math.atan2(ay-y,ax-x);tm=math.atan2(by-y,bx-x);t1=math.atan2(cy-y,cx-x)
    sweep=1 if (tm-t0)%(2*math.pi)<(t1-t0)%(2*math.pi) else 0
    extent=(t1-t0)%(2*math.pi) if sweep else (t0-t1)%(2*math.pi)
    r=math.hypot(ax-x,ay-y)
    return f'M {ax} {ay} A {r} {r} 0 {int(extent>math.pi)} {sweep} {cx} {cy}'


def graphic(n,color='#88434c',library=False):
    kind=n.kind.removeprefix('fp_').removeprefix('gr_')
    stroke=child(n,'stroke');width=float(value(stroke,'width',.15)) if stroke else float(value(n,'width',.15))
    width=width if width>0 else .1524
    fillnode=child(n,'fill');filltype=value(fillnode,'type') if fillnode else value(n,'fill','none')
    fill='#fff4c9' if library and filltype=='background' else color if filltype in ('solid','outline','yes') else 'none'
    attrs=dict(stroke=color,stroke_width=width,fill=fill,stroke_linejoin='round',stroke_linecap='round')
    if kind in ('line','segment'):return line(nums(n,'start'),nums(n,'end'),width,color)
    if kind in ('polyline','poly'):
        pts=child(n,'pts');points=[[float(v)for v in p.values[1:]] for p in pts.children('xy')] if pts else []
        return primitive('path',d=path_points(points,kind=='poly' or fill!='none'),**attrs)
    if kind in ('rectangle','rect'):
        a=nums(n,'start');b=nums(n,'end')
        return primitive('rect',x=min(a[0],b[0]),y=min(a[1],b[1]),width=abs(a[0]-b[0]),height=abs(a[1]-b[1]),**attrs)
    if kind=='circle':
        a=nums(n,'center');r=value(n,'radius')
        if r is None:r=math.dist(a,nums(n,'end'))
        return primitive('circle',cx=a[0],cy=a[1],r=float(r),**attrs)
    if kind=='arc':
        result=primitive('path',d=arc_path(nums(n,'start'),nums(n,'mid'),nums(n,'end')),**attrs)
        # Include cardinal extrema only when they lie on the native arc.
        a=nums(n,'start');b=nums(n,'mid');c=nums(n,'end')
        det=2*(a[0]*(b[1]-c[1])+b[0]*(c[1]-a[1])+c[0]*(a[1]-b[1]))
        if abs(det)>1e-12:
            aa=sum(v*v for v in a);bb=sum(v*v for v in b);cc=sum(v*v for v in c)
            x=(aa*(b[1]-c[1])+bb*(c[1]-a[1])+cc*(a[1]-b[1]))/det;y=(aa*(c[0]-b[0])+bb*(a[0]-c[0])+cc*(b[0]-a[0]))/det;r=math.dist(a,[x,y])
            start=math.atan2(a[1]-y,a[0]-x);mid=math.atan2(b[1]-y,b[0]-x);end=math.atan2(c[1]-y,c[0]-x)
            forward=(mid-start)%(2*math.pi)<(end-start)%(2*math.pi)
            extent=(end-start)%(2*math.pi)if forward else(start-end)%(2*math.pi)
            result['bounds']=[a,b,c]+[[x+r*math.cos(t),y+r*math.sin(t)]for t in (0,math.pi/2,math.pi,3*math.pi/2)if ((t-start)if forward else(start-t))%(2*math.pi)<=extent+1e-9]
        return result
    if kind=='bezier':
        points=[[float(v)for v in p.values[1:]]for p in n.one('pts').children('xy')]
        if len(points)!=4:return None
        return primitive('path',d='M '+' '.join(map(str,points[0]))+' C '+' '.join(str(v)for p in points[1:]for v in p),**attrs)
    return None


def matrix(at, library=False, mirror=None):
    angle=math.radians(at[2] if len(at)>2 else 0);c=math.cos(angle);s=math.sin(angle)
    # KiCad library Y is up; document/PCB Y is down. PCB local axes already Y-down.
    a,b,c2,d=(c,-s,-s,-c) if library else (c,-s,s,c)
    if mirror=='x':b=-b;d=-d
    if mirror=='y':a=-a;c2=-c2
    return [round(v,10)for v in (a,b,c2,d,at[0],at[1])]


def transform(m):return 'matrix('+' '.join(map(str,m))+')'


def point(m,p):return [m[0]*p[0]+m[2]*p[1]+m[4],m[1]*p[0]+m[3]*p[1]+m[5]]


class Drawing:
    def __init__(self):self.objects=[];self.omitted=[];self.points=[]
    def omit(self,n,reason):self.omitted.append(dict(id=identity(n,n.kind),type=n.kind,reason=reason))
    def add(self,id,primitives,layer='document',transform_=None,points=(),role=None):
        primitives=[p for p in primitives if p]
        if not primitives:return
        row=dict(id=id,layer=layer,primitives=primitives)
        if transform_:row['transform']=transform_
        if role:row['role']=role
        self.objects.append(row);self.points.extend(points)
        m=[1,0,0,1,0,0]
        if transform_:
            values=[float(v)for v in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?',transform_)]
            if transform_.startswith('matrix'):m=values
            elif transform_.startswith('translate'):
                a=math.radians(values[2]if len(values)>2 else 0);m=[math.cos(a),math.sin(a),-math.sin(a),math.cos(a),*values[:2]]
        for p in primitives:
            attrs=p['attrs'];tag=p['tag'];extra=list(p.get('bounds',[]))
            if tag in ('circle','ellipse'):
                x,y=attrs['cx'],attrs['cy'];rx=attrs.get('rx',attrs.get('r'));ry=attrs.get('ry',attrs.get('r'));extra.extend([[x-rx,y-ry],[x+rx,y-ry],[x+rx,y+ry],[x-rx,y+ry]])
            elif tag=='rect':
                x,y,w,h=(attrs.get(k,0)for k in ('x','y','width','height'));extra.extend([[x,y],[x+w,y],[x+w,y+h],[x,y+h]])
            self.points.extend(point(m,p)for p in extra)
    def bounds(self):
        if not self.points:return [0,0,100,100]
        xs,ys=zip(*self.points);margin=2
        return [min(xs)-margin,min(ys)-margin,max(max(xs)-min(xs)+2*margin,1),max(max(ys)-min(ys)+2*margin,1)]
