"""Host contract for a bounded pair inside a real multi-terminal node.

This describes selected pads; it never reduces the global net or its terminals.
The existing host still selects every affected net and checks complete source
physical-group preservation and native candidate geometry during adoption.
"""


POLICY = 'bounded-shared-node-two-terminal-pair-v1'
SCOPE = ('Two selected two-terminal footprints within the unchanged global net; '
         'all other node pads are fixed external endpoints. This is a topology '
         'selection only; full source physical groups, affected nets and native '
         'geometry/adoption checks remain required.')


def _terminal(footprint, pad):
    return dict(reference=footprint['reference'], footprintId=footprint['id'],
                pin=pad['number'], at=list(pad['at']), id=pad['id'])


def prepare(snapshot, local):
    """Return a pair-compatible descriptor only for explicit shared-node mode."""
    topology = local.get('linkTopology', 'series-pair')
    if topology == 'series-pair':
        return None
    if topology != 'shared-node':
        raise ValueError('Unknown local link topology.')

    references = local['references']
    if len(references) != 2 or len(set(references)) != 2:
        raise ValueError('Shared-node selection requires two distinct references.')
    selected = []
    for reference in sorted(references):
        matches = [fp for fp in snapshot['footprints'] if fp['reference'] == reference]
        if len(matches) != 1:
            raise ValueError('Shared-node reference is missing or ambiguous: ' + reference)
        selected.append(matches[0])
    selected_ids = {fp['id'] for fp in selected}
    if len(selected_ids) != 2:
        raise ValueError('Shared-node footprints must have distinct physical UUIDs.')

    net = local['linkNet']
    selected_pads = []
    outer_nets = []
    for fp in selected:
        electrical = [pad for pad in fp['pads'] if pad.get('net')]
        if len(electrical) != 2:
            raise ValueError('Shared-node selection requires ordinary two-terminal footprints.')
        linked = [pad for pad in electrical if pad['net'] == net]
        if len(linked) != 1:
            raise ValueError('Each shared-node footprint requires exactly one link-net pad.')
        selected_pads.append(_terminal(fp, linked[0]))
        outer_nets.append(next(pad['net'] for pad in electrical if pad is not linked[0]))
    if net in outer_nets or outer_nets[0] == outer_nets[1]:
        raise ValueError('Shared-node outer nets must be distinct and different from the link net.')

    node_pads = [_terminal(fp, pad) for fp in snapshot['footprints']
                 for pad in fp['pads'] if pad.get('net') == net]
    node_ids = [pad['id'] for pad in node_pads]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError('Shared-node pads require unique physical UUIDs.')
    external = [pad for pad in node_pads if pad['footprintId'] not in selected_ids]
    if len(node_pads) < 3 or not external:
        raise ValueError('Shared-node selection requires at least three pads and a third footprint.')
    if {pad['reference'] for pad in external} & set(local.get('companionReferences', [])):
        raise ValueError('Shared-node fixed external footprints cannot be moving companions.')
    locked = sorted(fp['reference'] for fp in selected if fp.get('locked'))
    if len(locked) == 2:
        raise ValueError('Both shared-node footprints are locked.')

    return dict(policy=POLICY, linkTopology='shared-node', net=net,
                references=sorted(references), footprintIds=sorted(selected_ids),
                padIds=sorted(pad['id'] for pad in selected_pads), pads=selected_pads,
                lockedReferences=locked, objectIds=[],
                nodeTerminalCount=len(node_pads), nodePadIds=sorted(node_ids),
                fixedExternalPads=sorted(external, key=lambda pad: (pad['reference'], pad['id'])),
                fixedExternalFootprintIds=sorted({pad['footprintId'] for pad in external}),
                scope=SCOPE)
