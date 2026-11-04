"""Keep autorouted tracks out of open SMD body gaps."""
import pcbnew as pcb
from kicad_compat import set_copper_keepout, copper_keepout


def cores(board):
    for f in board.GetFootprints():
        pads = [p for p in f.Pads() if p.GetAttribute() == pcb.PAD_ATTRIB_SMD]
        if len(pads) < 2 or len(pads) != len(list(f.Pads())):
            continue
        layer = pcb.F_Cu if f.GetLayer() == pcb.F_Cu else pcb.B_Cu
        if any(not p.IsOnLayer(layer) for p in pads):
            continue
        fab = pcb.F_Fab if layer == pcb.F_Cu else pcb.B_Fab
        shapes = [s.GetBoundingBox() for s in f.GraphicalItems()
                  if s.GetClass() == 'PCB_SHAPE' and s.GetLayer() == fab]
        if not shapes:
            continue
        bx = (min(s.GetLeft() for s in shapes), min(s.GetTop() for s in shapes),
              max(s.GetRight() for s in shapes), max(s.GetBottom() for s in shapes))
        if len(pads)==2:
            a,b = sorted(pads,key=lambda p:p.GetPosition().y)
            if abs(a.GetPosition().y-b.GetPosition().y) > abs(a.GetPosition().x-b.GetPosition().x):
                y0,y1 = a.GetBoundingBox().GetBottom(),b.GetBoundingBox().GetTop()
                rect = (bx[0],max(bx[1],y0),bx[2],min(bx[3],y1))
            else:
                a,b = sorted(pads,key=lambda p:p.GetPosition().x)
                x0,x1 = a.GetBoundingBox().GetRight(),b.GetBoundingBox().GetLeft()
                rect = (max(bx[0],x0),bx[1],min(bx[2],x1),bx[3])
        else:
            # For a perimeter-mounted multi-pad part (for example a 4-pad
            # crystal), protect the empty center while retaining direct escape
            # corridors from every pad. Skip packages with a center/exposed pad.
            xs=sorted({p.GetPosition().x for p in pads});ys=sorted({p.GetPosition().y for p in pads})
            if len(xs)<2 or len(ys)<2:continue
            mx=(xs[0]+xs[-1])//2;my=(ys[0]+ys[-1])//2
            left=[p for p in pads if p.GetPosition().x<mx];right=[p for p in pads if p.GetPosition().x>mx]
            top=[p for p in pads if p.GetPosition().y<my];bottom=[p for p in pads if p.GetPosition().y>my]
            if not all((left,right,top,bottom)):continue
            rect=(max(bx[0],max(p.GetBoundingBox().GetRight() for p in left)),
                  max(bx[1],max(p.GetBoundingBox().GetBottom() for p in top)),
                  min(bx[2],min(p.GetBoundingBox().GetLeft() for p in right)),
                  min(bx[3],min(p.GetBoundingBox().GetTop() for p in bottom)))
            if any(not (p.GetBoundingBox().GetRight()<=rect[0] or p.GetBoundingBox().GetLeft()>=rect[2]
                           or p.GetBoundingBox().GetBottom()<=rect[1] or p.GetBoundingBox().GetTop()>=rect[3])
                   for p in pads):continue
        if rect[2]-rect[0] < pcb.FromMM(.2) or rect[3]-rect[1] < pcb.FromMM(.2):
            continue
        yield f.GetReference(),layer,rect


def prepare(board):
    rows=[]
    for reference,layer,(x0,y0,x1,y1) in cores(board):
        zone=pcb.ZONE(board);zone.SetIsRuleArea(True)
        layers=pcb.LSET();layers.AddLayer(layer);zone.SetLayerSet(layers)
        zone.SetZoneName('SMD_BODY_GAP_'+reference)
        zone.SetDoNotAllowTracks(True);zone.SetDoNotAllowVias(True)
        set_copper_keepout(zone,False);zone.SetDoNotAllowPads(False);zone.SetDoNotAllowFootprints(False)
        poly=zone.Outline();poly.NewOutline()
        for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1)):
            poly.Append(pcb.VECTOR2I(x,y))
        board.Add(zone)
        rows.append({'reference':reference,'layer':board.GetLayerName(layer),
                     'rectMm':[pcb.ToMM(v) for v in (x0,y0,x1,y1)]})
    return rows
