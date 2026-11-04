"""Shared mechanical editing policy; part identities remain stable references."""
import math

POLICY = dict(
    version='mechanical-editing-v1',
    parameters='collapsible-dimensions-and-appearance',
    screwColor='one-color-for-all-identified-screws',
    screwName='M{diameter}_{length}MM',
    identity='Keep output IDs, engineering references and file paths stable; display names use actual catalog dimensions.',
    agentInstructions='Expose editable dimensions with concise labels and units. Keep parameter and appearance panels collapsible. '
        'All screws share screw_color; never create a color control per screw. Identify screws from source-backed standard_part '
        'features (including rigid transforms/mirrors) or engineering_plan.joints, not a substring in a part name. '
        'Generate screw display_name from actual catalog diameter and length, for example M2.5_5MM. '
        'Keep instance IDs and all engineering/explosion references unchanged; do not invent unknown screw sizes.')


def screw_name(row):
    """The catalog is the dimension source; strings/IDs are never parsed as sizes."""
    diameter, length = row.get('diameter'), row.get('length')
    if row.get('kind') != 'screw' or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (diameter, length)):
        return None
    return f'M{diameter:g}_{length:g}MM'


def part_presentations(design, hardware):
    features = {f['id']: f for f in design.get('features', [])}
    joints = {j['screw_part']: j['screw_id'] for j in (design.get('engineering_plan') or {}).get('joints', [])}
    rows = []
    for part in design.get('parts', []):
        feature = features.get(part.get('feature')); seen = set()
        while feature and feature['id'] not in seen and feature['op'] in {'transform', 'mirror'}:
            seen.add(feature['id']); feature = features.get(feature['source'])
        part_id = feature['part_id'] if feature and feature['op'] == 'standard_part' else joints.get(part['name'])
        spec = hardware.get(part_id, {})
        label = screw_name(spec)
        row = dict(id=part['name'], displayName=label or part.get('display_name') or part['name'])
        if label:
            row.update(colorGroup='screws', catalogId=part_id, diameterMm=spec['diameter'], lengthMm=spec['length'])
        rows.append(row)
    return rows


def apply_screw_style(design, hardware):
    """Called by native schemas for every new build, including HTTP/MCP callers."""
    rows = part_presentations(design.model_dump(), hardware)
    screws = {r['id']: r for r in rows if r.get('colorGroup') == 'screws'}
    if not screws:
        return
    color = design.screw_color
    if color is None:
        color = next(p.color for p in design.parts if p.name in screws) or (.32, .55, .68)
    design.screw_color = color
    for part in design.parts:
        if part.name in screws:
            part.color = color
            part.display_name = screws[part.name]['displayName']
