"""Explicit physical stackup syntax and proof, independent of pcbnew and UI."""
import json
import math
from pathlib import Path

try:from .native_syntax import parse
except ImportError:from native_syntax import parse


def layer_names(count):
    if count not in (2,4):raise ValueError('Only explicit 2/4-layer stackups are supported')
    return ['F.Cu',*['In%d.Cu'%i for i in range(1,count-1)],'B.Cu']


def validate(profile):
    names=layer_names(len(profile['copper']))
    if [c['name'] for c in profile['copper']]!=names:raise ValueError('Copper stackup must follow physical order')
    if len(profile['dielectrics'])!=len(names)-1:raise ValueError('Exactly one dielectric model is required per copper gap')
    def number(v,lo,hi):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Invalid stackup physical quantity')
    number(profile['nominalThickness'],.4,3.2);number(profile['thicknessTolerance'],0,.2)
    for c in profile['copper']:number(c['thickness'],.009,.21)
    for d in profile['dielectrics']:
        number(d['thickness'],.02,3.2);number(d['epsilonR'],1,20)
        if d.get('lossTangent') is not None:number(d['lossTangent'],0,.2)
        if d['type'] not in ('core','prepreg') or not d['material'].strip():raise ValueError('Incomplete dielectric model')
    total=sum(c['thickness'] for c in profile['copper'])+sum(d['thickness'] for d in profile['dielectrics'])
    if abs(total-profile['nominalThickness'])>profile['thicknessTolerance']+1e-6:raise ValueError('Stackup sum exceeds explicit nominal-thickness tolerance')
    if abs(total-profile['nominalThickness'])>1e-6 and not profile['thicknessNote'].strip():raise ValueError('Nominal/model thickness difference requires an explicit basis')
    return names


def value(node,key,default=None):
    found=node.children(key)
    if not found:return default
    if len(found)!=1:raise ValueError('Ambiguous stackup '+key)
    return found[0].values[1]


def read_native(path):
    root=parse(Path(path).read_text(encoding='utf-8-sig'))
    names=[n.values[1] for n in root.one('layers').values[1:] if hasattr(n,'values') and str(n.values[1]).endswith('.Cu')]
    stacks=root.one('setup').children('stackup');copper=[];dielectrics=[]
    if len(stacks)>1:raise ValueError('Duplicate native stackup')
    for node in stacks[0].children('layer') if stacks else []:
        name=node.values[1];kind=value(node,'type');thickness=value(node,'thickness')
        if kind=='copper':copper.append(dict(name=name,thickness=float(thickness)))
        elif kind in ('core','prepreg'):
            dielectrics.append(dict(type=kind,thickness=float(thickness),material=value(node,'material'),
                epsilonR=float(value(node,'epsilon_r')) if value(node,'epsilon_r') is not None else None,
                lossTangent=float(value(node,'loss_tangent')) if value(node,'loss_tangent') is not None else None))
    total=sum(c['thickness'] for c in copper)+sum(d['thickness'] for d in dielectrics)
    return dict(copperLayers=names,nominalThickness=float(value(root.one('general'),'thickness')),copper=copper,dielectrics=dielectrics,
        modelThickness=round(total,6) if copper else None)


def write_native(path,profile):
    validate(profile);path=Path(path);text=path.read_text(encoding='utf-8-sig');root=parse(text);setup=root.one('setup');rows=[]
    q=lambda v:json.dumps(v,ensure_ascii=False)
    for i,c in enumerate(profile['copper']):
        rows.append(f'(layer {q(c["name"])} (type "copper") (thickness {c["thickness"]}))')
        if i<len(profile['dielectrics']):
            d=profile['dielectrics'][i];loss='' if d.get('lossTangent') is None else f' (loss_tangent {d["lossTangent"]})'
            rows.append(f'(layer "dielectric {i+1}" (type {q(d["type"])}) (thickness {d["thickness"]}) (material {q(d["material"])}) (epsilon_r {d["epsilonR"]}){loss})')
    replacement='(stackup\n'+ '\n'.join(rows)+'\n)';old=setup.children('stackup')
    if len(old)>1:raise ValueError('Ambiguous existing stackup')
    start,end=(old[0].start,old[0].end) if old else (setup.end-1,setup.end-1)
    path.write_text(text[:start]+replacement+text[end:],encoding='utf8')


def proof(profile,observed):
    names=validate(profile);matches=names==observed['copperLayers'] and abs(profile['nominalThickness']-observed['nominalThickness'])<1.1e-6
    matches &= len(profile['copper'])==len(observed['copper']) and len(profile['dielectrics'])==len(observed['dielectrics'])
    for expected,actual in zip(profile['copper'],observed['copper']):
        matches &= expected['name']==actual['name'] and abs(expected['thickness']-actual['thickness'])<1.1e-6
    for expected,actual in zip(profile['dielectrics'],observed['dielectrics']):
        matches &= all(expected[k]==actual[k] for k in ('type','material'))
        for k in ('thickness','epsilonR','lossTangent'):
            if expected.get(k) is not None:matches &= actual.get(k) is not None and abs(expected[k]-actual[k])<1.1e-6
    return dict(passed=bool(matches),profileName=profile['name'],copperLayers=names,nominalThickness=profile['nominalThickness'],
        modelThickness=observed['modelThickness'],thicknessNote=profile['thicknessNote'],impedanceVerified=False,manufacturingApproved=False,
        source=profile['source'],unprovidedLossTangents=[i+1 for i,d in enumerate(profile['dielectrics']) if d.get('lossTangent') is None],
        scope='Saved physical layer/material model matches declared profile; no field solving or fabrication approval.')
