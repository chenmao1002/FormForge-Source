"""Shared, finite readability preferences and fixed-reference length budgets.

This module has no KiCad dependency. Electrical legality is a caller-owned
mandatory gate; a visual improvement alone can never authorise copper.
"""
import math

_DEFAULTS = {'maxAddedLengthMm': 2.0, 'maxAddedRatio': .08}


def validate(config):
    if not isinstance(config, dict) or set(config) != set(_DEFAULTS):
        raise ValueError('readability needs maxAddedLengthMm and maxAddedRatio')
    for key, maximum in (('maxAddedLengthMm', 4.9), ('maxAddedRatio', .25)):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError('Invalid readability ' + key)
    return dict(config)


def settings(policy):
    """Return a fresh settings dictionary; omission preserves previous defaults."""
    return validate(policy['readability']) if 'readability' in (policy or {}) else dict(_DEFAULTS)


def allowance(original_length, policy=None):
    if not math.isfinite(original_length) or original_length < 0:
        raise ValueError('Readability reference length must be finite and nonnegative')
    configured = settings(policy or {})
    return min(configured['maxAddedLengthMm'], original_length * configured['maxAddedRatio'])


def _clusters(findings):
    """Deduplicate overlapping defect windows; touching endpoints are independent."""
    intervals = sorted((row['startIndex'], row['endIndex']) for row in findings)
    count = 0
    finish = -1
    for start, end in intervals:
        if start >= finish:
            count += 1
        finish = max(finish, end)
    return count


def metrics(points, short_step_length=.5):
    from circuit_path_readability import _compact, direction_changes, polyline_length, short_bend_issues
    points = _compact(points)
    findings = short_bend_issues(points, short_step_length, short_step_length * 4)
    return {'lengthMm': polyline_length(points), 'bendCount': direction_changes(points),
            'backtrackCount': _clusters([row for row in findings if row['kind'] == 'local-backtracking']),
            'shortClusterCount': _clusters([row for row in findings if row['kind'] == 'short-alternating-bends'])}


def quality_key(measurements):
    return (measurements['backtrackCount'], measurements['shortClusterCount'],
            measurements['bendCount'], measurements['lengthMm'])


def improves(candidate, before, original_length=None, policy=None, old_vias=0, new_vias=0):
    """Accept only monotone visual quality within the immutable original budget.

    ``candidate`` and ``before`` are metrics dictionaries. ``original_length``
    must be the complete terminal path's first reference, not the previous
    window or a subsequently accepted longer path.
    """
    if new_vias > old_vias:
        return False
    reference = before['lengthMm'] if original_length is None else original_length
    if candidate['lengthMm'] > reference + allowance(reference, policy) + 1e-8:
        return False
    fields = ('backtrackCount', 'shortClusterCount', 'bendCount')
    if any(candidate[field] > before[field] for field in fields):
        return False
    visual_gain = any(candidate[field] < before[field] for field in fields)
    return visual_gain or candidate['lengthMm'] < before['lengthMm'] - 1e-8 or new_vias < old_vias
