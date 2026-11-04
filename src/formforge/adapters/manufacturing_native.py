"""Read-only isolated KiCad inventory for manufacturing exports; never saves."""
import json
import sys
from pathlib import Path

import pcbnew as pcb
from native_asserts import configure_native_asserts

configure_native_asserts('exception')


def read(path):
    board = pcb.LoadBoard(path)
    mm = lambda value: round(pcb.ToMM(value), 6)
    point = lambda value: [mm(value.x), mm(value.y)]
    parts, holes = [], []
    for fp in board.GetFootprints():
        attributes = fp.GetAttributes()
        parts.append(dict(id=fp.m_Uuid.AsString(), reference=fp.GetReference(), value=fp.GetValue(),
            footprint=fp.GetFPID().GetUniStringLibId(), at=point(fp.GetPosition()),
            rotation=fp.GetOrientationDegrees(), layer=board.GetLayerName(fp.GetLayer()),
            path=fp.GetPath().AsString(), attributes=attributes, boardOnly=fp.IsBoardOnly(),
            excludeFromBom=fp.IsExcludedFromBOM(), excludeFromPosition=fp.IsExcludedFromPosFiles(),
            dnp=fp.IsDNP(), smd=bool(attributes & pcb.FP_SMD),
            throughHole=bool(attributes & pcb.FP_THROUGH_HOLE),
            # HasThroughHolePads also counts plastic locating NPTHs. Only
            # plated solder joints make an SMD part a hybrid assembly part.
            hasThroughHolePads=any(p.GetAttribute()==pcb.PAD_ATTRIB_PTH for p in fp.Pads()),
            fields={field.GetName(): field.GetText() for field in fp.GetFields()},
            pads=[dict(id=p.m_Uuid.AsString(),number=p.GetNumber(),at=point(p.GetPosition()),net=p.GetNetname(),netCode=p.GetNetCode()) for p in fp.Pads() if p.GetNumber()]))
        for pad in fp.Pads():
            size=point(pad.GetDrillSize())
            if max(size)>0:
                holes.append(dict(id=pad.m_Uuid.AsString(), footprintId=fp.m_Uuid.AsString(),
                    reference=fp.GetReference(), at=point(pad.GetPosition()), size=size,
                    plated=pad.GetAttribute()!=pcb.PAD_ATTRIB_NPTH))
    for via in board.GetTracks():
        if isinstance(via, pcb.PCB_VIA):
            holes.append(dict(id=via.m_Uuid.AsString(), reference=None, at=point(via.GetPosition()),
                              size=[mm(via.GetDrillValue())]*2, plated=True))
    edges=[]
    for edge in board.GetDrawings():
        if edge.GetLayer()==pcb.Edge_Cuts:
            edges.append(dict(id=edge.m_Uuid.AsString(), shape=int(edge.GetShape()),
                              start=point(edge.GetStart()), end=point(edge.GetEnd()), width=mm(edge.GetWidth())))
    texts=[dict(id=t.m_Uuid.AsString(),text=t.GetText(),at=point(t.GetPosition()),layer=board.GetLayerName(t.GetLayer()),
                size=point(t.GetTextSize()),thickness=mm(t.GetTextThickness()))
           for t in board.GetDrawings() if isinstance(t,pcb.PCB_TEXT) and t.GetLayer() in (pcb.F_SilkS,pcb.B_SilkS)]
    return dict(toolVersion=pcb.GetBuildVersion(), units='mm', origin='absolute-board-zero',boardTexts=texts,
        positionTransform='CLI PosX=boardX; PosY=-boardY; no bottom-negate-X; native rotation',
        auxiliaryOrigin=point(board.GetDesignSettings().GetAuxOrigin()),
        nominalThicknessMm=mm(board.GetDesignSettings().GetBoardThickness()),
        copperLayers=[board.GetLayerName(layer) for layer in board.GetEnabledLayers().CuStack()],
        footprints=sorted(parts,key=lambda f:f['reference']), holes=holes, edges=edges)


if __name__=='__main__':
    result=read(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
