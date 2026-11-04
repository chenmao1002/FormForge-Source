"""Exact physical interference for rigid parts, including overlapping source solids."""


def intersection_volume(first, second, *, first_leaves=None, second_leaves=None):
    from product_ide_3d.assembly_cad import bounds_overlap

    # A source PCBA remains one output part. Its independent package/terminal
    # solids can overlap internally; a Boolean on that unfused compound can
    # silently omit intersections. Intersect its leaves, then union the common
    # regions so an internal overlap cannot hide or double-count a collision.
    # A build can supply its fixed output instances' leaves and bounds. Keep
    # this cache owned by that build, never by a global shape/pose identity.
    first_solids = first.Solids() if first_leaves is None else None
    second_solids = second.Solids() if second_leaves is None else None
    first_count = len(first_solids) if first_leaves is None else len(first_leaves)
    second_count = len(second_solids) if second_leaves is None else len(second_leaves)
    if first_count == second_count == 1:
        return first.intersect(second).Volume()
    left = first_leaves if first_leaves is not None else [(solid, solid.BoundingBox()) for solid in first_solids]
    right = second_leaves if second_leaves is not None else [(solid, solid.BoundingBox()) for solid in second_solids]
    common = []
    for a, box_a in left:
        for b, box_b in right:
            if not bounds_overlap(box_a, box_b):
                continue
            overlap = a.intersect(b)
            if overlap.Volume() > 0:
                common.append(overlap)
    if not common:
        return 0
    result = common[0].fuse(*common[1:]) if len(common) > 1 else common[0]
    if not result.isValid():
        raise ValueError('Cannot form a valid union of solid-pair interference regions')
    return result.Volume()


def blocking_witness(first, second, threshold=1e-5, *, first_leaves=None, second_leaves=None):
    """Exact positive contact sufficient for a stop; volume may be a lower bound."""
    from product_ide_3d.assembly_cad import bounds_overlap
    left=first_leaves if first_leaves is not None else [(s,s.BoundingBox()) for s in first.Solids()]
    right=second_leaves if second_leaves is not None else [(s,s.BoundingBox()) for s in second.Solids()]
    small=[]
    for a,ba in left:
        for b,bb in right:
            if not bounds_overlap(ba,bb):continue
            region=a.intersect(b);volume=region.Volume()
            if volume>threshold:return dict(volumeMm3=volume,volumeScope='exact-leaf-witness-lower-bound')
            if volume>0:small.append(region)
    if not small:return dict(volumeMm3=0,volumeScope='exact-total')
    merged=small[0].fuse(*small[1:]) if len(small)>1 else small[0]
    if not merged.isValid():raise ValueError('Cannot form valid small-contact union')
    return dict(volumeMm3=merged.Volume(),volumeScope='exact-total')
