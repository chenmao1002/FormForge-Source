"""Validate rounded routing proposals before copper emission; no board mutation."""
import math
import time

if __package__:
    from .route_path_readability import simplify_polyline, _sharp_turns
    from .route_readability_policy import metrics
else:
    from route_path_readability import simplify_polyline, _sharp_turns
    from route_readability_policy import metrics


def _octilinear(points):
    for a, b in zip(points, points[1:]):
        dx, dy = abs(a[0]-b[0]), abs(a[1]-b[1])
        # Compare nanometre integer deltas, rather than a broad clearance epsilon.
        nx, ny = round(dx*1_000_000), round(dy*1_000_000)
        if not nx+ny or nx and ny and nx != ny:
            return False
    return True


def _compact_native_segments(points):
    """Drop emitted collinear vertices without floating cross-product noise.

    Work in the same integer nanometres as the final copper check, retaining
    the caller's original coordinates at every surviving point. A merged leg
    still has to pass the complete native legality callback below.
    """
    kept = []
    for p in points:
        q = tuple(round(v * 1_000_000) for v in p)
        if kept and q == kept[-1][1]:
            continue
        while len(kept) >= 2:
            a, b = kept[-2][1], kept[-1][1]
            u = (b[0]-a[0], b[1]-a[1]); v = (q[0]-b[0], q[1]-b[1])
            if u[0]*v[1] != u[1]*v[0] or u[0]*v[0]+u[1]*v[1] <= 0:
                break
            kept.pop()
        kept.append((p, q))
    result = [p for p, _ in kept]
    if result:
        result[0] = points[0]
        result[-1] = points[-1]
    return result


def prepare_path(points, legal, deadline, style=None):
    """Keep contacts, bounded length growth, monotone metrics and full-leg legality.

    The callback tests finite-width native segments against this board's current
    geometry. The 0.2s/256-check legacy or 0.75s/768-check tidy cap shares the routing deadline. Final
    validation is mandatory even if the cooperative search has exhausted its cap.
    Only an explicit style allows bounded growth for a measured improvement.
    No inherited 1021C preference or existing-copper rewrite is applied.
    """
    original = []
    for p in points:
        if len(p) != 2 or not all(math.isfinite(v) for v in p):
            raise ValueError('Routing path requires finite 2D coordinates')
        if not original or list(p) != original[-1]:
            original.append(list(p))
    before = metrics(original)
    allowance = min(style['maxAddedLengthMm'], before['lengthMm']*style['maxAddedLengthRatio']) if style else 0.
    started = time.monotonic()
    local_deadline = min(deadline, started+(.75 if style else .2))
    checks = 0
    def clear(a, b):
        nonlocal checks
        checks += 1
        return legal(a, b)
    proposed = simplify_polyline(_compact_native_segments(original), clear, allowance, max_window_corners=24,
                                 max_segment_checks=768 if style else 256, short_step_length=.5,
                                 deadline=local_deadline)
    # Copper is emitted on KiCad's nanometre grid. Recheck what will be saved,
    # including any long segment created by compaction and both unchanged ends.
    candidate = [[round(v, 6) for v in p] for p in proposed]
    after = metrics(candidate)
    fields = ('bendCount', 'backtrackCount', 'shortClusterCount')
    valid = (len(candidate) == len(original) == 0 or
             bool(candidate) and candidate[0] == original[0] and candidate[-1] == original[-1])
    valid = valid and _octilinear(candidate) and after['lengthMm'] <= before['lengthMm']+allowance+1e-8
    valid = valid and all(after[k] <= before[k] for k in fields)
    valid = valid and _sharp_turns(candidate) <= _sharp_turns(original)
    if style:
        if __package__:from .routing_rules import corner_audit
        else:from routing_rules import corner_audit
        def corners(points):
            return corner_audit([dict(id=str(i),type='segment',net='',layer='',start=a,end=b)for i,(a,b)in enumerate(zip(points,points[1:]))])['exposedCornerCount']
        valid = valid and corners(candidate)<=corners(original)
    if after['lengthMm']>before['lengthMm']+1e-8:
        valid = valid and any(after[k]<before[k] for k in fields)
    if valid:
        valid = all(legal(a, b) for a, b in zip(candidate, candidate[1:]))
    if not valid:
        candidate = original
        after = before.copy()
    # Search clearance is advisory until the final whole emitted path is checked.
    if not all(legal(a, b) for a, b in zip(candidate, candidate[1:])):
        raise ValueError('Routing path failed final full-segment native geometry validation')
    improved = any(after[k] < before[k] for k in fields) or after['lengthMm'] < before['lengthMm']-1e-8
    return candidate, dict(before=before, after=after, improved=improved,
        fallback=not valid, searchSegmentChecks=checks,
        searchBudgetExhausted=time.monotonic() >= local_deadline,
        maxAddedLengthMm=allowance, elapsedSeconds=round(time.monotonic()-started, 6))


def summarize(paths):
    fields = ('lengthMm', 'bendCount', 'backtrackCount', 'shortClusterCount')
    totals = {stage: {key: sum(p[stage][key] for p in paths) for key in fields}
              for stage in ('before', 'after')}
    return dict(strategy='bounded-octilinear-simplification-v1', pathCount=len(paths),
                improvedPathCount=sum(p['improved'] for p in paths), maxAddedLengthMm=sum(p.get('maxAddedLengthMm',0.) for p in paths),
                **totals, paths=paths,
                scope='New ordinary-route same-layer proposal geometry only; native post-save checks remain required.')


def compare_proposals(candidate_ops,candidate_paths,baseline_ops,baseline_paths,style=None):
    """Keep the first-contact fallback unless the fully prepared path is no worse.

    Both proposals have already passed full native segment legality. This is a
    bounded comparison of new copper, not a terminal-network or board proof.
    """
    def measure(operations,paths):
        return {**summarize(paths)['after'],
                'viaCount':sum(op['kind']=='add_via' for op in operations)}
    candidate=measure(candidate_ops,candidate_paths);baseline=measure(baseline_ops,baseline_paths)
    allowance=min(style['maxAddedLengthMm'],baseline['lengthMm']*style['maxAddedLengthRatio']) if style else 0.
    fields=('bendCount','shortClusterCount','backtrackCount','viaCount')
    accepted=(candidate['lengthMm']<=baseline['lengthMm']+allowance+1e-8 and
              all(candidate[k]<=baseline[k] for k in ('bendCount','shortClusterCount','backtrackCount','viaCount')))
    if candidate['lengthMm']>baseline['lengthMm']+1e-8:
        accepted = accepted and any(candidate[k]<baseline[k] for k in fields[:3])
    return dict(useCompleteCostCandidate=accepted,candidate=candidate,firstContact=baseline,
                maxAddedLengthMm=allowance,scope='Prepared ordinary-route proposals only; bounded growth requires a readability improvement and no via regression.')
