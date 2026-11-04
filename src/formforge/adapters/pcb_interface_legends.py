"""Compare declared interface legends with saved native pads and silk text."""
import math


AGENT_INSTRUCTIONS = (
    '交付PCB前逐接口核对下载/调试、电源和外部连接器的功能、逐针信号及针1方向。'
    '不能只写SWD/UART等接口名称；按真实焊盘编号和网络登记pcb_edit.interfaceLegends，'
    '每针使用明确的编号:信号文字，必要时注明电压、极性及外接供电限制。'
    '功能名、完整逐针表和针1标记优先放器件所在面的丝印，按该观察面登记layer；顶层器件不因沿用旧背面表就跳过可用的顶层空位。先比较可读空区和分栏排列，避开实际器件本体、焊盘开窗、固定件及工具包络；本体下方不是可读空位。另一面布置可作为有来源理由的例外，保存偏好/例外并更新interfaceLegends观察面，不能静默移动后仍沿用旧面契约。'
    '电压额定值、实际输出范围与绝对上限须分别按电气评审及来源核对；例如5.2V升压输入不能笼统标5V MAX。文字匹配不证明电压语义正确，须保留Agent复核。'
    'pcb_read.layoutFeedback.interfaceLegends返回原生文字/针网匹配和未登记连接器提示；'
    '删除文字、移远、移到错误面或改变针网会使已登记规则失败。'
    '使用实际板面观察针序，连接器配对面不能直接照抄；原生预览和DRC仍需独立核对遮挡、开窗和可读性。'
)


def report(snapshot, rules):
    footprints = {f['reference']: f for f in snapshot['footprints'] if not f.get('mechanicalFeature')}
    texts = snapshot.get('boardTexts', [])
    findings, interfaces = [], []
    declared = {r['reference'] for r in rules}
    # This is an inspection hint based on the frozen library category, not an
    # inferred electrical role or an automatic requirement for arbitrary pads.
    unreviewed = sorted(ref for ref, f in footprints.items()
                        if ref not in declared and f.get('footprint', '').split(':')[0].startswith(('Connector', 'TerminalBlock')))
    for rule in rules:
        ref = rule['reference']; fp = footprints.get(ref)
        errors = []
        def issue(kind, **detail):
            item = dict(type=kind, reference=ref, **detail)
            findings.append(item); errors.append(item)
        if fp is None:
            issue('missing_interface')
            interfaces.append(dict(reference=ref, passed=False, pins=[])); continue
        pads = {}
        for pad in fp['pads']:
            if pad['number']: pads.setdefault(pad['number'], []).append(pad)
        declared_pins = {p['pin'] for p in rule['pins']}
        if declared_pins != set(pads):
            issue('pin_coverage', missing=sorted(set(pads)-declared_pins), unknown=sorted(declared_pins-set(pads)))
        rows = []
        def candidates(label):
            return [t for t in texts if t['text'] == label and t['layer'] == rule['layer']
                    and min(t['size']) + 1e-6 >= rule['minTextSizeMm']
                    and t['thickness'] + 1e-6 >= rule['minStrokeMm']]
        titles = [t for t in candidates(rule['title']) if math.dist(t['at'], fp['at']) <= rule['maxTitleDistanceMm']]
        if not titles: issue('missing_interface_title', text=rule['title'])
        for pin in rule['pins']:
            actual = pads.get(pin['pin'], [])
            nets = sorted({p['net'] for p in actual})
            net_ok = bool(actual) and nets == [pin['net']]
            if not net_ok: issue('pin_net_mismatch', pin=pin['pin'], expectedNet=pin['net'], actualNets=nets)
            matches = [t for t in candidates(pin['label']) if actual and
                       min(math.dist(t['at'], p['at']) for p in actual) <= rule['maxPinDistanceMm']]
            if not matches: issue('missing_pin_legend', pin=pin['pin'], text=pin['label'])
            rows.append(dict(pin=pin['pin'], net=pin['net'], label=pin['label'], passed=net_ok and bool(matches),
                             padIds=[p['id'] for p in actual], textIds=[t['id'] for t in matches]))
        interfaces.append(dict(reference=ref, purpose=rule['purpose'], passed=not errors,
                               titleTextIds=[t['id'] for t in titles], pins=rows))
    passed = not findings
    return dict(schemaVersion='formforge.interface-legends/1', declaredPassed=passed,
                status='failed' if not passed else 'needs-review' if unreviewed else 'passed' if rules else 'unplanned',
                interfaces=interfaces, findings=findings, unreviewedConnectors=unreviewed,
                scope='Saved native pad numbers/nets and numbered silk labels, layer, size, stroke and proximity; '
                      'library-category hints are not electrical role inference. Occlusion, mating-face orientation and manufacturing remain separate.')
