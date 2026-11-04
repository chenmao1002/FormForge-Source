"""Presentation-only wire measures. Labels never create geometric shortcuts."""
import heapq,math
from collections import defaultdict

EPS=1e-7


def on(p,a,b):
    return min(a[0],b[0])-EPS<=p[0]<=max(a[0],b[0])+EPS and min(a[1],b[1])-EPS<=p[1]<=max(a[1],b[1])+EPS and abs((p[0]-a[0])*(b[1]-a[1])-(p[1]-a[1])*(b[0]-a[0]))<=EPS


def cross(a,b):return a[0]*b[1]-a[1]*b[0]


def wire_quality(objects,budget=500000):
    segments=[(o['id'],tuple(a),tuple(b)) for o in objects if o['type']=='wire' for a,b in zip(o['points'],o['points'][1:])]
    pins=[dict(objectId=o['id'],reference=o.get('reference'),pin=p['number'],at=tuple(p['at'])) for o in objects if o['type']=='symbol' and not o.get('reference','').startswith('#') for p in o.get('pinPositions',[])]
    unavailable=[o['id'] for o in objects if o['type']=='symbol' and not o.get('reference','').startswith('#') and 'pinPositions' not in o]
    anchors={p['at'] for p in pins}|{tuple(o['at'][:2]) for o in objects if o['type'] in {'label','global_label','hierarchical_label','junction'} and o.get('at')}
    anchors.update(p for _,a,b in segments for p in (a,b))
    result=dict(version=1,status='available',scope='Known geometry on the selected sheet; no label-name or cross-sheet shortcuts.',electricalVerdict=False,
        totalLengthMm=round(sum(math.dist(a,b) for _,a,b in segments),6),segmentCount=len(segments),zeroLengthSegments=sum(math.dist(a,b)<=EPS for _,a,b in segments),
        bendCount=None,turnsByAngleDeg=None,nonConnectingCrossings=None,connectedCrossings=None,collinearOverlaps=None,pinPairPaths=None,
        totalDetourEstimateMm=None,coverage=dict(unavailableSymbolIds=unavailable),
        note='Detour is shortest physical pin-pair length above an orthogonal Manhattan baseline, or straight-line baseline for diagonal components. It is not proof that a route can be shortened.')
    if len(segments)*len(anchors)+len(segments)*(len(segments)-1)//2>budget:
        result.update(status='partial',reason='wire_geometry_budget');return result
    nonconnected=defaultdict(list);connected=defaultdict(list);overlaps=[]
    for i,(first,a,b) in enumerate(segments):
        v=(b[0]-a[0],b[1]-a[1])
        for second,c,d in segments[i+1:]:
            w=(d[0]-c[0],d[1]-c[1]);delta=(c[0]-a[0],c[1]-a[1]);den=cross(v,w)
            if abs(den)<=EPS:
                if math.dist(a,b)<=EPS or math.dist(c,d)<=EPS or abs(cross(delta,v))>EPS:continue
                axis=0 if abs(v[0])>=abs(v[1]) else 1
                extent=min(max(a[axis],b[axis]),max(c[axis],d[axis]))-max(min(a[axis],b[axis]),min(c[axis],d[axis]))
                if extent>EPS:overlaps.append(dict(wireIds=[first,second],lengthMm=round(extent*math.dist(a,b)/abs(v[axis]),6)))
                continue
            t=cross(delta,w)/den;u=cross(delta,v)/den
            if not EPS<t<1-EPS or not EPS<u<1-EPS:continue
            p=tuple(round(a[k]+t*v[k],6) for k in (0,1))
            (connected if p in anchors else nonconnected)[p].append([first,second])
    adj=defaultdict(dict)
    for _,a,b in segments:
        points=sorted((p for p in anchors if on(p,a,b)),key=lambda p:math.dist(a,p))
        for p,q in zip(points,points[1:]):
            length=math.dist(p,q)
            if length<=EPS:continue
            adj[p][q]=min(adj[p].get(q,math.inf),length);adj[q][p]=min(adj[q].get(p,math.inf),length)
    turns=defaultdict(int)
    for p,neighbors in adj.items():
        if len(neighbors)!=2:continue
        a,b=list(neighbors);v=(a[0]-p[0],a[1]-p[1]);w=(b[0]-p[0],b[1]-p[1])
        if abs(cross(v,w))<=EPS:continue
        angle=180-math.degrees(math.acos(max(-1,min(1,sum(v[k]*w[k] for k in (0,1))/math.dist(a,p)/math.dist(b,p)))))
        turns[str(round(angle,3))]+=1
    components=[];seen=set()
    for p in adj:
        if p in seen:continue
        group=set();stack=[p]
        while stack:
            q=stack.pop()
            if q in group:continue
            group.add(q);stack.extend(adj[q].keys()-group)
        components.append(group);seen.update(group)
    paths=[];excluded=defaultdict(int)
    for group in components:
        terminals=[p for p in pins if p['at'] in group]
        if len(terminals)!=2:
            excluded['fewerThanTwoPins' if len(terminals)<2 else 'moreThanTwoPins']+=1;continue
        first,second=terminals;start,end=first['at'],second['at'];dist={start:0.};previous={};queue=[(0.,start)]
        while queue:
            length,p=heapq.heappop(queue)
            if length!=dist[p]:continue
            if p==end:break
            for q,step in adj[p].items():
                candidate=length+step
                if candidate<dist.get(q,math.inf):dist[q]=candidate;previous[q]=p;heapq.heappush(queue,(candidate,q))
        diagonal=any(abs(p[0]-q[0])>EPS and abs(p[1]-q[1])>EPS for p in group for q in adj[p])
        baseline=math.dist(start,end) if diagonal else sum(abs(start[k]-end[k]) for k in (0,1))
        path=[end]
        while path[-1]!=start:path.append(previous[path[-1]])
        paths.append(dict(pins=[first,second],points=list(reversed(path)),lengthMm=round(dist[end],6),baseline='straight-line' if diagonal else 'manhattan',baselineMm=round(baseline,6),detourEstimateMm=round(max(0,dist[end]-baseline),6)))
    result.update(bendCount=sum(turns.values()),turnsByAngleDeg=dict(sorted(turns.items())),
        nonConnectingCrossings=[dict(at=p,wirePairs=items) for p,items in sorted(nonconnected.items())],
        connectedCrossings=[dict(at=p,wirePairs=items) for p,items in sorted(connected.items())],collinearOverlaps=overlaps,pinPairPaths=paths,
        totalDetourEstimateMm=round(sum(p['detourEstimateMm'] for p in paths),6),
        coverage=dict(unavailableSymbolIds=unavailable,wireComponents=len(components),twoPinComponents=len(paths),excludedComponents=dict(excluded)))
    return result
