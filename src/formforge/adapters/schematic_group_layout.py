"""Bounded pin-aware refinement inside existing non-overlapping layout cells."""
import math
from collections import defaultdict
from .schematic import location, properties
from .native_syntax import parse
from .schematic_transform import rotate_symbol


def refine(page, symbols, nets, groups, allocations, cells, cancel):
    variants={}
    for ref,(node,pins) in symbols.items():
        choices=[]
        # Preserve multi-pin source orientation: connectivity alone does not
        # identify intended signal flow or supply/return roles.
        angles=(int(location(node)[2]),) if len(pins)>2 else (int(location(node)[2]),0,90,180,270)
        for angle in angles:
            if any(c[0]==angle for c in choices):continue
            if angle==location(node)[2]:choices.append((angle,node,pins));continue
            edits,rotated=rotate_symbol(page,node,angle)
            text=node.raw
            for a,b,replacement in sorted(edits,reverse=True):text=text[:a-node.start]+replacement+text[b-node.start:]
            choices.append((angle,parse(text),rotated))
        variants[ref]=choices
    positions={ref:list(at) for items in allocations for ref,at in items}
    slots={ref:(list(at),cells[ref]) for ref,at in positions.items()}
    assignments={ref:ref for ref in symbols};selected={ref:0 for ref in symbols}
    # Score only explicit group-local connections. Fanout normalization prevents
    # common supply/return nets from outweighing short signal branches.
    def edges(group):
        by_net=defaultdict(list)
        for ref in group:
            for p in symbols[ref][1]:by_net[nets[ref,p['number']]].append((ref,p['number']))
        return [(a,b,1/(len(ps)-1)) for ps in by_net.values() if len(ps)>1
                for i,a in enumerate(ps) for b in ps[i+1:] if a[0]!=b[0]]
    def points(group):
        result={}
        for ref in group:
            _,node,pins=variants[ref][selected[ref]];origin=location(node)
            dx,dy=[round((positions[ref][i]-origin[i])/1.27)*1.27 for i in (0,1)]
            for p in pins:result[ref,p['number']]=([p['at'][0]+dx,p['at'][1]+dy],p['orientation'])
        return result
    def cost(group,links):
        pts=points(group);total=0
        for a,b,weight in links:
            pa,aa=pts[a];pb,ab=pts[b];vx,vy=pb[0]-pa[0],pb[1]-pa[1];length=abs(vx)+abs(vy)
            # Penalize pins facing away from their peers and unnecessary jogs.
            penalty=0
            for angle,dx,dy in [(aa,vx,vy),(ab,-vx,-vy)]:
                outward=(-math.cos(math.radians(angle)),math.sin(math.radians(angle)))
                along=dx*outward[0]+dy*outward[1]
                penalty+=6 if along<-.01 else 0
            total+=weight*(length+penalty+min(abs(vx),abs(vy))*.5)
        return total
    def fits(ref):
        center,(w,h)=slots[assignments[ref]];_,node,pins=variants[ref][selected[ref]];origin=location(node)
        dx,dy=[round((positions[ref][i]-origin[i])/1.27)*1.27 for i in (0,1)]
        bounds=[]
        for p in pins:
            x,y=p['at'][0]+dx,p['at'][1]+dy;length=len(nets[ref,p['number']])*.85+3
            angle=math.radians(p['orientation']);ox,oy=-math.cos(angle),math.sin(angle)
            bounds.extend([(x-2,y-2),(x+2,y+2),(x+ox*length,y+oy*length)])
        for field in node.children('property'):
            if field.values[1] not in {'Reference','Value'}:continue
            at=location(field);half=len(str(field.values[2]))*.5
            bounds.extend([(at[0]+dx-half,at[1]+dy-2),(at[0]+dx+half,at[1]+dy+2)])
        return all(center[0]-w/2+1<=x<=center[0]+w/2-1 and center[1]-h/2+1<=y<=center[1]+h/2-1 for x,y in bounds)
    before={ref:(positions[ref][:],int(location(symbols[ref][0])[2])) for ref in symbols};scores=[]
    for group in groups:
        links=edges(group);initial=cost(group,links);best=initial
        # Two deterministic passes; finite candidates, no unbounded solve.
        for _ in range(2):
            for ref in group:
                cancel();saved=(selected[ref],positions[ref][:]);choice=saved
                center=slots[assignments[ref]][0]
                for index in range(len(variants[ref])):
                    selected[ref]=index
                    for offset in [(0,0),(-2.54,0),(2.54,0),(0,-2.54),(0,2.54),(-5.08,0),(5.08,0),(0,-5.08),(0,5.08)]:
                        positions[ref]=[center[i]+offset[i] for i in (0,1)]
                        if not fits(ref):continue
                        candidate=cost(group,links)
                        if candidate<best-1e-6:best=candidate;choice=(index,positions[ref][:])
                selected[ref],positions[ref]=choice
            for i,a in enumerate(group):
                for b in group[i+1:]:
                    cancel();old=(assignments[a],assignments[b],positions[a][:],positions[b][:])
                    assignments[a],assignments[b]=assignments[b],assignments[a]
                    positions[a],positions[b]=slots[assignments[a]][0][:],slots[assignments[b]][0][:]
                    candidate=cost(group,links) if fits(a) and fits(b) else math.inf
                    if candidate<best-1e-6:best=candidate
                    else:assignments[a],assignments[b],positions[a],positions[b]=old
        scores.append(dict(references=group,before=round(initial,6),after=round(best,6)))
    changes=[]
    for ref in symbols:
        angle,node,pins=variants[ref][selected[ref]]
        if (positions[ref],angle)!=before[ref]:
            changes.append(dict(reference=ref,beforeAt=before[ref][0],afterAt=positions[ref],beforeRotation=before[ref][1],afterRotation=angle))
        symbols[ref]=(node,pins)
    allocations=[[ (ref,positions[ref]) for ref,_ in items] for items in allocations]
    return allocations,dict(policy='bounded-group-pin-geometry-v1',groups=scores,changes=changes,
        scope='Declared groups only; weighted pin distance, facing and cell envelopes. Not signal-role inference, exact glyph collision or global optimality.')
