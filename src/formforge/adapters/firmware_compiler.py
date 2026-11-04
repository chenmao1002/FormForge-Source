"""Configured ARM tools, pinned resources, real builds and deterministic readback."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from .importer import digest,within
from .connectivity import fingerprint
from .firmware_elf import verify_image
from .firmware_policy import validate_sources,validate_preprocessed
from ..store import DomainError
from ..processes import run_process
from .firmware_targets import get_target, catalog
from .firmware_templates import stm32_files

CODE_ROOT=Path(__file__).resolve().parents[3]
TARGET_FLAGS=['-mcpu=cortex-m0plus','-mthumb','-mfloat-abi=soft']


class FirmwareCompiler:
    def __init__(self,config):
        self.config=config;self.issue=None;self.tools={};self.runtimeLibraries={};self.resources={};self.version=None;self.engineHash=None;self.toolchainHash=None
        self.gcc=Path(getattr(config,'arm_gcc',None) or os.getenv('FORMFORGE_ARM_GCC') or shutil.which('arm-none-eabi-gcc') or 'arm-none-eabi-gcc')
        try:
            if not self.gcc.is_file():raise ValueError('未找到已配置的ARM GNU工具链。')
            self.gcc=self.gcc.resolve();self.toolchain_root=self.gcc.parent.parent
            for name in ['gcc','objcopy','readelf','size']:
                p=self.gcc.with_name('arm-none-eabi-'+name+('.exe' if os.name=='nt' else ''))
                if not p.is_file():raise ValueError('缺少ARM工具 '+name)
                self.tools[name]=dict(path=str(p),sha256=digest(p))
            flags=dict(capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            version=subprocess.run([str(self.gcc),'--version'],**flags);self.version=version.stdout.splitlines()[0]
            if version.returncode or 'arm-none-eabi' not in self.version:raise ValueError('编译器目标不匹配。')
            for name in ['cc1','as','ld']:
                run=subprocess.run([str(self.gcc),'-print-prog-name='+name],**flags);p=Path(run.stdout.strip()).resolve()
                if run.returncode or not p.is_file() or not p.is_relative_to(self.toolchain_root):raise ValueError('无法冻结子工具 '+name)
                self.tools[name]=dict(path=str(p),sha256=digest(p))
            runtime=subprocess.run([str(self.gcc),*TARGET_FLAGS,'-print-libgcc-file-name'],**flags)
            selected=Path(runtime.stdout.strip())
            if runtime.returncode or not selected.is_absolute():raise ValueError('无法确定目标ABI对应的编译器运行库。')
            selected=selected.resolve()
            if not selected.is_file() or not selected.is_relative_to(self.toolchain_root) or selected.name!='libgcc.a':raise ValueError('编译器运行库必须来自同一可信工具链。')
            self.runtimeLibraries['libgcc']=dict(path=str(selected),sha256=digest(selected),targetFlags=TARGET_FLAGS.copy())
            for cpu in ['cortex-m3','cortex-m4']:
                target_flags=['-mcpu='+cpu,'-mthumb','-mfloat-abi=soft']
                runtime=subprocess.run([str(self.gcc),*target_flags,'-print-libgcc-file-name'],**flags)
                archive=Path(runtime.stdout.strip())
                if runtime.returncode or not archive.is_absolute() or not archive.is_file() or not archive.resolve().is_relative_to(self.toolchain_root):raise ValueError('无法冻结目标运行库 '+cpu)
                self.runtimeLibraries['libgcc-'+cpu]=dict(path=str(archive.resolve()),sha256=digest(archive),targetFlags=target_flags)
            self.toolchainHash=fingerprint(dict(version=self.version,tools={name:row['sha256'] for name,row in self.tools.items()},
                                               runtimeLibraries={name:row['sha256'] for name,row in self.runtimeLibraries.items()}))
            for file in ['M6-firmware-kernel.json','M6-cmsis.json','M6-stm32-cmsis.json']:
                migration_file=CODE_ROOT/'docs/migrations'/file
                self.resources[migration_file.relative_to(CODE_ROOT).as_posix()]=digest(migration_file)
                migration=json.loads(migration_file.read_text(encoding='utf8'))
                for row in migration['files']:
                    if not row['destination'].startswith('vendor/firmware/'):continue
                    p=CODE_ROOT/row['destination']
                    if digest(p)!=row['sha256']:raise ValueError('固件依赖资源摘要不符。')
                    self.resources[row['destination']]=row['sha256']
            for name in ['firmware.py','firmware_compiler.py','firmware_targets.py','firmware_templates.py','firmware_hardware.py','firmware_policy.py','firmware_elf.py','schematic_evidence.py','importer.py','connectivity.py','__init__.py']:
                p=Path(__file__).with_name(name);self.resources[p.relative_to(CODE_ROOT).as_posix()]=digest(p)
            for name in ['processes.py','jobs.py','store.py','config.py','__init__.py']:
                p=Path(__file__).parents[1]/name;self.resources[p.relative_to(CODE_ROOT).as_posix()]=digest(p)
            p=CODE_ROOT/'scripts/replay-firmware-package.py';self.resources[p.relative_to(CODE_ROOT).as_posix()]=digest(p)
            self.engineHash=fingerprint(self.resources)
            self.engine_root=config.local/'engines/firmware'/self.engineHash
            for name,expected in self.resources.items():
                target=within(self.engine_root,name)
                if target.exists() and digest(target)!=expected:raise ValueError('冻结固件内核缓存改变。')
                if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(CODE_ROOT/name,target)
        except (OSError,ValueError,subprocess.SubprocessError) as error:self.issue=str(error)

    def describe(self):
        return dict(id='arm-gcc-firmware',version='0.8.0',available=self.issue is None,issue=self.issue,profiles=list(catalog()),targets=catalog(),
                    templates=['blink','uart_echo','i2c_probe_uart'],applicationFiles='C/H, bounded preprocessor/assembly/attribute policy',
                    clock='per-target nominal HSI8/HSI16; normal reset; option bytes not read',toolchainVersion=self.version,toolchainHash=self.toolchainHash,
                    engineHash=self.engineHash,runtimeLibraries=self.runtimeLibraries,standardLibrary='compiler builtins only; no libc or host syscalls',osSandbox=False,flashingEnabled=False,hardwareVerified=False,behaviorVerified=False)

    def verify_resources(self):
        if self.issue:raise DomainError('dependency_missing',self.issue)
        for name,expected in self.resources.items():
            if digest(self.engine_root/name)!=expected:raise DomainError('input_changed','冻结固件内核/头文件资源改变。',409)
        try:
            for row in [*self.tools.values(),*self.runtimeLibraries.values()]:
                if digest(Path(row['path']))!=row['sha256']:raise DomainError('input_changed','工具链或编译器运行库字节已改变，拒绝沿用原摘要。',409)
        except OSError as error:
            raise DomainError('input_changed','工具链或编译器运行库已不可读取，拒绝沿用原摘要。',409) from error

    def seed(self,task,period=None,baud=None,profile='STM32G030F6P6'):
        self.verify_resources()
        target=get_target(profile);clock=target['clockHz']
        if baud is not None and (clock/baud<16 or abs(clock/round(clock/baud)-baud)/baud>.02):raise DomainError('invalid_input','UART整数分频误差超过本配置2%额度或16倍采样范围；未包含实际HSI误差。')
        if task not in target['templates']:raise DomainError('invalid_input','所选型号尚未适配此模板。')
        if profile!='STM32G030F6P6':return stm32_files(profile,task,period or 1000,baud or 115200)
        file=self.engine_root/'vendor/firmware/kiforge_snapshot/g030_generate.py';spec=importlib.util.spec_from_file_location('formforge_g030_seed',file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.g030_files(dict(task=task,period_ms=period or 1000,baud=baud or 115200))

    def build(self,source,output,cancel,phase,timeout=180):
        design=json.loads((source/'design.json').read_text('utf8')) if (source/'design.json').is_file() else {}
        target=get_target(design.get('profile','STM32G030F6P6'))
        target_flags=['-mcpu='+target['cpu'],'-mthumb','-mfloat-abi=soft']
        runtime=self.runtimeLibraries['libgcc' if target['cpu']=='cortex-m0plus' else 'libgcc-'+target['cpu']]
        self.verify_resources();output.mkdir(parents=True,exist_ok=True);workspace=output/'workspace';workspace.mkdir();commands=[]
        for folder in ['app','platform','sdk']:shutil.copytree(source/folder,workspace/folder)
        app=workspace/'app';sdk=workspace/'sdk';sdk_names={p.name for p in sdk.glob('*.h')};policy=validate_sources(app,sdk_names)
        deadline=time.monotonic()+timeout;temp=output/'temp';temp.mkdir();env={**os.environ,'TMP':str(temp),'TEMP':str(temp)}
        for name in ['CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','OBJC_INCLUDE_PATH','GCC_EXEC_PREFIX','COMPILER_PATH','LIBRARY_PATH','DEPENDENCIES_OUTPUT','SUNPRO_DEPENDENCIES','GCC_COMPARE_DEBUG','LD_PRELOAD']:
            env.pop(name,None)
        def run(name,args):
            cancel();phase(name);log=output/f'command-{len(commands)+1}.log'
            row=run_process(args,workspace,log,env,cancel,deadline);row['name']=name;commands.append(row)
            if row['exitCode']:raise DomainError('tool_failed',name+'失败：'+log.read_text(encoding='utf8',errors='replace')[-3000:])
        common=[*target_flags,'-O2','-std=gnu11','-Wall','-Wextra','-Werror','-Wdate-time','-ffreestanding','-fno-builtin',
                '-fno-tree-loop-distribute-patterns','-ffunction-sections','-fdata-sections','-D'+target['header'][:-2].replace('stm32','STM32'),'-I'+str(sdk),'-I'+str(app),'-ffile-prefix-map='+str(workspace)+'=/formforge','-ffile-prefix-map='+workspace.as_posix()+'=/formforge']
        objects=[];deps={}
        try:
            for index,file in enumerate(sorted(app.rglob('*.c'))):
                pre=output/f'unit-{index}.i';run('预处理 '+file.relative_to(app).as_posix(),[self.tools['gcc']['path'],*common,'-E',str(file),'-o',str(pre)])
                for p in validate_preprocessed(pre,app,sdk,self.toolchain_root):deps[str(p)]=digest(p)
                obj=output/f'unit-{index}.o';run('编译 '+file.relative_to(app).as_posix(),[self.tools['gcc']['path'],*common,'-c',str(file),'-o',str(obj)]);objects.append(obj)
            obj=output/'startup.o';run('编译固定启动',[self.tools['gcc']['path'],*common,'-c',str(workspace/'platform/startup.c'),'-o',str(obj)]);objects.append(obj)
            elf=output/'firmware.elf';run('链接固定内存布局',[self.tools['gcc']['path'],*common,'-nostdlib','-Wl,--gc-sections','-Wl,--build-id=none','-Wl,-Map='+str(output/'firmware.map'),'-T',str(workspace/'platform/linker.ld'),*map(str,objects),runtime['path'],'-o',str(elf)])
            for flag,name in [('binary','firmware.bin'),('ihex','firmware.hex')]:run('生成 '+name,[self.tools['objcopy']['path'],'-O',flag,str(elf),str(output/name)])
            run('原生向量表回读',[self.tools['objcopy']['path'],'--dump-section','.isr_vector='+str(output/'vectors.bin'),str(elf)])
            run('原生ELF架构回读',[self.tools['readelf']['path'],'-h','-A',str(elf)])
            attributes=(output/commands[-1]['log']).read_text(encoding='utf8',errors='replace')
            expected_arch={'cortex-m0plus':'v6S-M','cortex-m3':'v7','cortex-m4':'v7E-M'}[target['cpu']]
            if ('Tag_CPU_arch: '+expected_arch+'\n') not in attributes or 'Tag_FP_arch:' in attributes:raise DomainError('tool_failed','ELF架构属性与所选内核不一致。')
            native=verify_image(elf,output/'firmware.bin',output/'firmware.hex',output/'vectors.bin',target['name'])
            self.verify_resources()
            if any(digest(Path(p))!=h for p,h in deps.items()):raise DomainError('input_changed','编译依赖在构建期间改变。',409)
            return dict(checksPassed=True,compilerPassed=True,imageChecks=native,commands=commands,dependencies=deps,sourcePolicy=policy,behaviorVerified=False,hardwareVerified=False,manufacturingApproved=False)
        except DomainError as error:
            return dict(checksPassed=False,compilerPassed=False,error=error.message,errorCode=error.code,commands=commands,dependencies=deps,sourcePolicy=policy,behaviorVerified=False,hardwareVerified=False,manufacturingApproved=False)
