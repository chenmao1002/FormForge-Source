"""Bounded, clearance-aware octilinear simplification for routed centrelines.

The geometry is independent of KiCad and of any lattice.  The caller supplies
the full-segment clearance test, so a sampled preview never authorises copper.
Candidates may introduce a bend away from the original grid staircase, while
one fixed length allowance limits the complete result, rather than each edit.
"""
import math
import time


_DIRECTIONS = ((1, 0), (1, 1), (0, 1), (-1, 1),
               (-1, 0), (-1, -1), (0, -1), (1, -1))
_EPS = 1e-9


def polyline_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _direction(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    scale = math.hypot(dx, dy)
    return None if scale == 0 else (round(dx / scale, 10), round(dy / scale, 10))


def _compact(points):
    result = []
    for point in points:
        point = tuple(point)
        if result and point == result[-1]:
            continue
        # Exact collinearity only: this also preserves arbitrary physical
        # endpoints without silently rounding native nanometre coordinates.
        if len(result) >= 2:
            a, b = result[-2:]
            u = (b[0]-a[0], b[1]-a[1]); v = (point[0]-b[0], point[1]-b[1])
            if u[0]*v[1] == u[1]*v[0] and u[0]*v[0]+u[1]*v[1] > 0:
                result.pop()
        result.append(point)
    return result


def direction_changes(points):
    directions = [_direction(a, b) for a, b in zip(points, points[1:]) if a != b]
    return sum(a != b for a, b in zip(directions, directions[1:]))


def _sharp_turns(points):
    directions = [_direction(a, b) for a, b in zip(points, points[1:]) if a != b]
    return sum(a[0]*b[0]+a[1]*b[1] < -_EPS for a, b in zip(directions, directions[1:]))


def short_bend_issues(points, short_step_length=.5, local_span_length=2.):
    """Locate short alternating elbows and local reversals in caller units.

    Same-sign 45-degree chamfers do not count as alternating bends. These are
    advisory geometric findings: pads, obstacles and length tuning can make a
    finding necessary, so only the caller's complete clearance/policy proof may
    authorise a replacement. Indices refer to the original input vertices.
    """
    if (not math.isfinite(short_step_length) or short_step_length <= 0
            or not math.isfinite(local_span_length) or local_span_length < short_step_length):
        raise ValueError('invalid local-bend audit lengths')
    points = [tuple(p) for p in points]
    vectors = [(b[0]-a[0], b[1]-a[1]) for a, b in zip(points, points[1:])]
    lengths = [math.hypot(*v) for v in vectors]
    turns = [(a[0]*b[1]-a[1]*b[0])/(la*lb) if la*lb else 0.
             for a, b, la, lb in zip(vectors, vectors[1:], lengths, lengths[1:])]
    findings = []
    for index, (first, second) in enumerate(zip(turns, turns[1:])):
        # A tiny diagonal staircase or dogleg has opposite signed turns
        # separated by one short leg; straight/zero legs contribute no turn.
        if first*second < -_EPS and lengths[index+1] <= short_step_length+_EPS:
            findings.append({'kind': 'short-alternating-bends', 'startIndex': index,
                             'endIndex': index+3, 'cornerIndices': [index+1, index+2],
                             'pattern': 'ABA' if _direction(points[index], points[index+1]) ==
                                        _direction(points[index+2], points[index+3]) else 'alternating',
                             'betweenCornersLength': lengths[index+1]})
    for index, first in enumerate(vectors):
        if not lengths[index]:
            continue
        travelled = 0.
        for end in range(index+1, len(vectors)):
            if end > index+1:
                travelled += lengths[end-1]
            if travelled > local_span_length+_EPS:
                break
            second = vectors[end]
            if first[0]*second[0]+first[1]*second[1] < -_EPS*lengths[index]*lengths[end]:
                findings.append({'kind': 'local-backtracking', 'startIndex': index,
                                 'endIndex': end+1, 'cornerIndices': list(range(index+1, end+1)),
                                 'betweenCornersLength': travelled})
                break
    return findings


def _terms(delta, u, v):
    determinant = u[0]*v[1]-u[1]*v[0]
    if not determinant:
        return None
    return ((delta[0]*v[1]-delta[1]*v[0])/determinant,
            (u[0]*delta[1]-u[1]*delta[0])/determinant)


def _point(a, direction, distance):
    values = (a[0]+direction[0]*distance, a[1]+direction[1]*distance)
    return tuple(round(value) if abs(value-round(value)) <= 1e-8 else value for value in values)


def _splits(values, low, high, integral):
    if integral:
        # The grid wrapper cannot accept fractional sliding corners. Include
        # neighbouring integer offsets (both parities for diagonal bridges),
        # so a fractional budget boundary cannot conceal a legal clear rail.
        values = set(values)
        for value in tuple(values):
            values.update((math.floor(value)-1, math.floor(value), math.ceil(value), math.ceil(value)+1))
    return (value for value in values if low-_EPS <= value <= high+_EPS)


def _candidates(a, b, hints, limit, deadline=None):
    """Two-leg visibility plus bounded sliding three-leg corridors.

    Splitting the axial/diagonal remainder between both ends is essential:
    neither endpoint of a long staircase need lie on its best main diagonal.
    Opposite outer legs also offer a small clear detour around an obstacle.
    """
    delta = (b[0]-a[0], b[1]-a[1])
    integral = all(value == round(value) for value in (*a, *b))
    candidates = {(a, b)} if not delta[0] or not delta[1] or abs(delta[0]) == abs(delta[1]) else set()
    for u in _DIRECTIONS:
        for v in _DIRECTIONS:
            if deadline is not None and time.monotonic() >= deadline:
                return iter(())
            terms = _terms(delta, u, v)
            if terms is None:
                continue
            outer, middle = terms
            if middle < -_EPS:
                continue
            middle = max(0.0, middle)
            # Parallel outer legs: start with u, traverse v, finish with u.
            if outer >= -_EPS and u[0]*v[0]+u[1]*v[1] >= 0:
                outer = max(0.0, outer)
                splits = {0.0, outer*.25, outer*.5, outer*.75, outer}
                norm = u[0]*u[0]+u[1]*u[1]
                for hint in hints:
                    projection = ((hint[0]-a[0])*u[0]+(hint[1]-a[1])*u[1])/norm
                    splits.add(max(0.0, min(outer, projection)))
                    projection -= middle*(v[0]*u[0]+v[1]*u[1])/norm
                    splits.add(max(0.0, min(outer, projection)))
                for split in _splits(splits, 0.0, outer, integral):
                    first = _point(a, u, split)
                    second = _point(first, v, middle)
                    candidates.add(tuple(_compact((a, first, second, b))))
            # Opposite outer legs are meaningful only with a perpendicular
            # middle leg; other combinations introduce acute/backward turns.
            if u[0]*v[0]+u[1]*v[1] == 0:
                low = max(0.0, outer)
                high = (limit-middle*math.hypot(*v)+outer*math.hypot(*u))/(2*math.hypot(*u))
                if high < low-_EPS:
                    continue
                splits = {low, high, (low+high)/2, low+(high-low)*.25, low+(high-low)*.75}
                norm = u[0]*u[0]+u[1]*u[1]
                for hint in hints:
                    projection = ((hint[0]-a[0])*u[0]+(hint[1]-a[1])*u[1])/norm
                    splits.add(max(low, min(high, projection)))
                for split in _splits(splits, low, high, integral):
                    first = _point(a, u, split)
                    second = _point(first, v, middle)
                    candidates.add(tuple(_compact((a, first, second, b))))
    # Two 45-degree bends with a sliding middle leg, e.g. NE,E,SE over an
    # obstacle or E,NE,N through a clear diagonal corridor. The first/last
    # directions are perpendicular, so there are only sixteen bounded cases.
    for u in _DIRECTIONS:
        for w in _DIRECTIONS:
            if deadline is not None and time.monotonic() >= deadline:
                return iter(())
            if u[0]*w[0]+u[1]*w[1] != 0:
                continue
            v = (int(math.copysign(1, u[0]+w[0])) if u[0]+w[0] else 0,
                 int(math.copysign(1, u[1]+w[1])) if u[1]+w[1] else 0)
            r0, t0 = _terms(delta, u, w)
            r1, t1 = _terms((-v[0], -v[1]), u, w)
            low, high = 0.0, limit/math.hypot(*v)
            # Each inequality has the form constant+slope*s >= 0.
            for constant, slope in ((r0, r1), (t0, t1),
                    (limit-r0*math.hypot(*u)-t0*math.hypot(*w),
                     -r1*math.hypot(*u)-math.hypot(*v)-t1*math.hypot(*w))):
                if abs(slope) <= _EPS:
                    if constant < -_EPS:
                        high = -1
                        break
                elif slope > 0:
                    low = max(low, -constant/slope)
                else:
                    high = min(high, -constant/slope)
            if high < low-_EPS:
                continue
            splits = {low, high, (low+high)/2, low+(high-low)*.25, low+(high-low)*.75}
            for hint in hints:
                projection = ((hint[0]-a[0])*u[0]+(hint[1]-a[1])*u[1])/(u[0]*u[0]+u[1]*u[1])
                if abs(r1) > _EPS:
                    splits.add(max(low, min(high, (projection-r0)/r1)))
                projection = ((b[0]-hint[0])*w[0]+(b[1]-hint[1])*w[1])/(w[0]*w[0]+w[1]*w[1])
                if abs(t1) > _EPS:
                    splits.add(max(low, min(high, (projection-t0)/t1)))
            for middle in _splits(splits, low, high, integral):
                first = _point(a, u, max(0.0, r0+r1*middle))
                second = _point(first, v, middle)
                candidates.add(tuple(_compact((a, first, second, b))))
    return (candidate for candidate in candidates if len(candidate) >= 2 and
            polyline_length(candidate) <= limit+1e-8 and not _sharp_turns(candidate))


def simplify_polyline(points, legal, max_added_length=0.0, *, max_window_corners=48,
                      max_segment_checks=4096, short_step_length=None, deadline=None):
    """Reduce bends and short oscillations, with unchanged endpoints and legal legs.

    Coordinates and the length allowance share the caller's units. ``legal``
    receives each complete proposed segment. Clearance tests are cached, and
    both corner windows and total callback checks are bounded. A candidate
    needs fewer contextual direction changes (including its two joins), or
    the same count and less length; it cannot introduce an acute internal turn
    or increase acute turns at the joins. The original route's complete length
    is the fixed reference, so repeated edits cannot compound the allowance.
    """
    original = [tuple(point) for point in points]
    if any(len(point) != 2 or not all(math.isfinite(value) for value in point) for point in original):
        raise ValueError('polyline points must contain two finite coordinates')
    if not math.isfinite(max_added_length) or max_added_length < 0:
        raise ValueError('max_added_length must be finite and non-negative')
    if max_window_corners < 2 or max_segment_checks < 0:
        raise ValueError('invalid readability search bounds')
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError('readability deadline must be a finite monotonic timestamp')
    route = _compact(original)
    def expired():
        return deadline is not None and time.monotonic() >= deadline
    if len(route) < 3 or expired():
        return route
    length_limit = polyline_length(original)+max_added_length
    cache = {}
    checks = 0

    def clear(a, b):
        nonlocal checks
        if expired():
            return False
        key = tuple(sorted((a, b)))
        if key not in cache:
            if checks >= max_segment_checks:
                return False
            checks += 1
            cache[key] = bool(legal(a, b))
        return cache[key]

    def context_clear(candidate, prefix, suffix, old_context):
        # A shortcut can align with an unchanged neighbouring leg. Exact
        # compaction then makes one longer segment: screen that merged segment
        # too, rather than assuming legal constituent legs authorise the join.
        old_legs = {tuple(sorted((a, b))) for a, b in zip(old_context, old_context[1:])}
        compact_context = _compact(prefix+list(candidate)+suffix)
        proposed = list(zip(candidate, candidate[1:]))
        proposed.extend((a, b) for a, b in zip(compact_context, compact_context[1:])
                        if tuple(sorted((a, b))) not in old_legs)
        return all(clear(a, b) for a, b in proposed)

    if __package__:
        from .route_readability_policy import metrics, quality_key
    else:
        from route_readability_policy import metrics, quality_key
    def measurements(points):
        if short_step_length is not None:
            return metrics(points, short_step_length)
        return {'lengthMm': polyline_length(points), 'bendCount': direction_changes(points),
                'backtrackCount': 0, 'shortClusterCount': 0}

    def improves(new, old):
        fields = ('bendCount', 'backtrackCount', 'shortClusterCount')
        return all(new[field] <= old[field] for field in fields) and (
            any(new[field] < old[field] for field in fields)
            or new['lengthMm'] < old['lengthMm']-1e-8)

    if short_step_length is not None:
        # Visit detected defects across the whole chain before the general
        # long-window search. An obstructed prefix must not consume the entire
        # callback quota and hide a short dogleg farther along the route.
        attempted = set()
        while checks < max_segment_checks and not expired():
            issues = short_bend_issues(route, short_step_length, short_step_length*4)
            issue = next((r for r in issues
                          if tuple(route[r['startIndex']:r['endIndex']+1]) not in attempted), None)
            if issue is None:
                break
            start, end = issue['startIndex'], issue['endIndex']
            section = route[start:end+1]
            attempted.add(tuple(section))
            prefix = route[max(0, start-2):start]
            suffix = route[end+1:end+3]
            context = prefix+section+suffix
            old_quality, old_sharp = measurements(context), _sharp_turns(context)
            old_length = polyline_length(section)
            limit = old_length+max(0., length_limit-polyline_length(route))
            candidates = []
            for candidate in _candidates(section[0], section[-1], section, limit, deadline):
                if expired():
                    break
                new_context = prefix+list(candidate)+suffix
                quality = measurements(new_context)
                if improves(quality, old_quality) \
                        and _sharp_turns(new_context) <= old_sharp:
                    candidates.append((quality_key(quality), candidate))
            window_quota = min(max_segment_checks, checks+48)
            for _, candidate in sorted(candidates):
                if checks >= window_quota or expired():
                    break
                if context_clear(candidate, prefix, suffix, context):
                    route[start:end+1] = candidate
                    route = _compact(route)
                    break

    for _ in range(2):
        if expired():
            break
        changed = False
        start = 0
        current_length = polyline_length(route)
        while start+2 < len(route) and checks < max_segment_checks and not expired():
            farthest = min(len(route)-1, start+max_window_corners)
            spans = {2, 3, 4, 8, 16, 24, 32, max_window_corners, farthest-start}
            ends = sorted((start+span for span in spans if start+2 <= start+span <= farthest), reverse=True)
            accepted = None
            for end in ends:
                if expired():
                    break
                section = route[start:end+1]
                old_length = polyline_length(section)
                limit = old_length+max(0.0, length_limit-current_length)
                prefix = route[max(0, start-2):start]
                suffix = route[end+1:end+3]
                context = prefix+section+suffix
                old_quality = measurements(context)
                old_sharp = _sharp_turns(context)
                hints = section if len(section) <= 18 else [section[round(i*(len(section)-1)/17)] for i in range(18)]
                candidates = []
                for candidate in _candidates(section[0], section[-1], hints, limit, deadline):
                    if expired():
                        break
                    candidate_context = prefix+list(candidate)+suffix
                    length = polyline_length(candidate)
                    quality = measurements(candidate_context)
                    if improves(quality, old_quality) \
                            and _sharp_turns(candidate_context) <= old_sharp:
                        candidates.append((quality_key(quality), length, candidate))
                # Stable geometry order makes outcomes independent of set/hash
                # iteration and of the platform's Python randomisation seed.
                for _, length, candidate in sorted(candidates):
                    if expired():
                        break
                    if context_clear(candidate, prefix, suffix, context):
                        accepted = end, length, candidate, old_length
                        break
                if accepted is not None:
                    break
            if accepted is None:
                start += 1
                continue
            end, length, candidate, old_length = accepted
            route[start:end+1] = candidate
            route = _compact(route)
            current_length += length-old_length
            changed = True
            start += max(1, len(candidate)-2)
        if not changed:
            break
    return route
