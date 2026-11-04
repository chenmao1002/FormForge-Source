"""Execute bounded repair of existing ordinary chains; no file writes here."""
import math,time
import pcbnew as pcb
from route_engine import Geometry,uid,xy,point,physical_groups
from route_path_readability import simplify_polyline,_candidates,_compact
from route_readability_policy import metrics
from route_quality import _octilinear
from routing_rules import trace_chains,corner_audit,native_exit_options
from pad_exits import native_audit,proof
from body_gap import BodyGapGuard


def rows(board):
 return [dict(id=uid(t),type='via' if isinstance(t,pcb.PCB_VIA) else 'arc' if isinstance(t,pcb.PCB_ARC) else 'segment',net=t.GetNetname(),width=pcb.ToMM(t.GetWidth(pcb.F_Cu) if isinstance(t,pcb.PCB_VIA) else t.GetWidth()),**(dict(at=xy(t.GetPosition())) if isinstance(t,pcb.PCB_VIA) else dict(start=xy(t.GetStart()),end=xy(t.GetEnd()),layer=board.GetLayerName(t.GetLayer())))) for t in board.GetTracks()]


def plan(board,params):
 if params['request'].get('contactRework'):
  from pad_escape_rework import plan_contact
  return plan_contact(board,params)
 request=params['request'];snapshot=params['snapshot'];nets=set(request['nets']);basis=params['basis']
 from pad_escape_rework import evaluate,improved as escape_improved,path_improves
 targets=request.get('escapeTargets',[])
 escape_before=evaluate(board,basis,targets) if targets else None
 exit_blockers=set(escape_before['blockingObjectIds']) if escape_before else set()
 known={p['net'] for f in snapshot['footprints'] for p in f['pads']}
 if not nets<=known:raise ValueError('Unknown optimization net')
 protected={r['net'] for c in basis.get('pairedSections',[]) for r in c['rails']}
 tracks={uid(t):t for t in board.GetTracks()};source=rows(board)
 chains=trace_chains(snapshot['footprints'],source)
 bad={i for f in snapshot['layoutFeedback']['padExits']['findings'] for i in f['objectIds']}
 chains=[c for c in chains if c['net'] in nets and (set(c['objectIds'])&(bad|exit_blockers) or metrics(c['points'])['shortClusterCount'] or metrics(c['points'])['backtrackCount'])]
 chains.sort(key=lambda c:(-bool(set(c['objectIds'])&exit_blockers),len(c['objectIds']) if targets else 0,-bool(set(c['objectIds'])&bad),-metrics(c['points'])['backtrackCount'],-metrics(c['points'])['shortClusterCount'],c['objectIds']))
 deadline=time.monotonic()+request['searchSeconds'];ops=[];results=[]
 for chain in chains[:request['maxChains']]:
  if time.monotonic()>=deadline:break
  ids=chain['objectIds'];objects=[tracks[i] for i in ids];old=chain['points'];before=metrics(old)
  opening=bool(set(ids)&exit_blockers);prior_escape=evaluate(board,basis,targets) if opening else None
  if chain['net'] in protected or any(t.IsLocked() for t in objects):
   results.append(dict(net=chain['net'],objectIds=ids,status='protected'));continue
  if old[0]==old[-1]:continue
  prior=native_audit(board);groups=physical_groups(board)
  for t in objects:board.Remove(t)
  geometry=Geometry(board,basis['outline'],basis['rules'],body_guard=BodyGapGuard(board))
  open_space=evaluate(board,basis,targets) if opening else None
  layer=board.GetLayerID(chain['layer']);li=geometry.layers.index(layer);net=objects[0].GetNetCode();width=chain['width']
  clear=lambda a,b:geometry.clear(a,b,li,net,width,basis['rules']['clearance']) and geometry.pad_exit_clear(a,b,li,net,width)
  end=min(deadline,time.monotonic()+2.5)
  candidates=[];escape_candidates=[]
  if opening and before['bendCount']==0:
   from local_copper_rework import detours
   for offset in (.25,.5,.75,1.):
    spec=dict(anchors=[old[0],old[-1]],maxOffsetMm=offset,area=basis['outline'],net=chain['net'],layer=chain['layer'],width=width)
    escape_candidates.extend(v['operations'][0]['points'] for v in detours(spec))
  candidate=simplify_polyline(old,clear,0,max_window_corners=48,max_segment_checks=1600,deadline=min(end,time.monotonic()+.7),short_step_length=.5)
  candidates.append(candidate)
  def ends(at):
   pads=[p for f in board.GetFootprints() for p in f.Pads() if p.GetNetCode()==net and p.IsOnLayer(layer) and p.GetEffectiveShape(layer).Collide(pcb.SHAPE_CIRCLE(point(at),0),0)]
   if len(pads)!=1:return [[at]]
   p=pads[0];centre=xy(p.GetPosition())
   # Off-centre terminals may move only within their unique original pad.
   options=[[centre,[round(centre[i]+o['axis'][i]*o['length'],6) for i in (0,1)]] for neck in ((.15,.7,1.1) if opening and len(ids)>30 else (.15,)) for o in native_exit_options(p,neck,layer)]
   return [option for option in options if clear(*option)]
  if opening and len(ids)>30:
   blocked=[i for i,id in enumerate(ids) if id in exit_blockers]
   # Preserve the rest of a long routed chain. Searching only its remote
   # terminal-to-terminal shortcut misses an obstructed IC's last few bends.
   if blocked:
    for margin in (.2,.4,.6,1.5,3.):
     lo=min(blocked);hi=min(len(old)-1,max(blocked)+1);walk=0.
     while lo>0 and walk<margin:
      walk+=math.dist(old[lo],old[lo-1]);lo-=1
     walk=0.
     while hi<len(old)-1 and walk<margin:
      walk+=math.dist(old[hi],old[hi+1]);hi+=1
     window=old[lo:hi+1];allowance=min(2,before['lengthMm']*.15)
     for left in ends(window[0]):
      for right in ends(window[-1]):
       if time.monotonic()>=end:break
       for middle in _candidates(tuple(left[-1]),tuple(right[-1]),window,metrics(window)['lengthMm']+allowance,end):
        path=list(_compact([*old[:lo],*left,*middle,*reversed(right),*old[hi+1:]]))
        if all(clear(a,b) for a,b in zip(path,path[1:])):candidates.append(path)
  for left in ends(old[0]):
   for right in ends(old[-1]):
    if time.monotonic()>=end:break
    for middle in _candidates(tuple(left[-1]),tuple(right[-1]),old,before['lengthMm']+2,end):
     path=list(_compact([*left,*middle,*reversed(right)]))
     if all(clear(a,b) for a,b in zip(path,path[1:])):candidates.append(path)
  chosen=None;created=[]
  for path in escape_candidates+sorted(candidates,key=lambda p:(metrics(p)['backtrackCount'],metrics(p)['shortClusterCount'],metrics(p)['bendCount'],metrics(p)['lengthMm'])):
   if time.monotonic()>=min(deadline,end+1.5):break
   path=[[round(v,6) for v in p] for p in path];after=metrics(path)
   if len(path)>100 or not _octilinear(path):continue
   if path==old or after['lengthMm']>before['lengthMm']+min(2,before['lengthMm']*.15)+1e-6:continue
   if not opening and any(after[k]>before[k] for k in ('backtrackCount','shortClusterCount','bendCount')):continue
   if opening and not path_improves(prior_escape,open_space,chain['net'],chain['layer'],width,path):continue
   if not all(clear(a,b) for a,b in zip(path,path[1:])):continue
   local=corner_audit([dict(id=str(i),type='segment',net=chain['net'],layer=chain['layer'],start=a,end=b) for i,(a,b) in enumerate(zip(path,path[1:]))])
   if local['exposedCornerCount']:continue
   added=[]
   for a,b in zip(path,path[1:]):
    t=pcb.PCB_TRACK(board);t.SetStart(point(a));t.SetEnd(point(b));t.SetWidth(objects[0].GetWidth());t.SetLayer(layer);t.SetNetCode(net);board.Add(t);added.append(t)
   new_escape=evaluate(board,basis,targets) if opening else None
   if opening and not escape_improved(prior_escape,new_escape):
    for t in added:board.Remove(t)
    continue
   audit=native_audit(board)
   valid=proof(dict(layoutFeedback=dict(padExits=prior)),dict(layoutFeedback=dict(padExits=audit)))['passed']
   # Native whole-board adoption still checks pad-exempt and branch corners.
   local=corner_audit([dict(id=str(i),type='segment',net=chain['net'],layer=chain['layer'],start=a,end=b) for i,(a,b) in enumerate(zip(path,path[1:]))])
   valid=valid and local['exposedCornerCount']==0 and physical_groups(board)==groups
   improved=escape_improved(prior_escape,new_escape) if opening else any(after[k]<before[k] for k in ('backtrackCount','shortClusterCount','bendCount')) or after['lengthMm']<before['lengthMm']-1e-6 or len(audit['findings'])<len(prior['findings'])
   if valid and improved:chosen=path;created=added;break
   for t in added:board.Remove(t)
  if chosen is None:
   for t in objects:board.Add(t)
   results.append(dict(net=chain['net'],objectIds=ids,status='no_legal_improvement'));continue
  ops.extend(dict(kind='remove_track',objectId=i,expectedHash=params['hashes'][i]) for i in ids)
  ops.append(dict(kind='add_track',net=chain['net'],layer=chain['layer'],width=width,points=chosen))
  results.append(dict(net=chain['net'],objectIds=ids,status='improved',before=before,after=metrics(chosen),padFindingsBefore=len(prior['findings']),padFindingsAfter=len(audit['findings'])))
 return dict(operations=ops,chains=results,escapeBefore=escape_before,escapeAfter=evaluate(board,basis,targets) if targets else None,eligibleChainCount=len(chains),budgetExhausted=time.monotonic()>=deadline,scope='Existing explicit ordinary nets; preserve widths, layers, branches, vias, physical groups, locked copper and pair contracts. Final native checks required.')

