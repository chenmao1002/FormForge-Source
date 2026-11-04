"""Pure geometry for explicit paired route sections, independent of the native API."""
import math


def distance(a,b):return math.dist(a,b)
def length(points):return sum(distance(a,b) for a,b in zip(points,points[1:]))
def rounded(p):return [round(v,6) for v in p]


def directions(points):
    result=[]
    for a,b in zip(points,points[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1];n=math.hypot(dx,dy)
        # Rounding the miter coordinates to KiCad's nanometre lattice can
        # leave a one-nanometre diagonal residual. Compare that existing
        # tolerance with a floating-point epsilon, not a tighter binary edge.
        if n<1e-5 or not (abs(dx)<1e-6 or abs(dy)<1e-6 or abs(abs(dx)-abs(dy))<=1e-6+1e-12):
            raise ValueError('Pair spine requires nonzero horizontal/vertical/45 degree segments')
        result.append((dx/n,dy/n))
    for a,b in zip(result,result[1:]):
        if a[0]*b[0]+a[1]*b[1]<-1e-8:raise ValueError('Pair spine bends cannot exceed 90 degrees')
    return result


def offset(points,amount):
    units=directions(points);out=[]
    for i,p in enumerate(points):
        u=units[max(0,i-1)];v=units[min(len(units)-1,i)]
        n=(-u[1],u[0]);m=(-v[1],v[0]);den=1+u[0]*v[0]+u[1]*v[1]
        out.append(rounded([p[0]+amount*(n[0]+m[0])/den,p[1]+amount*(n[1]+m[1])/den]))
    actual=directions(out)
    if any(a[0]*b[0]+a[1]*b[1]<.99999 for a,b in zip(units,actual)):
        raise ValueError('Pair offset collapsed a short spine segment')
    return out


def fanouts(a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];s=min(abs(dx),abs(dy));sx=math.copysign(s,dx);sy=math.copysign(s,dy)
    result=[]
    for bend in ([a[0]+sx,a[1]+sy],[b[0]-sx,b[1]-sy]):
        path=[]
        for p in [a,rounded(bend),b]:
            if not path or distance(p,path[-1])>1e-6:path.append(p)
        if path not in result:result.append(path)
    return result


def simple(points):
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for a,b,c in zip(points,points[1:],points[2:]):
        if abs(cross(a,b,c))<1e-8 and (b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1])<0:return False
    for i,(a,b) in enumerate(zip(points,points[1:])):
        for c,d in list(zip(points,points[1:]))[i+2:]:
            if max(min(a[0],b[0]),min(c[0],d[0]))>min(max(a[0],b[0]),max(c[0],d[0]))+1e-8:continue
            if max(min(a[1],b[1]),min(c[1],d[1]))>min(max(a[1],b[1]),max(c[1],d[1]))+1e-8:continue
            if cross(a,b,c)*cross(a,b,d)<=1e-10 and cross(c,d,a)*cross(c,d,b)<=1e-10:return False
    return True


def metrics(rails,trunks,width):
    totals=[length(r) for r in rails];fans=[length(r)-length(t) for r,t in zip(rails,trunks)]
    gaps=[];overlaps=[]
    for (a,b),(c,d) in zip(zip(trunks[0],trunks[0][1:]),zip(trunks[1],trunks[1][1:])):
        u=((b[0]-a[0])/distance(a,b),(b[1]-a[1])/distance(a,b));v=((d[0]-c[0])/distance(c,d),(d[1]-c[1])/distance(c,d))
        if u[0]*v[0]+u[1]*v[1]<.99999:raise ValueError('Pair trunk directions differ')
        gaps.append(abs((c[0]-a[0])*u[1]-(c[1]-a[1])*u[0])-width)
        along=lambda p:p[0]*u[0]+p[1]*u[1]
        overlaps.append(min(along(b),along(d))-max(along(a),along(c)))
    return dict(lengthsMm=totals,skewMm=abs(totals[0]-totals[1]),fanoutLengthsMm=fans,
                trunkLengthsMm=[length(t) for t in trunks],trunkGapsMm=gaps,parallelOverlapLengthsMm=overlaps,
                simplePaths=all(simple(p) for p in rails))


def within_limits(m,request):
    return not limit_findings(m,request)


def limit_findings(m,request):
    """Explain the same geometry limits used for acceptance, without relaxing them."""
    checks=[('simplePaths',m['simplePaths']),
        ('parallelOverlap',all(v>1e-6 for v in m['parallelOverlapLengthsMm'])),
        ('trunkGap',bool(m['trunkGapsMm']) and all(abs(g-request['gap'])<=3e-6 for g in m['trunkGapsMm'])),
        ('maxLength',max(m['lengthsMm'])<=request['maxLength']+1e-6),
        ('maxSkew',m['skewMm']<=request['maxSkew']+1e-6),
        ('maxFanoutLength',max(m['fanoutLengthsMm'])<=request['maxFanoutLength']+1e-6)]
    return [name for name,passed in checks if not passed]


def verify(contracts,snapshot):
    """Measure native saved segments, never trust metrics supplied by the planner."""
    objects={o['id']:o for o in snapshot['tracks']};pads={p['id']:p for f in snapshot['footprints'] for p in f['pads']}
    results=[]
    for contract in contracts:
        req=contract['request'];rails=[];trunks=[];failures=[]
        for rail in contract['rails']:
            points=[]
            for spec in rail['segments']:
                actual=objects.get(spec['id'])
                if not actual or actual['type']!='segment' or actual['net']!=rail['net'] or actual['layer']!=req['layer'] or abs(actual['width']-req['width'])>1e-6:
                    failures.append('missing_or_changed_segment');continue
                a,b=actual['start'],actual['end']
                if distance(a,spec['start'])+distance(b,spec['end'])>3e-6:
                    if distance(b,spec['start'])+distance(a,spec['end'])>3e-6:failures.append('segment_geometry_changed');continue
                    a,b=b,a
                if points and distance(points[-1],a)>1e-6:failures.append('segment_chain_disconnected')
                if not points:points.append(a)
                points.append(b)
            for endpoint,at in zip(rail['endpoints'],[points[0] if points else None,points[-1] if points else None]):
                pad=pads.get(endpoint['id'])
                if not pad or at is None or pad['net']!=rail['net'] or distance(pad['at'],endpoint['at'])>1e-6 or distance(pad['at'],at)>1e-6:
                    failures.append('endpoint_changed')
            if not any(g['net']==rail['net'] and {e['id'] for e in rail['endpoints']}<=set(g['pads']) for g in snapshot['physicalGroups']):
                failures.append('native_endpoint_connection_unproven')
            rails.append(points)
            start,end=rail['trunkIndices'];trunks.append(points[start:end+1])
        measured=None
        if not failures:
            try:
                measured=metrics(rails,trunks,req['width'])
                if not within_limits(measured,req):failures.append('geometry_limit_exceeded')
            except (ValueError,IndexError,ZeroDivisionError):failures.append('invalid_saved_geometry')
        results.append(dict(name=req['name'],passed=not failures,failures=sorted(set(failures)),metrics=measured))
    return dict(passed=all(r['passed'] for r in results),sections=results,impedanceVerified=False,
                returnCurrentSimulated=False,manufacturingApproved=False,hardwareVerified=False,
                scope='Declared paired sections only; measured track centerline length includes bounded endpoint fanouts, not full net branches or delay simulation.')
