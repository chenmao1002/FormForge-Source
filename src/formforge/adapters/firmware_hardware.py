"""Native electrical identity and explicit firmware roles, never net-name inference."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from .importer import digest
from .connectivity import fingerprint,read_graph
from ..store import DomainError
from .schematic_evidence import read_evidence
from .firmware_targets import TARGETS, get_target

PAD_NAMES={'1':'PB7/PB8','2':'PB9/PC14','3':'PC15','4':'VDD','5':'VSS','6':'NRST','7':'PA0','8':'PA1','9':'PA2','10':'PA3',
           '11':'PA4','12':'PA5','13':'PA6','14':'PA7','15':'PA8/PB0/PB1/PB2','16':'PA9/PA11','17':'PA10/PA12','18':'PA13','19':'PA14/PA15','20':'PB3/PB4/PB5/PB6'}
ROLES={'led':dict(pad='7',function='GPIOA.0',mode='output-active-high'),
       'tx':dict(pad='9',function='USART2_TX',alternateFunction=1),
       'rx':dict(pad='10',function='USART2_RX',alternateFunction=1),
       'scl':dict(pad='16',function='I2C1_SCL',alternateFunction=6,remap='PA11_RMP=1,PA12_RMP=1'),
       'sda':dict(pad='17',function='I2C1_SDA',alternateFunction=6,remap='PA11_RMP=1,PA12_RMP=1')}


def fail(message):raise DomainError('invalid_input',message)


def parse_hardware(path):
    raw=path.read_bytes()
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:fail('硬件XML不接受外部实体。')
    root=ET.fromstring(raw);parts={}
    for p in root.findall('./libparts/libpart'):
        parts[(p.get('lib'),p.get('part'))]={n.get('num'):dict(n.attrib) for n in p.findall('./pins/pin')}
    components={}
    for c in root.findall('./components/comp'):
        ref=c.get('ref');lib=c.find('libsource')
        if ref in components or lib is None:fail('硬件网表缺少唯一器件身份。')
        components[ref]=dict(reference=ref,value=c.findtext('value',''),footprint=c.findtext('footprint',''),
                             library=dict(lib.attrib),pins=parts.get((lib.get('lib'),lib.get('part')),{}))
    graph=read_graph(path);nets=[dict(name=g['name'],pins=g['pins']) for g in graph['groups']]
    semantic=dict(components=components,nets=sorted(nets,key=lambda n:fingerprint(n['pins'])))
    return dict(**semantic,hardwareHash=fingerprint(semantic),graphHash=graph['hash'],netlistSha256=digest(path))


def target_contract(hardware,target):
    comp=hardware['components'].get(target)
    name=comp['value'].upper() if comp else ''
    if name in TARGETS and name!='STM32G030F6P6':
        profile=get_target(name)
        if comp['library'].get('part')!=profile['symbol'] or comp['footprint']!=profile['footprint']:fail('MCU符号/封装与所选精确型号不一致。')
        names={p:m.get('name','').replace('~{','').replace('}','') for p,m in comp['pins'].items()}
        if names!=profile['pins']:fail('MCU完整物理引脚与所选精确封装不一致。')
        member={(p['reference'],p['pin']):i for i,n in enumerate(hardware['nets']) for p in n['pins']}
        supplies=[pad for pad,pin in names.items() if pin in {'VDD','VDDA','VBAT','VDD/VDDA'}]
        grounds=[pad for pad,pin in names.items() if pin in {'VSS','VSSA','VSS/VSSA'}]
        supply_nets={member.get((target,pad)) for pad in supplies};ground_nets={member.get((target,pad)) for pad in grounds}
        if None in supply_nets|ground_nets or supply_nets&ground_nets or any(len(hardware['nets'][i]['pins'])<2 for i in supply_nets|ground_nets):fail('MCU全部供电脚/地脚须实际连接，电源与地不可短接。')
        for pad,pin in names.items():
            if 'NRST' in pin and (member.get((target,pad)) is None or member[(target,pad)] in supply_nets|ground_nets):fail('NRST不得缺失或直接短接电源/地。')
        selected_roles={}
        for role,pin in [('led','PA0'),('tx','PA2'),('rx','PA3')]:
            pad=next(pad for pad,label in names.items() if label==pin)
            selected_roles[role]=dict(pad=pad,function=('GPIOA.0' if role=='led' else 'USART2_'+role.upper()),
                mode='output-active-high' if role=='led' else 'alternate-function',alternateFunction=1 if profile['family']=='g0' else 7 if profile['family']=='f4' else None)
        return dict(profile=name,targetReference=target,component=comp,roleOptions=selected_roles,
                    powerNetIndices=sorted(supply_nets|ground_nets),
                    supply=hardware['nets'][member[(target,next(pad for pad in supplies if names[pad]=='VDD'))]],
                    ground=hardware['nets'][member[(target,grounds[0])]],
                    resetPolicy='normal reset; option bytes not read',
                    scope='完整原生引脚/网络身份；模拟供电、VCAP、去耦、BOOT、波形及实物另验。')
    if not comp or comp['value'].upper()!='STM32G030F6P6' or comp['library'].get('part')!='STM32G030F6Px' or comp['footprint']!='Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm':
        fail('当前固件配置仅支持已登记的精确STM32型号及对应符号/封装。')
    names={p:m.get('name','').replace('~{','').replace('}','') for p,m in comp['pins'].items()}
    if names!=PAD_NAMES:fail('MCU完整20脚名称/复用别名与已核对封装不一致。')
    member={(p['reference'],p['pin']):i for i,n in enumerate(hardware['nets']) for p in n['pins']}
    supply=member.get((target,'4'));ground=member.get((target,'5'))
    if supply is None or ground is None or supply==ground or any(len(hardware['nets'][i]['pins'])<2 for i in [supply,ground]):
        fail('MCU电源与地必须实际连接且彼此分离。')
    reset=member.get((target,'6'))
    if reset is None or reset in (supply,ground):fail('NRST不得缺失或直接短接电源/地。')
    return dict(profile='STM32G030F6P6',targetReference=target,component=comp,roleOptions=ROLES,
                supply=hardware['nets'][supply],ground=hardware['nets'][ground],
                scope='原生器件/完整物理脚/网络身份；供电电压、选项字节、行为和实物另列。')


def validate_roles(hardware,target,pins,template=None):
    contract=target_contract(hardware,target);member={(p['reference'],p['pin']):i for i,n in enumerate(hardware['nets']) for p in n['pins']};used=[];rows=[]
    required={'blink':{'led'},'uart_echo':{'tx','rx'},'i2c_probe_uart':{'tx','rx','scl','sda'}}.get(template)
    if required is not None and set(pins)!=required:fail('模板必须完整且只声明其实际使用的引脚角色。')
    for role,pad in pins.items():
        if role not in contract['roleOptions'] or pad!=contract['roleOptions'][role]['pad']:fail('引脚角色与精确物理焊盘/复用功能不符。')
        net=member.get((target,pad))
        if net is None or len(hardware['nets'][net]['pins'])<2 or net in used:fail('固件接口必须接到实际网络且各信号角色独立，不能短接或仅有悬空脚。')
        used.append(net);rows.append(dict(role=role,**contract['roleOptions'][role],net=hardware['nets'][net]))
    supply=hardware['nets'].index(contract['supply']);ground=hardware['nets'].index(contract['ground'])
    if any(i in contract.get('powerNetIndices',(supply,ground)) for i in used):fail('信号角色不能直接短接MCU供电或地。')
    if template=='i2c_probe_uart':
        for role in ['scl','sda']:
            net=hardware['nets'][member[(target,pins[role])]];valid=False
            for p in net['pins']:
                c=hardware['components'][p['reference']]
                if c['library'].get('part')=='R' and c['value']=='4.7k' and p['pin'] in ('1','2'):
                    other=member.get((p['reference'],'2' if p['pin']=='1' else '1'));valid|=other==supply
            if not valid:fail('I2C初始模板需要两条线各有已声明的4.7k到同一电源上拉；时序仍需实测。')
    if template=='blink':
        led_net=member[(target,pins['led'])];valid=False
        for p in hardware['nets'][led_net]['pins']:
            resistor=hardware['components'][p['reference']]
            if resistor['library'].get('part')!='R' or p['pin'] not in ('1','2'):continue
            other=member.get((p['reference'],'2' if p['pin']=='1' else '1'))
            if other is None:continue
            for d in hardware['nets'][other]['pins']:
                led=hardware['components'][d['reference']]
                if led['library'].get('part')=='LED' and d['pin']=='2' and member.get((d['reference'],'1'))==ground:valid=True
        if not valid:fail('闪灯模板需要实际GPIO→串联电阻→LED阳极、阴极接地；不能仅凭网名认定。')
    return dict(**contract,bindings=rows,hardwareVerified=False,behaviorVerified=False)


def read_bound_hardware(config,store,project_id,revision_id,target,evidence=None,check_job_id=None):
    revision=store.get('revisions',revision_id)
    if revision['projectId']!=project_id or revision.get('module','eda')!='eda' or not revision['entrypoints'].get('schematic'):
        fail('固件硬件输入必须是同工程原理图版本。')
    if revision.get('validation',{}).get('status') in {'pending','failed','needs_review','cancelled','interrupted'}:
        fail('尚未采纳或未通过的原理图候选不能作为固件硬件依据。')
    binding=read_evidence(config,store,project_id,revision_id,evidence,check_job_id)
    hardware=parse_hardware(binding['netlistPath']);contract=target_contract(hardware,target)
    return dict(**binding,hardware=hardware,contract=contract)
