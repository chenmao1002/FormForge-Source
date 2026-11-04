"""Pure source-copper ownership shared by the host and native local planner."""


def moved_ids(source_poses,moves):
    return {m['objectId'] for m in moves if m['at']!=source_poses[m['objectId']]['at'] or
            m['rotation']%360!=source_poses[m['objectId']]['rotation']%360}


def for_pose(removals,owners,link_ids,source_poses,moves,copper_rework=None,pre_copper_operations=(),contact_rework=None):
    moved=moved_ids(source_poses,moves)
    required=set(link_ids)
    active=[r for r in removals if r['objectId'] not in owners or r['objectId'] in required or
            moved.intersection(owners[r['objectId']])]
    # An explicit obstacle chain stays untouched in source/base poses. Only a
    # concrete detour replaces it, including its original end caps.
    rework=copper_rework or contact_rework
    return active+(rework['removeCopper'] if rework and pre_copper_operations else [])


def comparison_quality(footprints,tracks,source_ids,scope_ids):
    if __package__:
        from .routing_rules import trace_chains
        from .route_readability_policy import metrics
    else:
        from routing_rules import trace_chains
        from route_readability_policy import metrics
    source_ids=set(source_ids);scope_ids=set(scope_ids)
    selected=[];anchors=[]
    for t in tracks:
        if t['type']=='via' or t['id'] in scope_ids or t['id'] not in source_ids:selected.append(t)
        elif t['type']=='segment':anchors.extend((t['start'],t['end']))
    fields=('lengthMm','bendCount','backtrackCount','shortClusterCount');totals=dict.fromkeys(fields,0)
    for chain in trace_chains(footprints,selected,extra_anchors=anchors):
        row=metrics(chain['points'])
        for key in fields:totals[key]+=row[key]
    return {**totals,'scope':'The same declared affected source copper, including retained segments, plus new copper; fixed external contacts remain anchors.'}
