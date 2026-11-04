"""Narrow G030F6P6 TSSOP20 profile derived from DS12991/RM0454."""
import math

PAD_NAMES={'1':'PB7/PB8','2':'PB9/PC14','3':'PC15','4':'VDD','5':'VSS','6':'NRST',
           '7':'PA0','8':'PA1','9':'PA2','10':'PA3','11':'PA4','12':'PA5','13':'PA6','14':'PA7',
           '15':'PA8/PB0/PB1/PB2','16':'PA9/PA11','17':'PA10/PA12','18':'PA13','19':'PA14/PA15','20':'PB3/PB4/PB5/PB6'}
ROLE_PADS={'led':'7','tx':'9','rx':'10','scl':'16','sda':'17'}


def role_metadata(role):
    if role in ('tx','rx'):return {'peripheral':'USART2','alternate_function':1,'remap':'none; PA2/PA3 only'}
    if role in ('sda','scl'):return {'peripheral':'I2C1','alternate_function':6,'remap':'PA11_RMP=1 and PA12_RMP=1; PA9/PA10, not default PA11/PA12'}
    return {'peripheral':'GPIOA','alternate_function':None,'remap':'none'}


def validate_g030_circuit(board,target,req):
    errors=[];component=board['components'][target]
    if component.get('footprint')!='Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm' or component.get('lib_id',{}).get('part')!='STM32G030F6Px':
        errors.append('G030 requires the exact F6P6 TSSOP20 symbol and footprint')
    if {p:m.get('name') for p,m in component.get('pins',{}).items()}!=PAD_NAMES:
        errors.append('G030 requires all 20 verified package-pad names including bonded/remapped aliases')
    if any(m.get('type')!=({'4':'power_in','5':'power_in','6':'input'}.get(p,'bidirectional')) for p,m in component.get('pins',{}).items()):
        errors.append('G030 pin electrical types disagree with the verified symbol')
    if req.get('clock_hz',16000000)!=16000000:errors.append('G030 profile supports only nominal HSI16 16 MHz, no HSE/PLL or arbitrary bootloader clock')
    if req.get('electrical',{}).get('io_voltage')!=3.3:errors.append('G030 review fixture requires a 3.3 V domain')
    if req.get('electrical',{}).get('reset_boot_reviewed') is not True:
        errors.append('reset_boot_reviewed=true required for reset entry, factory HSI trim, Flash-boot option bytes and SWCLK/BOOT0 interaction; no option-byte operation is generated')
    def endpoints(ref,pin):return [(n,x) for n in board['nets'] for x in n['nodes'] if x['ref']==ref and x['pin']==pin]
    def net(pin):
        matches=endpoints(target,pin)
        return matches[0][0] if len(matches)==1 and 'no_connect' not in matches[0][1]['type'] else None
    supply,ground=net('4'),net('5')
    if supply is None or ground is None or supply is ground:
        errors.append('G030 requires connected separate VDD/VDDA and VSS/VSSA networks');return errors
    def passive_to(n,part,value,other_net):
        if n is None:return False
        for node in n['nodes']:
            c=board['components'][node['ref']]
            if c.get('lib_id',{}).get('part')!=part or c['value']!=value or node['pin'] not in ('1','2'):continue
            other=endpoints(node['ref'],'2' if node['pin']=='1' else '1')
            if len(other)==1 and other[0][0] is other_net:return True
        return False
    if not passive_to(supply,'C','100n',ground) or not passive_to(supply,'C','4.7u',ground):
        errors.append('G030 requires reviewed 100n and 4.7u supply bypass capacitors')
    reset,boot=net('6'),net('19')
    if reset is supply or reset is ground or not passive_to(reset,'R','10k',supply) or not passive_to(reset,'C','100n',ground):
        errors.append('G030 requires NRST 10k pullup and 100n capacitor')
    if boot is supply or boot is ground or not passive_to(boot,'R','10k',ground):
        errors.append('G030 requires the reviewed PA14/PA15 SWCLK/BOOT0 pad19 10k pulldown')
    for n in (reset,boot):
        if n and any(x['ref']!=target and x['type'] in ('output','open_collector','open_emitter','power_out') for x in n['nodes']):
            errors.append('G030 reset/boot profile rejects competing output drivers')
    for pin in ('1','2','3','15','20'):
        matches=endpoints(target,pin)
        if len(matches)!=1 or 'no_connect' not in matches[0][1]['type']:
            errors.append('G030 profile reserves oscillator/bonded pad '+pin+' as explicitly unconnected')
    if req['task']=='i2c_probe_uart':
        elec=req.get('electrical',{});cap=elec.get('i2c_bus_capacitance_max_pf')
        if elec.get('i2c_probe_reviewed') is not True:
            errors.append('i2c_probe_reviewed=true required for a single-master bus and reviewed address-only probe effects on every attached device')
        if type(cap) not in (int,float) or not math.isfinite(cap) or not 0<cap<=200:
            errors.append('Reviewed G030 I2C bus capacitance budget must be >0 and <=200 pF; waveforms remain unmeasured')
    return errors
