"""Read physical drill centres from KiCad; never modify the PCB."""
import json,sys
from pathlib import Path
import pcbnew as pcb
sys.path.insert(0,str(Path(__file__).resolve().parent))
from native_asserts import configure_native_asserts
configure_native_asserts('exception')

def mm(v):return round(pcb.ToMM(v),6)
def xy(v):return [mm(v.x),mm(v.y)]

def main(params):
    if not pcb.GetBuildVersion().startswith('10.'):raise ValueError('KiCad 10 required')
    board=pcb.LoadBoard(params['board']);holes=[]
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            size=xy(pad.GetDrillSize())
            if min(size)<=0:continue
            holes.append(dict(id=pad.m_Uuid.AsString(),footprintId=fp.m_Uuid.AsString(),reference=fp.GetReference(),number=pad.GetNumber(),at=xy(pad.GetPosition()),drill=size,
                rotation=pad.GetOrientationDegrees(),plated=pad.GetAttribute()!=pcb.PAD_ATTRIB_NPTH,kind='pad',mountingPurpose='not-inferred'))
    for item in board.GetTracks():
        if isinstance(item,pcb.PCB_VIA):holes.append(dict(id=item.m_Uuid.AsString(),at=xy(item.GetPosition()),drill=[mm(item.GetDrillValue())]*2,rotation=0,plated=True,kind='via',mountingPurpose='not-a-mounting-hole'))
    return dict(units='mm',thickness=mm(board.GetDesignSettings().GetBoardThickness()),holes=sorted(holes,key=lambda h:h['id']),engineVersion=pcb.GetBuildVersion())

if __name__=='__main__':
    result=main(json.loads(Path(sys.argv[1]).read_text(encoding='utf8')))
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
