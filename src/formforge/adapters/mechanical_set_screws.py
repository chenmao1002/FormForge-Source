"""Nominal geometry for a nut-backed set screw; never a force or creep rating."""
import math


def inspect_set_screw(joint, shapes, catalog, profile, reference_parts):
    import cadquery as cq
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    from mechanical_collision import blocking_witness
    from mechanical_engineering import standard_shape

    spec=catalog['hardware'].get(joint.screw_id)
    receiver=catalog['hardware'].get(joint.receiver_id)
    if not spec or spec.get('kind')!='screw' or not spec.get('headless'):
        raise ValueError('set_screw_nut requires a source-backed headless screw')
    if not receiver or receiver.get('kind')!='nut':
        raise ValueError('set_screw_nut requires a source-backed nut')
    origin=cq.Vector(*joint.bearing);axis=cq.Vector(*joint.axis).normalized()

    def placed(shape, point, clock=0):
        cross=cq.Vector(0,0,1).cross(axis)
        if cross.Length>1e-8:
            shape=shape.rotate((0,0,0),cross.toTuple(),math.degrees(math.acos(max(-1.,min(1.,axis.z)))))
        elif axis.z<0:shape=shape.rotate((0,0,0),(1,0,0),180)
        if clock:shape=shape.rotate((0,0,0),axis.toTuple(),clock)
        return shape.translate(point)

    def exact_pose(actual, expected):
        return actual.cut(expected).Volume()<1e-5 and expected.cut(actual).Volume()<1e-5

    def volume(a,b):
        return blocking_witness(a,b)['volumeMm3']

    screw=shapes[joint.screw_part];nut=shapes[joint.receiver_part]
    host=shapes[joint.receiver_host_part];pressure=shapes[joint.pressure_part]
    receiver_origin=origin+axis*joint.receiver_start_mm
    pose=exact_pose(screw,placed(standard_shape(joint.screw_id),origin)) and exact_pose(nut,placed(standard_shape(joint.receiver_id),receiver_origin,joint.receiver_clock_degrees))
    matching=spec['thread']==receiver['thread'] and spec['pitch']==receiver['pitch']
    engagement=max(0,min(spec['length']-joint.receiver_start_mm,receiver['height']))
    obstacles={n:s for n,s in shapes.items() if n not in {joint.screw_part,joint.receiver_part,*reference_parts}}
    radius=spec['diameter']/2+joint.path_clearance_mm
    path=cq.Solid.makeCylinder(radius,joint.receiver_start_mm,origin,axis)
    path_rows=[dict(part=n,volumeMm3=volume(path,s)) for n,s in obstacles.items()]
    tool=cq.Solid.makeCylinder(joint.tool_radius_mm,joint.tool_travel_mm,origin,-axis)
    tool_rows=[dict(part=n,volumeMm3=volume(tool,s)) for n,s in obstacles.items() if n not in joint.tool_removed_parts]
    actual_collisions=[dict(part=n,volumeMm3=volume(screw,s)) for n,s in obstacles.items()]
    intrusion=sum(volume(nut,shapes[n]) for n in joint.members)
    support=volume(nut.translate(-axis*.05),host)
    anti_rotation=volume(nut.rotate(origin.toTuple(),(origin+axis).toTuple(),30),host)>1e-5

    outer=cq.Workplane('XY').polygon(6,2*receiver['acrossFlats']/math.sqrt(3)).extrude(receiver['height']).val()
    outer=placed(outer,receiver_origin,joint.receiver_clock_degrees)
    offset=cq.Vector(*joint.receiver_entry_offset)
    if offset.Length<1e-6:raise ValueError('Nut entry offset must be nonzero')
    sweeps=[cq.Shape.cast(BRepPrimAPI_MakePrism(face.wrapped,gp_Vec(*offset.toTuple())).Shape()) for face in outer.Faces() if face.normalAt().dot(offset)>1e-8]
    swept=outer.fuse(*sweeps)
    entry=sum(volume(swept,shapes[n]) for n in joint.members)

    point=cq.Vector(*joint.pressure_point);tip=origin+axis*spec['length']
    tip_error=(point-tip).Length
    pressure_distance=cq.Vertex.makeVertex(*point.toTuple()).distance(pressure)
    pressure_passed=tip_error<1e-5 and pressure_distance<1e-5 and volume(screw,pressure)<1e-5
    group=cq.Compound.makeCompound([screw,nut]);capture=[]
    for direction in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]:
        witness=volume(group.translate(tuple(v*joint.capture_displacement_mm for v in direction)),host)
        capture.append(dict(direction=direction,displacementMm=joint.capture_displacement_mm,volumeMm3=witness,passed=witness>1e-5))
    passed=pose and matching and engagement+1e-5>=joint.engagement_mm and support>1e-5 and anti_rotation and intrusion<1e-5 and entry<1e-5 and pressure_passed and all(r['volumeMm3']<1e-5 for r in path_rows+tool_rows+actual_collisions) and all(r['passed'] for r in capture)
    return dict(id=joint.id,method=joint.method,receiverHostPart=joint.receiver_host_part,hardwarePoseMatched=pose,
        bearingReference='drive-face',headBearingPassed=None,headBearingApplicability='not-applicable-headless',
        receiverBearingMm3=support,receiverAntiRotationBlocked=anti_rotation,receiverEntryCollisionMm3=entry,receiverEntryPassed=entry<1e-5,
        receiverCollisionMm3=intrusion,receiverClockDegrees=joint.receiver_clock_degrees,matchingThread=matching,
        engagementMm=engagement,requiredEngagementMm=joint.engagement_mm,pathBlockers=path_rows,
        toolBlockers=[r for r in tool_rows if r['volumeMm3']>1e-5],toolRemovedParts=joint.tool_removed_parts,toolAccessReason=joint.tool_access_reason,
        screwBodyCollisions=[r for r in actual_collisions if r['volumeMm3']>1e-5],pressurePart=joint.pressure_part,
        pressurePoint=list(joint.pressure_point),pressureTipDatumErrorMm=tip_error,pressureSurfaceDistanceMm=pressure_distance,
        pressureContactPassed=pressure_passed,hardwareCapture=capture,assembly=joint.assembly,passed=passed,
        collisionEvaluation='Exact positive leaf witnesses; positive volumes may be lower bounds.',
        scope='Source-backed nominal pressure contact, nut load shoulder/entry/antirotation, screw/tool clearance and six declared hardware-group displacement samples. No clamp force, thread strength, torque, frictional retention, creep, continuous escape or physical qualification.')
