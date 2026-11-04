"""Frozen defaults, explicit overrides and native KiCad design rules."""
from .schemas import BoardRules

def resolve(preferences, explicit=None):
    values=preferences.get('personalDefaultsSnapshot',{}).get('pcbDefaults',{}).get('numericRules',{})
    values={**values,**preferences.get('pcbRules',{})}
    if explicit is not None:values.update(explicit)
    rules=BoardRules.model_validate(values).model_dump(exclude_none=True)
    return rules

def apply_native_vias(settings,diameter,drill):
    native=settings.setdefault('board',{}).setdefault('design_settings',{}).setdefault('rules',{})
    native.update(min_via_diameter=diameter,min_through_hole_diameter=drill)
    # An explicitly selected via must also fit KiCad's annular-width minimum.
    # Preserve an existing compatible minimum; do not alter unrelated rules.
    native['min_via_annular_width']=min(native.get('min_via_annular_width',.1),round((diameter-drill)/2,9))


def apply_native(settings,rules):
    names=dict(clearance='min_clearance',trackWidth='min_track_width',viaDiameter='min_via_diameter',viaDrill='min_through_hole_diameter',copperEdge='min_copper_edge_clearance',holeClearance='min_hole_clearance')
    settings.setdefault('board',{}).setdefault('design_settings',{}).setdefault('rules',{}).update({v:rules[k] for k,v in names.items()})
    apply_native_vias(settings,rules['viaDiameter'],rules['viaDrill'])
    classes=settings.setdefault('net_settings',{}).setdefault('classes',[])
    default=next((c for c in classes if c.get('name')=='Default'),None)
    if default is None:default={'name':'Default'};classes.append(default)
    default.update(clearance=rules['clearance'],track_width=rules['trackWidth'],via_diameter=rules['viaDiameter'],via_drill=rules['viaDrill'])
