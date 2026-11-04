"""Exact, reviewed STM32 targets shared by builds, hardware binding and flashing."""
import copy
import json
from pathlib import Path
import re
from typing import Literal

ROOT = Path(__file__).resolve().parents[3]
STM32Target = Literal['STM32G030F6P6','STM32G030K8T6','STM32G071CBT6','STM32F103C8T6',
                      'STM32F103RCT6','STM32F401CCU6','STM32F411CEU6','STM32F407VGT6']
PACKS = {
    'g0': dict(file='Keil.STM32G0xx_DFP.2.1.0.pack', sha256='3076c0f9a6a9a99f7a2325a2e519bed0ac3a3c1db3553748ceeb5b0676645889'),
    'f1': dict(file='Keil.STM32F1xx_DFP.2.4.1.pack', sha256='807ea15da5b172b916bbc47b2b87f1e621240ad208d38e82a417a2ef8191e9d1'),
    'f4': dict(file='Keil.STM32F4xx_DFP.3.1.1.pack', sha256='345231106fe697df24bbe9133aeeec1b0383d7891c4adf4f47bbb8b478e9f2f0'),
}


def _target(name, symbol, family, header, cpu, flash, ram, clock, device_id, erase):
    return dict(name=name, symbol=symbol, family=family, header=header, cpu=cpu,
                flashSize=flash*1024, ramSize=ram*1024, clockHz=clock*1000000,
                clock=f'nominal-hsi{clock}-{clock}mhz-no-bootloader-handover',
                templates=['blink','uart_echo'], targetName=(symbol[:-2] if family=='f1' else symbol).lower(), deviceId=device_id,
                eraseSectors=erase, source=dict(uri='https://www.st.com/resource/en/datasheet/'+name[:11].lower()+'.pdf',
                version='ST CMSIS pinned headers and exact CMSIS-Pack; see M6-stm32-cmsis.json',
                basis='Software target definition; physical hardware not verified'))


TARGETS = {
    p['name']:p for p in [
        _target('STM32G030F6P6','STM32G030F6Px','g0','stm32g030xx.h','cortex-m0plus',32,8,16,0x466,[(0,2048)]),
        _target('STM32G030K8T6','STM32G030K8Tx','g0','stm32g030xx.h','cortex-m0plus',64,8,16,0x466,[(0,2048)]),
        _target('STM32G071CBT6','STM32G071CBTx','g0','stm32g071xx.h','cortex-m0plus',128,36,16,0x460,[(0,2048)]),
        _target('STM32F103C8T6','STM32F103C8Tx','f1','stm32f103xb.h','cortex-m3',64,20,8,0x410,[(0,1024)]),
        _target('STM32F103RCT6','STM32F103RCTx','f1','stm32f103xe.h','cortex-m3',256,48,8,0x414,[(0,2048)]),
        _target('STM32F401CCU6','STM32F401CCUx','f4','stm32f401xc.h','cortex-m4',256,64,16,0x423,[(0,16384),(65536,65536),(131072,131072)]),
        _target('STM32F411CEU6','STM32F411CEUx','f4','stm32f411xe.h','cortex-m4',512,128,16,0x431,[(0,16384),(65536,65536),(131072,131072)]),
        _target('STM32F407VGT6','STM32F407VGTx','f4','stm32f407xx.h','cortex-m4',1024,128,16,0x413,[(0,16384),(65536,65536),(131072,131072)]),
    ]
}
TARGETS['STM32G030F6P6']['templates'].append('i2c_probe_uart')
TARGETS['STM32F407VGT6']['additionalMemory'] = dict(ccmBytes=65536, allocatedByDefault=False)


def get_target(name='STM32G030F6P6'):
    p = copy.deepcopy(TARGETS[name])
    if name == 'STM32G030F6P6':
        p.update(vectorWords=46, reservedVectors=[4,5,6,7,8,9,10,12,13,17,24,31,33,34,36,45],
                 footprint='Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm')
        return p
    pins = json.loads((ROOT/'vendor/firmware/stm32/pins.json').read_text('utf8'))[p['symbol']]
    p.update(copy.deepcopy(pins))
    header = (ROOT/'vendor/firmware/stm32'/p['family']/p['header']).read_text('utf8')
    irqs = {int(n) for n in re.findall(r'\b\w+_IRQn\s*=\s*(-?\d+)', header)}
    size = max(irqs)+17
    present = {0,1,*(n+16 for n in irqs)}
    p.update(vectorWords=size, reservedVectors=sorted(set(range(size))-present),
             hardwareVerified=False)
    return p


def flash_target(name):
    p=get_target(name)
    pack=PACKS[p['family']]
    return dict(backend='pyocd', base=0x08000000, size=p['flashSize'], eraseSize=p['eraseSectors'][0][1],
                pack=dict(path='.local/cmsis-packs/'+pack['file'],sha256=pack['sha256'],source='https://www.keil.com/pack/'+pack['file']),
                eraseSectors=p['eraseSectors'], targetName=p['targetName'], architecture=p['cpu'], family=p['family'],
                deviceId=p['deviceId'], deviceIdAddress=0x40015800 if p['family']=='g0' else 0xE0042000,
                flashSizeAddress={'g0':0x1fff75e0,'f1':0x1ffff7e0,'f4':0x1fff7a22}[p['family']],
                uidAddress={'g0':0x1fff7590,'f1':0x1ffff7e8,'f4':0x1fff7a10}[p['family']])


def catalog():
    return {name:get_target(name) for name in TARGETS}
