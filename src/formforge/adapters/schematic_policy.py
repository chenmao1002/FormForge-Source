"""Shared schematic presentation defaults and read-only physical-wire feedback."""
from collections import defaultdict

POLICY = 'functional-local-wires-v1'

AGENT_INSTRUCTIONS = (
    '原理图默认按功能组组织，信号流程优先从左向右，供电/返回优先上下表达；重复通道保持一致布局。'
    '组内串联、分压、去耦和驱动支路优先真实短导线，跨组/跨页保留标签；不要把全部器件孤立贴标签当完成排版。'
    '先按实际引脚方向摆放、旋转与对齐，留出正交连线及位号/数值空间，再建线；不能仅为画线比例硬拉长线或跨功能框。'
    'rotate_symbol旋转普通单单元器件，连线和标签须在同批明确重建并声明连接目标；move_group显式选组移动，外部端点固定，不能保持正交时先明确重布线路径。'
    '新建时用add_wire/add_junction直接表达已明确关系；已有同页同类同名标签可选择labelIds，'
    '用connect_local_labels单独成批补线（默认25.4mm），读取connected/skipped/unavailable。'
    '读schematic_read.readability的labelOnlySymbols、labelOnlyPins、pinConnections和unavailable；公共返回连线不代表所有信号支路已有实体线。它只反映图面局部实体连线，'
    '不是电气未连接或功能分组判断。对提示项决定补线、重新摆放或保留跨组标签并说明理由，不能盲目全页补线。'
    '每轮完成后复读整页readability并查看原生SVG/PDF；ERC通过不代替可读性复核。wireQuality分别给出实体线长度、转弯、非连接交叉、重叠与双引脚实体路径绕行估计；functionalGroups按已登记符号UUID给出分块指标、全页字段估计和声明支路实体连续性，跨块/未归属导线及跨块交叉单列；未登记/成员缺失/预算不足不推断或假报完成；读取coverage/预算，不把同名标签当捷径或绕行余量当可删线。'
    'auto_layout的易读策略可带version=1的intent.branches，声明id/kind/source/target/evidence/allowedRotations；'
    'source与target使用真实reference/pin，须同组同网。普通支路移动双引脚target，driver/protection允许明确唯一角度的普通多引脚目标，source锚点先放置；无可行直接线则拒绝，不静默退回标签。'
    'P2可用cleanup整理同一物理连接内的重复标签、悬空标签短支线、字段和目录备注；不按同网名删除跨组标签。'
    'protection和多引脚driver须明确唯一角度；moveTarget=false保持已放器件，tapSide明确既有线上的分支方向。'
    'allowCrossings仅允许无连接点的内部正交交叉，不接触其他引脚/端点；最终仍核对原生完整连接。'
    '位号/数值压线时用tidy_fields在明确symbolIds上单独整理；保留字符、角度及全部电气锚点。'
    'P3可用organization声明功能块id/name/page及有序references，repetitions声明同角色顺序的组和列数；不根据网名猜测重复关系。'
    '命名功能页支持1至9页，旧双页模式保留直接连线总览；其他页数逐页生成真实父页引脚短线和原网名标签。保留独立器件身份与参数，不等于共享子图纸文件复用。'
    'organization.pagePlanning可按实际功能块包络规划页面：maxPages/pagePrefix/samePage/netWeights/maxCandidates；省略group.page才是可分配，明确page和samePage是硬约束。权重需实际net和evidence，不按网名猜角色，不改变电气关系。先比较可行页数，再比较跨页代价；有界候选/预算不代表全局最优或无解。重复columns在规划模式为每行上限，组内关键回路不拆分。'
    '重建分页明确拒绝未支持的无UUID根元数据（如bus_alias），可保留原图或使用完整块整理；不静默删除。重建按原生解析结果在最终标签保存隐藏Netclass字段，不改项目规则；组合网类成员有歧义则拒绝。新排版候选额外核对原生网名/网类、器件属性/NC/内嵌库及项目/PCB文件；分页实例路径迁移单列，已有PCB须下游重审，不自动更新铜线。'
    '跨页连接同时生成子页层次标签、父页引脚与实体线，H_别名只用于页内接口，原始显式网名由顶层保持；仍须原生全针网、ERC及视觉验收。'
    'P4保留层次整理用blockLayout.version=1/sheets，每页声明expectedSheetHash、groups(id/name/objectIds)、columns；共享文件须affectSharedInstances=true并核对全部instancePaths。仅整体平移完整物理块，保留原纸张/页数/实例与块内几何，不与重建分页或支路意图混用。'
    '排版优化先用adoption=review生成同版候选，并带includeBaselineDiagnostics=true导出原生前后图；读取schematic_candidate_read检查变化/未满足项，查看原生图后再用schematic_candidate_adopt绑定jobId/baseRevisionId/expectedProjectVersion采纳。默认automatic保留旧客户端行为。共享实例绘图用drawing_scene_read的instance_path明确选择，不能把同一文件的不同实例缓存混用。'
    '多单元显示按冻结项目的A/a或数字编号、可打印ASCII分隔符与内嵌unit_name解析；共享实例的完整显示位号共同参与整块文字包络，基础位号/引脚身份不变。未知编号或冲突单元名须使用原生预览，不默认为A/B。'
    '文字包络和有界候选是辅助，不是精确字形、全局最优或无解证明；连接保持/明确目标与原生ERC仍强制。')


def defaults():
    return dict(policy=POLICY, source='FormForge software default; user requirements remain separate',
                localConnections='physical-wires-preferred', crossGroupConnections='labels-preferred',
                layout='agent-explicit-functional-groups', repeatedChannels='consistent-layout',
                review='readability-feedback-and-native-preview', automaticWholeSheetRewire=False,
                electricalAcceptance='native-erc-and-connectivity-target')


def feedback(objects):
    """Count physical peers, deliberately never unioning equal label names.

    Endpoints, pins and explicit junctions connect segments; a bare X crossing
    does not. This is presentation feedback, not a replacement native netlist.
    """
    symbols = [o for o in objects if o['type']=='symbol' and not o.get('reference','').startswith('#')]
    wires = [(tuple(a),tuple(b)) for o in objects if o['type']=='wire' for a,b in zip(o['points'],o['points'][1:])]
    labels = [o for o in objects if o['type'] in {'label','global_label','hierarchical_label'} and o.get('at')]
    unavailable = [dict(objectId=o['id'],reference=o.get('reference'),reason=o.get('templateLimitation','pin_positions_unavailable'))
                   for o in symbols if 'pinPositions' not in o]
    known = [o for o in symbols if 'pinPositions' in o]
    parent = {}
    def root(p):
        parent.setdefault(p,p)
        while parent[p]!=p:
            parent[p]=parent[parent[p]];p=parent[p]
        return p
    def union(a,b):parent[root(a)]=root(b)
    pins = [(o['id'],tuple(pin['at'])) for o in known for pin in o['pinPositions']]
    anchors = {p for _,p in pins} | {tuple(o['at'][:2]) for o in labels}
    anchors.update(tuple(o['at'][:2]) for o in objects if o['type']=='junction' and o.get('at'))
    for a,b in wires: anchors.update((a,b))
    result = dict(policy=POLICY,scope='whole-selected-sheet',advisory=True,electricalVerdict=False,
                  symbolCount=len(symbols),wireSegmentCount=len(wires),labelCount=len(labels),
                  labelOnlySymbols=[],symbolsWithLocalWirePeers=0,unavailable=unavailable,
                  pinConnections=[],labelOnlyPins=[])
    if len(anchors)*len(wires)>2_000_000:
        result.update(status='unavailable',reason='presentation_analysis_budget');return result
    for a,b in wires:
        union(a,b)
        for p in anchors:
            if (min(a[0],b[0])<=p[0]<=max(a[0],b[0]) and min(a[1],b[1])<=p[1]<=max(a[1],b[1])
                    and abs((p[0]-a[0])*(b[1]-a[1])-(p[1]-a[1])*(b[0]-a[0]))<1e-7):
                union(a,p)
    owners=defaultdict(set);group_labels=defaultdict(set)
    for identity,p in pins:owners[root(p)].add(identity)
    for o in labels:group_labels[root(tuple(o['at'][:2]))].add(o['id'])
    by_id = {o['id']: o for o in objects}
    for o in known:
        for pin in o['pinPositions']:
            g = root(tuple(pin['at']))
            peers = sorted(owners[g]-{o['id']})
            ids = sorted(group_labels[g])
            row = dict(objectId=o['id'], reference=o.get('reference'), pin=pin['number'], at=pin['at'],
                       peerObjects=[dict(objectId=i,reference=by_id[i].get('reference')) for i in peers],
                       labels=[dict(objectId=i,text=by_id[i].get('text',''),type=by_id[i]['type']) for i in ids],
                       status='local-peer' if peers else 'label-only' if ids else 'no-local-peer')
            result['pinConnections'].append(row)
            if row['status']=='label-only':result['labelOnlyPins'].append(row)
        groups={root(tuple(pin['at'])) for pin in o['pinPositions']}
        if any(len(owners[g])>1 for g in groups):result['symbolsWithLocalWirePeers']+=1
        else:
            ids=sorted({label for g in groups for label in group_labels[g]})
            if ids:result['labelOnlySymbols'].append(dict(objectId=o['id'],reference=o.get('reference'),labelIds=ids))
    result['labelOnlySymbols'].sort(key=lambda row:(row['reference'] or '',row['objectId']))
    result['pinConnections'].sort(key=lambda row:(row['reference'] or '',row['pin']))
    result['labelOnlyPins'].sort(key=lambda row:(row['reference'] or '',row['pin']))
    from .schematic_wire_quality import wire_quality
    result['wireQuality']=wire_quality(objects)
    result['pinReviewSuggested']=bool(result['labelOnlyPins'])
    result['status']='review_suggested' if result['labelOnlySymbols'] else 'no_label_only_symbols_detected'
    result['note']='标签独立器件可能是合理跨组接口；先判断功能组，不自动补线。未检测到提示也不代表整页美观或电气通过。'
    return result
