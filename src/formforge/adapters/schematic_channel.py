"""Bounded grid route for an explicitly requested congested schematic channel."""
import heapq
import math
from .schematic_connections import on_segment
from .schematic_fields import crosses


def route(a,b,oa,ob,boxes,wires,pins,cancel,grid=1.27,budget=24000,width=420,height=297):
    start=tuple(round(v/grid) for v in a);end=tuple(round(v/grid) for v in b)
    if any(abs(v/grid-round(v/grid))>1e-6 for v in [*a,*b]):return None
    bounds=(max(12,min(start[0],end[0])-24),max(12,min(start[1],end[1])-24),
            min(math.floor((width-15)/grid),max(start[0],end[0])+40),min(math.floor((height-30)/grid),max(start[1],end[1])+24))
    obstacles={tuple(p['at']) for p in pins}-{a,b}
    wire_ends={p for pair in wires for p in pair}-{a,b}
    cache={}
    def xy(p):return tuple(round(v*grid,6) for v in p)
    def info(p):
        if p in cache:return cache[p]
        pos=xy(p)
        blocked=pos in obstacles or pos in wire_ends or any(v[0]<=pos[0]<=v[2] and v[1]<=pos[1]<=v[3] for v in boxes)
        crossing=[(c,d) for c,d in wires if on_segment(pos,c,d)]
        cache[p]=(blocked,crossing);return cache[p]
    first=(*start,*oa);cost={first:0};parents={};queue=[(0,0,first)];count=0
    while queue and count<budget:
        _,g,state=heapq.heappop(queue)
        if cost.get(state)!=g:continue
        count+=1
        if count%100==0:cancel()
        x,y,dx,dy=state;p=(x,y)
        if p==end and (dx,dy)==(-ob[0],-ob[1]):
            nodes=[p]
            while state in parents:state=parents[state];nodes.append(state[:2])
            nodes.reverse();result=[]
            for pos in map(xy,nodes):
                while len(result)>1 and (result[-1][0]-result[-2][0])*(pos[1]-result[-1][1])==(result[-1][1]-result[-2][1])*(pos[0]-result[-1][0]):result.pop()
                result.append(pos)
            return result
        _,current_wires=info(p)
        for nx,ny in ((1,0),(-1,0),(0,1),(0,-1)):
            if p==start and (nx,ny)!=oa:continue
            if (nx,ny)==(-dx,-dy):continue
            if current_wires and p!=start and (nx,ny)!=(dx,dy):continue
            q=(x+nx,y+ny)
            if not(bounds[0]<=q[0]<=bounds[2] and bounds[1]<=q[1]<=bounds[3]):continue
            blocked,crossing=info(q)
            if blocked and q!=end:continue
            if any((c[0]==d[0] and nx==0) or (c[1]==d[1] and ny==0) for c,d in [*current_wires,*crossing]):continue
            c,d=xy(p),xy(q)
            if any(crosses(box,c,d) for box in boxes):continue
            turn=(nx,ny)!=(dx,dy)
            newg=g+1+4*turn+40*len(crossing)
            key=(*q,nx,ny)
            if newg>=cost.get(key,math.inf):continue
            cost[key]=newg;parents[key]=state
            heapq.heappush(queue,(newg+abs(q[0]-end[0])+abs(q[1]-end[1]),newg,key))
    return None
