"""Bounded functional-page assignment from actual block envelopes and native nets."""
import math
from collections import defaultdict
from .schematic import fail

GRID=1.27
PAPERS={'A4':(297,210),'A3':(420,297),'A2':(594,420),'A1':(841,594),'A0':(1189,841)}


def plan_pages(groups,envelopes,nets,repetitions,spec,paper,limit,cancel):
    by_id={g['id']:g for g in groups};order={g['id']:i for i,g in enumerate(groups)}
    parent={i:i for i in by_id}
    def root(i):
        while parent[i]!=i:i=parent[i]
        return i
    for together in spec['samePage']:
        if len(together)<2 or len(set(together))!=len(together) or any(i not in by_id for i in together):fail('同页约束引用未知或重复的功能块。')
        for i in together[1:]:parent[root(i)]=root(together[0])
    units=defaultdict(list)
    for g in groups:units[root(g['id'])].append(g['id'])
    units=list(units.values());unit_index={gid:i for i,ids in enumerate(units) for gid in ids};pins={}
    for i,ids in enumerate(units):
        pages={by_id[g].get('page') for g in ids}-{None}
        if len(pages)>1:fail('同页约束与明确功能页归属冲突。')
        pins[i]=next(iter(pages)) if pages else None
        if sum(len(by_id[g]['references']) for g in ids)>limit:fail('不可拆分的同页功能块超过每页器件上限。')
    named=list(dict.fromkeys(g['page'] for g in groups if g.get('page')))
    if len(named)>spec['maxPages']:fail('明确功能页数量超过maxPages。')
    patterns={g['id']:('single',g['id']) for g in groups};columns={g['id']:1 for g in groups}
    for repetition in repetitions:
        for gid in repetition['groups']:patterns[gid]=('repeat',repetition['id']);columns[gid]=repetition['columns']
    sizes={}
    for pattern in dict.fromkeys(patterns.values()):
        ids=[i for i in by_id if patterns[i]==pattern]
        w=max(max(envelopes[i][2]-envelopes[i][0],len(by_id[i]['name'])*1.52*.85) for i in ids)
        h=max(envelopes[i][3]-envelopes[i][1] for i in ids)
        for gid in ids:sizes[gid]=(math.ceil((w+10.16)/GRID)*GRID,math.ceil((h+13.97)/GRID)*GRID)
    width,height=PAPERS[paper]
    def pack(ids):
        if sum(len(by_id[i]['references']) for i in ids)>limit:return None
        x,y,rowheight=80.01,25.4,0.;row_pattern=None;row_count=0;positions={}
        for gid in sorted(ids,key=order.get):
            w,h=sizes[gid];pattern=patterns[gid]
            if row_pattern!=pattern or row_count>=columns[gid] or x+w>width-15:
                if rowheight:y+=rowheight
                x=80.01;rowheight=0.;row_count=0;row_pattern=pattern
            if x+w>width-15 or y+h>height-30:return None
            env=envelopes[gid]
            positions[gid]=[round(round((v-env[k])/GRID)*GRID,6) for k,v in enumerate((x,y))]
            x+=w;rowheight=max(rowheight,h);row_count+=1
        return positions
    for ids in units:
        if pack(ids) is None:fail('本轮有界摆放未容纳不可拆分功能块；请调整约束或纸张，不缩小文字，也不据此认定无解。')
    memberships=defaultdict(set)
    for g in groups:
        for ref in g['references']:
            for net in nets[ref].values():
                if net!='(isolated)' and not net.startswith('unconnected-'):memberships[net].add(g['id'])
    weights={n:1. for n in memberships}
    weighted=[]
    for item in spec['netWeights']:
        if item['net'] not in memberships or item['net'] in weighted:fail('跨页权重必须引用唯一的实际原生网络。')
        weights[item['net']]=item['weight'];weighted.append(item['net'])
    budget=spec['maxCandidates'];evaluated=0
    def evaluate(assignment):
        nonlocal evaluated
        cancel();evaluated+=1;pages=defaultdict(list)
        for i,p in assignment.items():pages[p].extend(units[i])
        if len(pages)>spec['maxPages']:return None
        placements={}
        for ids in pages.values():
            packed=pack(ids)
            if packed is None:return None
            placements.update(packed)
        cut=sum(weights[n]*(len({assignment[unit_index[g]] for g in ids})-1) for n,ids in memberships.items())
        return (len(pages),round(cut,6)),placements
    def first_fit(sequence):
        assignment={i:p for i,p in pins.items() if p is not None};used=list(named)
        if any(pack([g for i,p in assignment.items() if p==page for g in units[i]]) is None for page in used):return None
        for i in sequence:
            if i in assignment:continue
            target=None
            for page in used:
                ids=[g for j,p in assignment.items() if p==page for g in units[j]]+units[i]
                if pack(ids) is not None:target=page;break
            if target is None:
                if len(used)>=spec['maxPages']:return None
                ordinal=1
                while f'{spec["pagePrefix"]} {ordinal}' in used:ordinal+=1
                target=f'{spec["pagePrefix"]} {ordinal}';used.append(target)
            assignment[i]=target
        return assignment
    natural=list(range(len(units)))
    # Try a small deterministic set of seeds; an unsuccessful seed is not proof of no solution.
    sequences=[natural,sorted(natural,key=lambda i:(-sum(len(by_id[g]['references']) for g in units[i]),i)),list(reversed(natural))]
    baseline=best=None;assignment=None;placements=None
    for sequence in sequences:
        candidate=first_fit(sequence)
        if candidate is None:continue
        result=evaluate(candidate)
        if result and (best is None or result[0]<best):assignment=candidate;best,placements=result
        if baseline is None:baseline=result[0]
    if assignment is None:fail('本轮有界分页候选未满足包络/同页/数量约束；请调整意图，不能据此认定无解。')
    improved=True
    while improved and evaluated<budget:
        improved=False;current=dict(assignment);destinations=list(dict.fromkeys(current.values()))
        for i in natural:
            if pins[i] is not None:continue
            for page in destinations:
                if evaluated>=budget:break
                if page==current[i]:continue
                candidate=dict(current);candidate[i]=page;result=evaluate(candidate)
                if result and result[0]<best:assignment=candidate;best,placements=result;improved=True
            for j in range(i+1,len(units)):
                if evaluated>=budget:break
                if pins[j] is not None or current[i]==current[j]:continue
                candidate=dict(current);candidate[i],candidate[j]=current[j],current[i];result=evaluate(candidate)
                if result and result[0]<best:assignment=candidate;best,placements=result;improved=True
    assigned={gid:assignment[i] for i,ids in enumerate(units) for gid in ids}
    page_names=list(dict.fromkeys(assigned[g['id']] for g in groups))
    # Search seed order is internal; generated page names follow functional order.
    generated=[p for p in page_names if p not in named];renamed={};used=set(named);ordinal=1
    for page in generated:
        while f'{spec["pagePrefix"]} {ordinal}' in used:ordinal+=1
        name=f'{spec["pagePrefix"]} {ordinal}';renamed[page]=name;used.add(name)
    assigned={gid:renamed.get(page,page) for gid,page in assigned.items()}
    page_names=list(dict.fromkeys(assigned[g['id']] for g in groups))
    crossings=[dict(net=n,pages=sorted({assigned[g] for g in ids}),weight=weights[n]) for n,ids in sorted(memberships.items()) if len({assigned[g] for g in ids})>1]
    return assigned,placements,dict(version=1,algorithm='bounded-envelope-pages-v1',pages=page_names,
        assignments=assigned,samePage=spec['samePage'],netWeights=spec['netWeights'],
        before=dict(pageCount=baseline[0],crossPageCost=baseline[1]),after=dict(pageCount=best[0],crossPageCost=best[1]),
        evaluatedCandidates=evaluated,searchBudget=budget,budgetExhausted=evaluated>=budget,crossings=crossings,
        objective='Minimize feasible page count, then weighted native-net page crossings. No role inference or global optimality claim.',
        geometry='Actual functional envelopes, uniform repeated cells, 1.27mm translations; columns are a per-row maximum in planned mode.')
