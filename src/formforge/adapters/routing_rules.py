"""Explicit routing style and centreline topology; neither is an SI/PI proof.

Rules are our own implementation. KiCad optimizer and public hardware were
studied for corridor/endpoint discipline, not copied or used as a router.
"""
import collections
import math

STYLE = dict(maxAddedLengthMm=2., maxAddedLengthRatio=.15,
             bendPenaltyMm=.25, padExitLengthMm=.15)

PCB_WORKFLOW_POLICY = dict(
    id='compact-layout-component-side-legends-v1', execution='agent-guided-existing-tools',
    compactLayout='compare-feasible-outline-and-connector-arrangements-before-routing',
    copperLayers='explicit-user-or-project-choice', interfaceLegends='component-side-first-with-recorded-exceptions',
    thermalReview='actual-supply-branches-and-qualified-reference-temperature',
    referenceReview='declared-current-edge-length-scope-with-raw-findings-retained',
    automaticBoardShrink=False, automaticLayerChange=False, manufacturingApproval=False)

PCB_AGENT_INSTRUCTIONS = (
    'pcb_plan按需使用：只有groups时默认轻量读取真实器件/针网，跳过接口试放、出口/通道及走线审计；需要接口检查再传probeInterfaces=true。读取performedChecks/skippedChecks，跳过不算通过，不要为单纯分区汇总强制开启几何诊断。'
    '布局前用pcb_plan显式登记功能组及来源、分区与锚点，读取跨组真实引脚网络及引脚侧再规划接口和走线通道。通道以真实net/points/width/clearance/layers/source检查，不生成铜或禁布区。失败后先读layoutFeedback.nextActions，用同源routeJobId或escapeTargets调用pcb_plan，一次获取真实阻挡、可动候选和全部受影响网络；封装内部线宽冲突先审器件/规则，不重复挪位置或扩大搜索预算。建议不是执行许可或原生验收。'
    '新板布局及用户要求缩板时，布线前比较实际空间利用：按真实器件本体/庭院、固定与工具包络、接头访问、供电及连接通道安排功能区；比较接口相邻边或相对边、旋转和局部重排后，再用现有初始化/编辑入口选择更紧凑板框。记录原/新尺寸、面积变化及保留条件，不把减少空白等同可布通或散热合格。48x33mm等样例尺寸不是通用默认，旧板不自动缩小。层数遵循用户/工程明确选择，不为普通投影提示擅自增加铜层，也不将某板双层要求推广为所有板必须双层。'
    '布局前必须读取layoutFeedback.connectorAccess，主动核对外接接口朝向与板边。原生PCB Edge基准可确定朝外方向，不能把USB朝板内的板子当作完成；先规划建议位姿和受影响铜线重布，再检查真实配对件插拔包络。缺少基准须查封装/数据手册，不按名称猜方向。已知错误的新摆放会被拒绝，旧问题保留可见；DRC通过不代表能插线。'
    '默认PCB板框圆角，除非用户有要求；仅用户明确要求直角时允许cornerRadius=0，并保存squareCornerReason。新建/修改板框缺省3mm圆角，小板不超过短边1/4，实际生成原生四段圆弧。PCB固定定位孔默认摆放在四个角（整齐对齐呈方形摆放），除非用户有其他要求。'
    'PCB三维与机械装配中，电路板和全部元器件作为一个PCBA总成显示、导入和移动。机械模型使用pcb_geometry_export的assembly.step作为一个STEP特征和一个输出零件，不逐器件创建机械输出零件；保留源颜色、孔洞和内部元器件几何供确定性检查。'
    'pcb_initialize默认执行mountingPlan自动四孔规划；pcb_edit可单独提交mountingPlan重新规划，custom/none须reason/source。不允许以普通文字说明代替结构化例外。定位孔规划保存在版本中，后续孔位/板框/组件侵占由统一候选验收拒绝。'
    '新设计默认先规划四个角的四个固定定位孔，再布局和布线；不能以尚未选择固定方式为由省略。长方形板按板框四角对齐，不为孔阵列强改板形。孔径、边距与双面螺钉/工具空间按实际固定件选择，不凭默认孔数杜撰规格。用户指定孔位、真实配合孔位和机械/电气净空约束优先；冲突时明确说明并调整方案，不静默改孔或省略。读取layoutFeedback.mountingReview；缺孔、不对齐或不支持板形须处理或明确例外理由。旧板不自动补孔；aligned_corner_pattern只证明已声明孔的四角布局，不证明可安装。'
    '涉及外壳时，PCB设计前先读取mechanical_schema(kind="enclosure").pcbPlanning，确定固定方法、板框/孔位、接头朝向和配对件插拔空间、圆角；所选支柱固定才要求至少三个非共线纯NPTH孔，不能等布完板才补结构要求。'
    'PCB设计先读pcb_read的原生对象、layoutFeedback.placementReview、layoutFeedback.routingQuality、layoutFeedback.padExits和powerFeeds。'
    '根据明确的参考网/返回端/铜区声明安排地连接，不按GND等网名猜角色。拟由铺铜连接的地端先保留并铺铜，读取实际填充后的物理分组，只对未连接分组补短线或过孔；不要提前给每个地端布满线孔。去耦回流及参考检查仍须满足已声明条件，不能把同名铜区当作连接证明。'
    '按实际供电分支补功耗与温升预算：区分直接供电的芯片核心、外部IO轨、稳压器负载、输出到外部负载的功率及对端送入本板终端的热；不把整板输入电流全部当作LDO输出。来源、负载、环境、动态分配及热阻资格用schematic_review的sources/facts/calculations记录；热阻参考值不等于当前板实际热阻，缺失不杜撰。参考面检查按电流、实际边沿、板内路径及用途分级，低波特率不能代替边沿判断；普通低功率短线板不将所有参考铜投影全绿作为通用完成门槛。保留原始缺口与已声明硬条件，记录当前适用范围和取舍；USB、快沿、局部去耦/ESD返回及地实际连通仍复核，不将几何短线筛查称为SI/PI或EMC通过。'
    '铺铜默认热焊盘padConnection=thermal，明确并保存thermalGap和thermalSpoke，以便焊接；全连接full仅用于有明确电气/热理由的局部铜区。重填后检查原生DRC、源物理连接和剩余未连，不能用热焊参数保证可焊性或制造资格。'
    '全局预算反复耗在受阻网时，按邻近未连分组分批或显式降低maxPairAttemptsPerConnection，给后续网络留预算；no_legal_pad_exit先检查具体端点和相邻出口，不能把受限失败当成不可布通。功能丝印须读取当前原生文字/板边规则并避开封装轮廓和焊盘开窗。'
    '反复达到扩展上限时可显式选择searchHeuristicWeight>1的近似探索，结果不证明最优；比较实际线长/转弯/过孔并复核全部硬约束，不能把加权停止条件说成最短路径证明。'
    '先优化摆放再延长绕线：二端串联组若出现长绕行或两端背向出线，比较原位旋转、交换位置及就近横/竖串排；按相连焊盘中心对齐，不能只对齐器件原点。'
    '同时规划组内短连接、外部供电/负载入口及接地回流，避免供电线横穿组内通道后迫使信号绕器件一圈。placementReview仅是提示，不证明存在可布通路线；根据明确电气角色保留去耦/反馈/极性、已锁定器件及机械条件。'
    '明确的就近去耦连接与接口公共走廊应先形成或预留，再让远端信号经过；不能只摆齐电容却在信号占道后才补其供电短线。直接H/V同轴端子连接不必因网格端口为空而绕行，普通路由会先检查这一无平面转弯候选；净距、本体、实际铜层及其他同网焊盘的中心轴仍检查。pcb_read的焊盘copperLayers和drill来自原生回读，规划换层不能把顶面SMD焊盘假定为背面铜障碍，也不能漏掉通孔与孔铜净距。'
    '密集MCU按真实焊盘同时规划全部待用出口、局部供电颈线、就近去耦和地返回孔，避免先占满引脚外侧再处理其他网络；主干与局部颈线分别决定，后者仍不得小于板规则。单一直出未找到合法孔位只表示当前方案受阻，比较交错孔、45度接入及供电/返回局部调整，并复核整板既有铜，不能靠降低净距或自动缩孔解决。'
    '对可移动候选比较实际线长、转弯、过孔、本体和庭院；保留通过同一原生净距/中心直出/连通/电源节点/成组/参考条件的更优方案，不用降低规则换取评分。'
    '手动编辑与自动布线遵守同一规则：裸露连续走线只用直线及45度过渡，禁止新直角、锐角和齿状折返；'
    '水平/垂直/45度是单段方向，不能据此认为组合后的夹角合规。焊盘出线必须从原生焊盘中心连续直出；真实原生圆形铜焊盘允许中心45度斜出，其余焊盘沿板坐标上、下、左、右。禁止偏心出口及焊盘内部拐弯平移出口，越过真实焊盘铜边界后至少直行0.15mm再用45度转弯；不能只在焊盘内留一小段。圆角矩形、椭圆和外观近圆不能使用圆形例外。'
    '直出长度跨对象和线宽累计；直接水平/垂直连接邻近焊盘或过孔且未转弯可短于0.15mm。允许适当加长，不能牺牲净距或回流。'
    '宽度变化、拆分add_track或接在已有铜端点不能绕过转角检查；焊盘内转向与真实T形分支另列，不是裸露线链直角。'
    '接到既有线端/过孔须同时检查该铜层原线方向；过孔不能豁免同层二分支直角。成组失败读取routeDiagnostics中的具体长度/偏差/扇出限制和拒绝计数，再调整公共走廊，不自动放宽条件。'
    '电感输出先形成输出电容节点再分配，集中多电容逐只登记powerFeeds，反馈取样端点也列入后级。'
    '四层板明确每层用途；可比较In1保留地参考、In2分配电源及少量低速控制，不能因只用表面层反复失败便降低几何规则。被信号/电源占用的In2地铜仅称辅助填充，重新填铜后另验相邻层参考覆盖。电源从已声明输出电容后级既有通孔进入，禁止换层产生上游旁路。'
    '手动清理重复或部分重叠铜、移动器件时必须在同一候选保留全部旧物理连接，必要重接也须同批提交；同net、焊盘网络图一致或减少未连总数不能代替sourceConnectivity通过。合法分支和调长不能仅凭外观相近删除。'
    '同网同层同宽的完全重复/包含直段可用pcb_edit的cleanup_tracks按明确nets单独清理，读取覆盖对象证据；算法保留锁定对象与差分契约整网，部分重叠、蛇形与宽度过渡不自动改。'
    'ESD和去耦角色须有冻结资料SHA、明确器件型号/针号或用户角色声明；先以schematic_review核对实际端子网络，再记录连接器侧/受保护端/电源脚/返回端及有来源的距离或路径限值。不能从网名猜角色；缺证据列为待澄清。现有powerFeeds只强制已声明电容节点拓扑，不等于通用ESD布局、去耦回路或PI证明。'
    '提交后读job_get的adopted、cornerPolicy、padExitPolicy、routingQuality.cornerFindings及padExits.findings，按端子/网络/层/坐标修正被拒候选，不能仅凭DRC为零宣布布线完成。'
    '交付同时给整板及修改区域原生预览，明确未处理的既有问题；不要把丝印矩形当走线转角。'
)


def corner_audit(tracks, pad_contains=None):
    """Audit degree-two copper junctions independently of width/path anchors.

    A native same-net/layer pad containment callback separates hidden turns.
    Interior endpoints on a longer segment are included to identify real T/X
    branches. No footprint bounding rectangle is used as a pad substitute.
    """
    groups=collections.defaultdict(list)
    for t in tracks:
        if t['type']=='segment':groups[t['net'],t['layer']].append(t)
    findings=[];internal=0;branches=0
    for (net,layer),segments in sorted(groups.items()):
        points={tuple(p) for t in segments for p in (t['start'],t['end'])}
        for at in sorted(points):
            rays=collections.defaultdict(set)
            for t in segments:
                if not on_segment(at,t['start'],t['end']):continue
                for p in (t['start'],t['end']):
                    length=math.dist(at,p)
                    if length<1e-7:continue
                    direction=tuple(round((p[i]-at[i])/length,6)for i in (0,1))
                    rays[direction].add(t['id'])
            if len(rays)>2:branches+=1;continue
            if len(rays)!=2:continue
            directions=sorted(rays);a,b=directions
            angle=180-math.degrees(math.acos(max(-1.,min(1.,sum(x*y for x,y in zip(a,b))/math.hypot(*a)/math.hypot(*b)))))
            if angle<89.999:continue
            if pad_contains and pad_contains(net,layer,at):internal+=1;continue
            findings.append(dict(net=net,layer=layer,at=list(at),turnDegrees=round(angle,4),directions=[list(d)for d in directions],
                objectIds=sorted(set().union(*rays.values())),kind='right-angle'if abs(angle-90)<.001 else'acute-corner'))
    return dict(cornerPolicy='no-new-exposed-sharp-corners-v1',exposedCornerCount=len(findings),
        rightAngleCount=sum(r['kind']=='right-angle'for r in findings),acuteCornerCount=sum(r['kind']=='acute-corner'for r in findings),
        padInternalCornerCount=internal,branchJunctionCount=branches,cornerFindings=findings)


def corner_proof(previous, actual):
    """Block new or reworked exposed corners; report untouched legacy copper."""
    quality=actual.get('layoutFeedback',{}).get('routingQuality',{})
    if quality.get('cornerPolicy')!='no-new-exposed-sharp-corners-v1':
        return dict(policy='no-new-exposed-sharp-corners-v1',passed=False,reason='native_corner_audit_missing',findings=[])
    old=previous.get('layoutFeedback',{}).get('routingQuality',{}).get('cornerFindings',[])
    def key(r):return (r['net'],r['layer'],tuple(r['at']),tuple(map(tuple,r['directions'])))
    old_keys={key(r)for r in old};before={t['id']:t for t in previous.get('tracks',[])}
    def geometry(t):return {k:t.get(k)for k in ('type','net','layer','start','end','at','width','drill')}
    changed={t['id']for t in actual.get('tracks',[])if t['id']not in before or geometry(t)!=geometry(before[t['id']])}
    blocked=[r for r in quality['cornerFindings']if key(r)not in old_keys or changed.intersection(r['objectIds'])]
    return dict(policy=quality['cornerPolicy'],passed=not blocked,exposedCornerCount=quality['exposedCornerCount'],
        blockingCornerCount=len(blocked),unchangedExistingCornerCount=quality['exposedCornerCount']-len(blocked),findings=blocked,
        scope='New or reworked exposed degree-two linear-copper turns >=90 degrees are rejected; native pad interiors and real branches are separate. Existing untouched findings remain visible.')


def turn_cost(first, second, penalty):
    if first==8 or second==8:return 0.
    steps=min((first-second)%8,(second-first)%8)
    return penalty*(0,1,3,6,10)[steps]


def on_segment(p, a, b):
    u=(b[0]-a[0],b[1]-a[1]);v=(p[0]-a[0],p[1]-a[1])
    return abs(u[0]*v[1]-u[1]*v[0])<=1e-7*max(1.,math.hypot(*u)) and \
        min(a[0],b[0])-1e-7<=p[0]<=max(a[0],b[0])+1e-7 and min(a[1],b[1])-1e-7<=p[1]<=max(a[1],b[1])+1e-7


def trace_chains(footprints, tracks,extra_anchors=()):
    """Do not simplify across pads, vias, branches, width or layer changes.

    An interior contact fixes both ends of its owning segment. This deliberately
    avoids rewriting a partial object or dropping a branch in an optimizer.
    """
    pins=[tuple(p['at']) for f in footprints for p in f['pads']]+[tuple(p) for p in extra_anchors]
    vias=[tuple(t['at']) for t in tracks if t['type']=='via']
    groups=collections.defaultdict(list)
    for t in tracks:
        if t['type']=='segment':groups[t['net'],t['layer'],t['width']].append(t)
    result=[]
    for (net,layer,width),edges in sorted(groups.items()):
        adjacency=collections.defaultdict(list);by_id={t['id']:t for t in edges}
        for t in edges:
            for p in (t['start'],t['end']):adjacency[tuple(p)].append(t['id'])
        anchors=set(pins+vias)
        for other in tracks:
            if other['net']!=net or other['type']!='segment' or other['layer']!=layer:continue
            if other['width']!=width:anchors.update((tuple(other['start']),tuple(other['end'])))
        contacts=set(adjacency)|anchors
        # Only points inside a segment's bounding box can touch it. Keep the
        # exact predicate/tolerance below; buckets merely narrow candidates.
        buckets=collections.defaultdict(list)
        for p in contacts:buckets[math.floor(p[0]/2),math.floor(p[1]/2)].append(p)
        for t in edges:
            a,b=tuple(t['start']),tuple(t['end'])
            x0,x1=[math.floor(v/2) for v in (min(a[0],b[0])-1e-7,max(a[0],b[0])+1e-7)]
            y0,y1=[math.floor(v/2) for v in (min(a[1],b[1])-1e-7,max(a[1],b[1])+1e-7)]
            nearby=(p for x in range(x0,x1+1) for y in range(y0,y1+1) for p in buckets.get((x,y),()))
            if (x1-x0+1)*(y1-y0+1)>len(buckets):nearby=contacts
            if any(p!=a and p!=b and on_segment(p,a,b) for p in nearby):anchors.update((a,b))
        anchors.update(p for p,ids in adjacency.items() if len(ids)!=2)
        visited=set()
        def walk(start,first):
            points=[start];ids=[];at=start;key=first
            while key not in visited:
                visited.add(key);ids.append(key);t=by_id[key]
                at=tuple(t['end']) if tuple(t['start'])==at else tuple(t['start']);points.append(at)
                if at in anchors:break
                remaining=[i for i in adjacency[at] if i not in visited]
                if len(remaining)!=1:break
                key=remaining[0]
            return dict(net=net,layer=layer,width=width,objectIds=ids,points=[list(p)for p in points])
        for p in sorted(anchors&adjacency.keys()):
            for key in sorted(adjacency[p]):
                if key not in visited:result.append(walk(p,key))
        for key in sorted(by_id):
            if key not in visited:result.append(walk(tuple(by_id[key]['start']),key))
    return result


def style_audit(footprints, tracks, pad_contains=None, chains=None):
    if __package__:from .route_readability_policy import metrics
    else:from route_readability_policy import metrics
    rows=[];totals=collections.Counter()
    if chains is None:chains=trace_chains(footprints,tracks)
    for chain in chains:
        values=metrics(chain['points']);totals.update(values)
        if values['shortClusterCount'] or values['backtrackCount']:
            rows.append({**chain,**values})
    return dict(policy='tidy-routing-v2',**totals,**corner_audit(tracks,pad_contains),chainCount=len(chains),flaggedChainCount=len(rows),findings=rows,
        scope='Advisory chain geometry. Pad/via/junction/width/layer anchors are preserved; necessary escapes and length tuning require explicit review.')


def native_exit_options(pad, neck, layer=None):
    """Centred escapes from native copper; true circles also permit 45 degrees."""
    import pcbnew as pcb
    if __package__:from .pad_exits import clip,contains,convex,ERROR,NECK,native_circle
    else:from pad_exits import clip,contains,convex,ERROR,NECK,native_circle
    if not hasattr(pad,'TransformShapeToPolygon'):return []
    if layer is None:
        layer=next((l for l in (pcb.F_Cu,pcb.B_Cu) if pad.IsOnLayer(l)),None)
    if layer is None or not pad.IsOnLayer(layer):return []
    native=pcb.SHAPE_POLY_SET();pad.TransformShapeToPolygon(native,layer,0,pcb.FromMM(ERROR),pcb.ERROR_OUTSIDE)
    if native.OutlineCount()!=1 or native.HoleCount(0):return []
    outline=native.Outline(0)
    polygon=[[pcb.ToMM(outline.CPoint(i).x),pcb.ToMM(outline.CPoint(i).y)]for i in range(outline.PointCount())]
    at=[round(pcb.ToMM(v),6)for v in (pad.GetPosition().x,pad.GetPosition().y)]
    if not convex(polygon) or not contains(polygon,at):return []
    origin=pad.GetParent().GetPosition();radial=[at[0]-pcb.ToMM(origin.x),at[1]-pcb.ToMM(origin.y)]
    reach=max(math.dist(at,p) for p in polygon)+1.;neck=max(NECK,float(neck))
    result=[]
    axes=[[1,0],[-1,0],[0,1],[0,-1]]
    if native_circle(pad,layer):axes.extend([[x/math.sqrt(2),y/math.sqrt(2)]for x,y in ((1,1),(1,-1),(-1,1),(-1,-1))])
    for axis in axes:
        far=[at[i]+axis[i]*reach for i in (0,1)];interval=clip(polygon,at,far)
        if interval is None:continue
        # Round outward to a native nanometre, never shorten the specified neck.
        length=math.ceil((reach*interval[1]+neck)*1e6-1e-6)/1e6
        result.append(dict(at=at,axis=axis,length=length))
    return sorted(result,key=lambda r:(-sum(a*b for a,b in zip(r['axis'],radial)),r['length']))


def native_exit(pad, neck):
    """Preferred native option; the router considers all allowed directions."""
    return next(iter(native_exit_options(pad,neck)),None)


def power_feed_report(snapshot, rules):
    """Prove a declared capacitor waypoint on explicit copper centrelines.

    Filled copper and arcs on a selected net are unsupported and cannot pass.
    This is intentionally narrower than finite-width or power integrity proof.
    """
    fps={f['reference']:f for f in snapshot['footprints']};rows=[]
    def pin(ref):
        matches=[p for p in fps.get(ref['reference'],{}).get('pads',[]) if p['number']==ref['pin']]
        if len(matches)!=1:raise ValueError('Power feed anchor must identify one existing pad: '+str(ref))
        return matches[0],fps[ref['reference']]['layer']
    for rule in rules:
        source,source_layer=pin(rule['source']);cap,cap_layer=pin(rule['capacitor']);loads=[pin(x)for x in rule['loads']]
        net=source['net'];issues=[]
        if not net or any(p['net']!=net for p,_ in [(cap,cap_layer)]+loads):raise ValueError('Power feed anchors must share the same nonempty net')
        cap_fp=fps[rule['capacitor']['reference']]
        if not any(p['net']==rule['returnNet']for p in cap_fp['pads']):raise ValueError('Declared capacitor has no pad on its return net')
        tracks=[t for t in snapshot['tracks']if t['net']==net];zones=[z for z in snapshot.get('zones',[])if z['net']==net]
        if zones or any(t['type']=='arc'for t in tracks):issues.append('unsupported_zone_or_arc')
        segments=[t for t in tracks if t['type']=='segment'];layers=snapshot.get('copperLayers',['F.Cu','B.Cu'])
        points=collections.defaultdict(set)
        for t in segments:points[t['layer']].update((tuple(t['start']),tuple(t['end'])))
        for p,layer in [(source,source_layer),(cap,cap_layer)]+loads:points[layer].add(tuple(p['at']))
        for t in tracks:
            if t['type']=='via':
                for layer in layers:points[layer].add(tuple(t['at']))
        # Native plated pad barrels connect the declared copper layers too;
        # a connector reached on an inner layer is still an F-side load.
        barrels=[p for fp in fps.values()for p in fp['pads']if p['net']==net and p.get('platedThrough')is True
            and max(p.get('drill',[0,0]))>0 and len(p.get('copperLayers',[]))>1]
        for p in barrels:
            for layer in p['copperLayers']:
                if layer in layers:points[layer].add(tuple(p['at']))
        # Intersections of ordinary centreline segments are real graph nodes.
        for i,a in enumerate(segments):
            for b in segments[i+1:]:
                if a['layer']!=b['layer']:continue
                p,q=a['start'],b['start'];u=[a['end'][j]-p[j]for j in (0,1)];v=[b['end'][j]-q[j]for j in (0,1)];cross=u[0]*v[1]-u[1]*v[0]
                if abs(cross)<1e-12:continue
                t=((q[0]-p[0])*v[1]-(q[1]-p[1])*v[0])/cross;r=tuple(round(p[j]+t*u[j],6)for j in (0,1))
                if on_segment(r,a['start'],a['end'])and on_segment(r,b['start'],b['end']):points[a['layer']].add(r)
        graph=collections.defaultdict(set)
        for t in segments:
            ordered=sorted((p for p in points[t['layer']]if on_segment(p,t['start'],t['end'])),key=lambda p:math.dist(p,t['start']))
            for a,b in zip(ordered,ordered[1:]):
                u=(t['layer'],a);v=(t['layer'],b);graph[u].add(v);graph[v].add(u)
        for t in tracks:
            if t['type']=='via':
                nodes=[(l,tuple(t['at']))for l in layers]
                for u in nodes:
                    for v in nodes:
                        if u!=v:graph[u].add(v)
        for p in barrels:
            nodes=[(l,tuple(p['at']))for l in p['copperLayers']if l in layers]
            for u in nodes:
                for v in nodes:
                    if u!=v:graph[u].add(v)
        start=(source_layer,tuple(source['at']));stop=(cap_layer,tuple(cap['at']))
        def reach(block=None):
            seen=set();todo=[start]
            while todo:
                u=todo.pop()
                if u==block or u in seen:continue
                seen.add(u);todo.extend(graph[u]-seen)
            return seen
        all_nodes=reach();without=reach(stop)
        if stop not in all_nodes:issues.append('capacitor_not_connected_in_centreline_model')
        for request,(p,layer) in zip(rule['loads'],loads):
            key=(layer,tuple(p['at']))
            if key not in all_nodes:issues.append('load_not_connected:'+request['reference']+'.'+request['pin'])
            elif key in without:issues.append('load_bypasses_capacitor:'+request['reference']+'.'+request['pin'])
        rows.append(dict(name=rule['name'],net=net,passed=not issues,issues=issues,rule=rule))
    return dict(status=('passed'if all(r['passed']for r in rows)else'failed')if rows else'not_declared',passed=all(r['passed']for r in rows)if rows else None,rules=rows,
        scope='Declared capacitor waypoint on explicit track centrelines only; not electrical series capacitance, finite-width/plane, ground-loop or PI validation.')
