"""Independent ELF32/Intel HEX memory and vector checks; never execute an image."""
import hashlib
import struct
from pathlib import Path
from ..store import DomainError
from .firmware_targets import get_target


def fail(text):raise DomainError('tool_failed','ELF回读未通过：'+text)


def read_elf(path):
    raw=path.read_bytes()
    if len(raw)<52 or raw[:7]!=b'\x7fELF\x01\x01\x01':fail('必须为ELF32、小端格式。')
    h=struct.unpack_from('<16sHHIIIIIHHHHHH',raw)
    if h[1]!=2 or h[2]!=40 or h[3]!=1 or h[8]!=52 or h[9]!=32 or not 1<=h[10]<=100 or h[11]!=40 or not 1<=h[12]<=1000:fail('不是受支持的ARM可执行映像。')
    def area(offset,size):
        if offset<0 or size<0 or offset+size>len(raw):fail('结构越界。')
        return raw[offset:offset+size]
    headers=[struct.unpack('<IIIIIIIIII',area(h[6]+i*h[11],40)) for i in range(h[12])]
    segments=[]
    for i in range(h[10]):
        p=struct.unpack('<IIIIIIII',area(h[5]+i*32,32))
        if p[0]==1:
            if p[4]>p[5]:fail('LOAD文件范围超过内存范围。')
            area(p[1],p[4]);segments.append(dict(offset=p[1],address=p[2],physical=p[3],fileSize=p[4],memorySize=p[5],flags=p[6]))
    if h[13]>=len(headers):fail('节名表无效。')
    strings=area(headers[h[13]][4],headers[h[13]][5])
    def name(table,offset):
        if offset>=len(table):fail('字符串索引越界。')
        end=table.find(b'\0',offset)
        if end<0:fail('字符串未终止。')
        return table[offset:end].decode('ascii',errors='strict')
    sections=[];symbols={}
    for s in headers:
        record=dict(name=name(strings,s[0]),type=s[1],flags=s[2],address=s[3],offset=s[4],size=s[5]);sections.append(record)
        if s[1]!=8:area(s[4],s[5])
        if s[1]==2:
            if s[6]>=len(headers) or s[9]!=16 or s[5]%16:fail('符号表无效。')
            st=headers[s[6]];names=area(st[4],st[5])
            for off in range(s[4],s[4]+s[5],16):
                n,v,size,info,other,index=struct.unpack('<IIIBBH',area(off,16))
                key=name(names,n)
                if key and index!=0:symbols[key]=dict(value=v,size=size,type=info&15,section=index)
    return dict(raw=raw,entry=h[4],sections=sections,segments=segments,symbols=symbols)


def parse_hex(path,flash_end=0x08008000):
    image={};base=0;ended=False;entry=None
    for line in path.read_text(encoding='ascii').splitlines():
        if ended or not line.startswith(':'):fail('HEX记录格式或结束标记错误。')
        try:row=bytes.fromhex(line[1:])
        except ValueError:fail('HEX包含非法字符。')
        if len(row)<5 or len(row)!=row[0]+5 or sum(row)&255:fail('HEX校验和/长度不符。')
        address=int.from_bytes(row[1:3],'big');kind=row[3];data=row[4:-1]
        if kind==0:
            for i,b in enumerate(data):
                target=base+address+i
                if target in image or not 0x08000000<=target<flash_end:fail('HEX越出Flash或地址重叠。')
                image[target]=b
        elif kind==1:
            if data or address:fail('HEX结束记录非法。')
            ended=True
        elif kind in (2,4):
            if len(data)!=2 or address:fail('HEX扩展地址非法。')
            base=int.from_bytes(data,'big')<<(4 if kind==2 else 16)
        elif kind==5:
            if len(data)!=4 or address or entry is not None:fail('HEX启动地址非法。')
            entry=int.from_bytes(data,'big')
        else:fail('不支持的HEX记录类型。')
    if not ended or not image:fail('HEX未完整结束或没有程序。')
    return image,entry


def verify_image(elf_path,bin_path,hex_path,vector_path,profile="STM32G030F6P6"):
    target=get_target(profile);flash_end=0x08000000+target["flashSize"];ram_end=0x20000000+target["ramSize"];vector_bytes=target["vectorWords"]*4
    elf=read_elf(elf_path);sections=elf['sections'];symbols=elf['symbols'];raw=elf['raw']
    vector=next((s for s in sections if s['name']=='.isr_vector'),None)
    if not vector or vector['address']!=0x08000000 or vector['size']!=vector_bytes:fail('必须为所选型号Flash起始的精确向量表。')
    data=raw[vector['offset']:vector['offset']+vector_bytes];words=struct.unpack('<'+str(target['vectorWords'])+'I',data)
    if data!=vector_path.read_bytes():fail('原生objcopy向量与ELF不一致。')
    if words[0]!=ram_end or words[1]!=elf['entry'] or not words[1]&1 or not 0x08000000<words[1]<flash_end:fail('初始栈/Thumb复位入口错误。')
    for key in ['Reset_Handler','SystemInit','main','SysTick_Handler','_estack','_sidata','_sdata','_edata','_sbss','_ebss']:
        if key not in symbols:fail('缺少必需符号 '+key)
    if symbols['Reset_Handler']['value']!=words[1] or symbols['SysTick_Handler']['value']!=words[15]:fail('复位/SysTick符号不匹配。')
    reserved=set(target['reservedVectors'])
    for index in reserved:
        if words[index]!=0:fail('所选型号保留向量槽必须为0。')
    for index,value in enumerate(words[1:],1):
        if index not in reserved and not value:fail('非保留向量槽缺少默认/实际处理函数。')
        if value and (not value&1 or not 0x08000000<value<flash_end):fail('中断向量越出代码区。')
    allocated=[s for s in sections if s['flags']&2 and s['size']]
    flash=[];ram=[];expected={}
    for s in allocated:
        start,end=s['address'],s['address']+s['size']
        if 0x08000000<=start<end<=flash_end:flash.append(s)
        elif 0x20000000<=start<end<=ram_end:ram.append(s)
        else:fail('分配节越出声明Flash/RAM：'+s['name'])
        owners=[p for p in elf['segments'] if p['address']<=start and end<=p['address']+p['memorySize']]
        if len(owners)!=1:fail('分配节缺少唯一LOAD映射。')
        p=owners[0]
        if s['type']!=8:
            physical=p['physical']+start-p['address']
            if not 0x08000000<=physical<physical+s['size']<=flash_end or s['offset']!=p['offset']+start-p['address'] or end>p['address']+p['fileSize']:fail('分配节文件/Flash装载地址无效。')
            for i,b in enumerate(raw[s['offset']:s['offset']+s['size']]):
                if physical+i in expected:fail('ELF装载内容地址重叠。')
                expected[physical+i]=b
    for key in ['Reset_Handler','SystemInit','main','SysTick_Handler']:
        value=symbols[key]['value']
        if not value&1 or not any(s['flags']&4 and s['address']<=value-1<s['address']+s['size'] for s in flash):fail('必需函数不在可执行Flash节中：'+key)
    if symbols['_estack']['value']!=ram_end:fail('链接栈顶与物理RAM不符。')
    sd,ed,sb,eb=[symbols[k]['value'] for k in ['_sdata','_edata','_sbss','_ebss']]
    if not 0x20000000<=sd<=ed<=sb<=eb<=ram_end:fail('启动初始化RAM范围不合法。')
    data_section=next((s for s in sections if s['name']=='.data'),None)
    bss=next((s for s in sections if s['name']=='.bss'),None)
    if not data_section or (sd,ed)!=(data_section['address'],data_section['address']+data_section['size']) or not bss or (sb,eb)!=(bss['address'],bss['address']+bss['size']):fail('启动初始化符号与实际节范围不同。')
    owner=next((p for p in elf['segments'] if p['address']<=sd and ed<=p['address']+p['memorySize']),None)
    if not owner or symbols['_sidata']['value']!=owner['physical']+sd-owner['address']:fail('数据初始化Flash来源不匹配。')
    if symbols['_ebss']['value']>ram_end-1024:fail('不足1KiB栈空间。')
    if any(symbols[k]['value']%4 for k in ['_sidata','_sdata','_edata','_sbss','_ebss']):fail('启动初始化地址未按字对齐。')
    binary=bin_path.read_bytes();image,entry=parse_hex(hex_path,flash_end)
    if not vector_bytes<=len(binary)<=target['flashSize'] or binary[:vector_bytes]!=data or (entry is not None and entry!=elf['entry']):fail('BIN/HEX长度或入口与ELF不同。')
    if max(image)!=0x08000000+len(binary)-1 or any(image.get(0x08000000+i,0)!=b for i,b in enumerate(binary)):fail('HEX/BIN字节不同。')
    if image!=expected:fail('HEX装载字节与ELF全部分配节不同。')
    return dict(passed=True,architecture='ELF32-little-ARM',entry=elf['entry'],initialStack=words[0],vectorWords=target['vectorWords'],profile=profile,flashCapacityBytes=target['flashSize'],ramCapacityBytes=target['ramSize'],
                flashImageBytes=len(binary),ramAllocatedBytes=sum(s['size'] for s in ram),stackHeadroomBytes=ram_end-symbols['_ebss']['value'],
                sections=allocated,symbols={k:symbols[k] for k in ['Reset_Handler','SystemInit','main','SysTick_Handler','_estack']},
                elfSha256=hashlib.sha256(elf['raw']).hexdigest(),binSha256=hashlib.sha256(binary).hexdigest(),hexSha256=hashlib.sha256(hex_path.read_bytes()).hexdigest(),
                nativeVectorMatches=True,hexBinMatch=True,elfLoadBytesMatch=True,startupMemoryRangesMatch=True,hardwareVerified=False,behaviorVerified=False)
