"""Advisory four-corner layout review of verified mounting features.

This is not screw-envelope, manufacturing or retention qualification.
"""
import math
from .board_outline import read_native, contains


def review(tree, features):
    result = dict(policy='four-corner-mounting-review-v1', status='needs_review',
                  expectedCornerCount=4, verifiedHoleCount=len(features), findings=[],
                  alignmentToleranceMm=0.05, cornerRegionFraction=0.25,
                  scope='Verified declared NPTH only; advisory geometry, not fastener clearance or fixation proof.')
    try:
        box = read_native(tree)
    except ValueError:
        result.update(status='unsupported_outline')
        result['findings'].append(dict(kind='outline_requires_explicit_mounting_plan'))
        return result
    result['outline'] = box
    x, y = box['origin']; w, h = box['width'], box['height']
    corners = [(x, y), (x+w, y), (x, y+h), (x+w, y+h)]
    selected = []
    for index, corner in enumerate(corners):
        candidates = [f for f in features if abs(f['at'][0]-corner[0]) <= w/4
                      and abs(f['at'][1]-corner[1]) <= h/4
                      and contains(box, f['at'], f['drill']/2)]
        if not candidates:
            result['findings'].append(dict(kind='missing_corner_hole', corner=index, at=list(corner)))
            selected.append(None)
        else:
            selected.append(min(candidates, key=lambda f: (math.dist(f['at'], corner), f['id'])))
    result['cornerHoleIds'] = [f['id'] if f else None for f in selected]
    for a, b, axis in [(0, 1, 1), (2, 3, 1), (0, 2, 0), (1, 3, 0)]:
        if selected[a] and selected[b]:
            delta = abs(selected[a]['at'][axis]-selected[b]['at'][axis])
            if delta > result['alignmentToleranceMm']:
                result['findings'].append(dict(kind='unaligned_corner_holes',
                    holeIds=[selected[a]['id'], selected[b]['id']], axis='xy'[axis], deltaMm=round(delta, 6)))
    if not result['findings']:
        result['status'] = 'aligned_corner_pattern'
    return result
