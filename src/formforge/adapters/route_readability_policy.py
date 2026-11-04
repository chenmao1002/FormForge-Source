"""Geometric readability metrics extracted from 1021C; no inherited preferences."""


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
    if __package__:
        from .route_path_readability import _compact, direction_changes, polyline_length, short_bend_issues
    else:
        from route_path_readability import _compact, direction_changes, polyline_length, short_bend_issues
    points = _compact(points)
    findings = short_bend_issues(points, short_step_length, short_step_length * 4)
    return {'lengthMm': polyline_length(points), 'bendCount': direction_changes(points),
            'backtrackCount': _clusters([row for row in findings if row['kind'] == 'local-backtracking']),
            'shortClusterCount': _clusters([row for row in findings if row['kind'] == 'short-alternating-bends'])}


def quality_key(measurements):
    return (measurements['backtrackCount'], measurements['shortClusterCount'],
            measurements['bendCount'], measurements['lengthMm'])


