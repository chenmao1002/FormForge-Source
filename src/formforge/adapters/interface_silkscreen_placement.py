"""Place explicit interface legends as aligned groups, using native text bounds."""
import math
import pcbnew as pcb
from silkscreen_placement import rect,overlaps
from board_outline import contains
from route_engine import point,uid,xy


def plan(board,params):
 refs=params['request']['references'];rules={r['reference']:r for r in params['legends']};fps={f.GetReference():f for f in board.GetFootprints()}
 if len(set(refs))!=len(refs) or any(r not in rules or r not in fps for r in refs):raise ValueError('Interface placement requires unique references with explicit complete legends')
 texts=[t for t in board.GetDrawings() if isinstance(t,pcb.PCB_TEXT)]
 operations=[];results=[]
 for ref in refs:
  rule=rules[ref];fp=fps[ref];layer=board.GetLayerID(rule['layer']);pads={}
  for p in fp.Pads():
   if p.GetNumber():pads.setdefault(p.GetNumber(),[]).append(p)
  if set(pads)!={p['pin'] for p in rule['pins']}:raise ValueError('Interface pin coverage mismatch')
  labels=[(rule['title'],xy(fp.GetPosition()),rule['maxTitleDistanceMm'])]
  for p in rule['pins']:
   if {a.GetNetname() for a in pads[p['pin']]}!={p['net']}:raise ValueError('Interface pin net mismatch')
   labels.append((p['label'],xy(pads[p['pin']][0].GetPosition()),rule['maxPinDistanceMm']))
  removed=[];items=[]
  for label,anchor,reach in labels:
   matches=sorted([t for t in texts if t not in removed and t.GetText()==label and t.GetLayer()==layer and math.dist(xy(t.GetPosition()),anchor)<=reach],key=lambda t:math.dist(xy(t.GetPosition()),anchor))
   old=matches[0] if matches else None
   if old:removed.append(old)
   size=max(rule['minTextSizeMm'],max(xy(old.GetTextSize())) if old else 0);stroke=max(rule['minStrokeMm'],pcb.ToMM(old.GetTextThickness()) if old else 0)
   item=pcb.PCB_TEXT(board);item.SetText(label);item.SetLayer(layer);item.SetTextSize(point([size,size]));item.SetTextThickness(round(stroke*1e6));item.SetMirrored(layer==pcb.B_SilkS);item.SetPosition(point([0,0]))
   items.append((item,rect(item),size,stroke))
  if fp.IsLocked() or any(t.IsLocked() for t in removed):
   results.append(dict(reference=ref,status='retained_locked'));continue
  obstacles=[]
  for f in fps.values():
   if f.GetLayer()==(pcb.F_Cu if layer==pcb.F_SilkS else pcb.B_Cu):
    b=f.GetBoundingBox(False,False);obstacles.append([pcb.ToMM(v) for v in (b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom())])
   for p in f.Pads():
    if p.IsOnLayer(pcb.F_Mask if layer==pcb.F_SilkS else pcb.B_Mask):obstacles.append(rect(p))
   obstacles.extend(rect(t) for t in f.GetFields() if t.IsVisible() and t.GetLayer()==layer)
  obstacles.extend(rect(t) for t in texts if t not in removed and t.GetLayer()==layer)
  origin=xy(fp.GetPosition());radius=params['request']['maxOffsetMm'];steps=int(radius/.5)
  height=sum(b[3]-b[1]+.35 for _,b,_,_ in items)-.35;width=max(b[2]-b[0] for _,b,_,_ in items)
  positions=[[origin[0]+x*.5-width/2,origin[1]+y*.5-height/2] for x in range(-steps,steps+1) for y in range(-steps,steps+1) if x*x+y*y<=steps*steps]
  positions.sort(key=lambda p:(math.dist([p[0]+width/2,p[1]+height/2],origin),p))
  chosen=None
  for left,top in positions:
   box=[left,top,left+width,top+height]
   if any(overlaps(box,b,.25) for b in obstacles):continue
   if not all(contains(params['outline'],p,.2) for p in ([box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]])):continue
   at=[];y=top
   for (item,b,size,stroke),(label,anchor,reach) in zip(items,labels):
    p=[round(left-b[0],6),round(y-b[1],6)];at.append(p);y+=b[3]-b[1]+.35
   if all(math.dist(p,label[1])<=label[2] for p,label in zip(at,labels)):chosen=at;break
  if chosen is None:
   results.append(dict(reference=ref,status='no_space_source_retained'));continue
  for t in removed:
   operations.append(dict(kind='remove_board_text',objectId=uid(t),expectedHash=params['hashes'][uid(t)]));texts.remove(t)
  for (item,b,size,stroke),at in zip(items,chosen):
   item.SetPosition(point(at));texts.append(item)
   operations.append(dict(kind='add_board_text',text=item.GetText(),at=at,layer=rule['layer'],rotation=0,size=size,thickness=stroke))
  results.append(dict(reference=ref,status='placed',layout='aligned-column',labelCount=len(items),labels=[t.GetText() for t,_,_,_ in items]))
 return dict(operations=operations,placements=results,scope='Explicit functional and pin labels as aligned native-bound groups; exact source pad/net and per-label distance checks. No inferred voltage or mating-face pin order.')
