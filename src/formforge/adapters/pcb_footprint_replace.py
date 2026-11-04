"""Native execution of already validated contact mappings. No selection policy."""
from pathlib import Path
import pcbnew as pcb


def replace(board,old,request,root):
    lib,name=request['footprint'].split(':');fp=pcb.FootprintLoad(str(Path(root)/(lib+'.pretty')),name)
    if fp is None:raise ValueError('Cannot load replacement footprint')
    mapped={(r['number'],tuple(round(float(v),6) for v in r['targetAt'])):r for r in request['padMap']}
    seen=set()
    for pad in fp.Pads():
        if not pad.GetNumber():continue
        pos=pad.GetFPRelativePosition();key=(pad.GetNumber(),(round(pcb.ToMM(pos.x),6),round(pcb.ToMM(pos.y),6)))
        row=mapped[key];pad.SetUuidDirect(pcb.KIID(row['padId']));seen.add(key)
    if seen!=mapped.keys():raise ValueError('Native pad contacts differ from complete mapping')
    fp.SetUuidDirect(pcb.KIID(request['objectId']));fp.SetFPID(pcb.LIB_ID(lib,name));fp.SetReference(old.GetReference());fp.SetValue(old.GetValue())
    fp.SetPosition(pcb.VECTOR2I(pcb.FromMM(request['at'][0]),pcb.FromMM(request['at'][1])))
    # Flip needs the board layer stack; an unattached library footprint has none.
    board.Remove(old);board.Add(fp)
    # KiCad's removed-footprint SWIG proxy owns the retired native object.
    # Retain it through serialization/inventory: early destruction corrupts the
    # cast of a complete library footprint still held by this board (10.0.6).
    retired=getattr(board,'_formforge_retired',[]);retired.append(old);board._formforge_retired=retired
    if old.GetLayer()==pcb.B_Cu:fp.Flip(fp.GetPosition(),False)
    fp.SetOrientationDegrees(request['rotation']);fp.SetLocked(old.IsLocked())
    instance=pcb.FP_DNP|pcb.FP_EXCLUDE_FROM_BOM|pcb.FP_EXCLUDE_FROM_POS_FILES|pcb.FP_BOARD_ONLY
    fp.SetAttributes((fp.GetAttributes()&~instance)|(old.GetAttributes()&instance))
    # Preserve user field geometry and appearance, not library-default labels.
    for previous,current in [(old.Reference(),fp.Reference()),(old.Value(),fp.Value())]:
        current.SetAttributes(previous.GetAttributes());current.SetPosition(previous.GetPosition());current.SetLayer(previous.GetLayer())
        current.SetTextAngle(previous.GetTextAngle());current.SetVisible(previous.IsVisible());current.SetKeepUpright(previous.IsKeepUpright())
    fields=old.GetFieldsText();fields.pop('Footprint',None);fp.SetFields(fields)
    return fp
