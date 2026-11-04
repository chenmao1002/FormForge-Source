"""Bounded application C policy; this does not claim operating-system isolation."""
import re
from pathlib import PurePosixPath
from ..store import DomainError

STANDARD_HEADERS={'stdint.h','stddef.h','stdbool.h','limits.h','string.h'}
TOKENS=re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/',re.S)


def fail(message):raise DomainError('invalid_input','应用源码策略：'+message)


def filename(value):
    p=PurePosixPath(value)
    if not re.fullmatch(r'[A-Za-z0-9_./-]{1,180}',value) or p.is_absolute() or '..' in p.parts or str(p)!=value or p.suffix not in {'.c','.h'}:
        fail('文件只支持工程内的普通C/H相对路径。')
    for part in p.parts:
        stem=part.split('.')[0].upper()
        if part.endswith('.') or stem in {'CON','PRN','AUX','NUL'} or re.fullmatch(r'(?:COM|LPT)[1-9]',stem):
            fail('应用路径不能使用Windows设备保留名或以点结尾的目录。')
    return value


def uncomment(text):
    text=re.sub(r'\\\r?\n','',text)
    return TOKENS.sub(lambda m:(''.join('\n' if c=='\n' else ' ' for c in m[0]) if m[0].startswith(('//','/*')) else m[0]),text)


def no_strings(text):return TOKENS.sub(lambda m:' ' if m[0].startswith(('"',"'")) else m[0],text)


def validate_sources(app, sdk_names):
    files={p.relative_to(app).as_posix() for p in app.rglob('*') if p.is_file()}
    if not files or 'main.c' not in files or len(files)>40:fail('需要main.c，应用文件不能超过40个。')
    if sum((app/f).stat().st_size for f in files)>2*1024*1024:fail('应用源码合计超过2MiB。')
    for name in files:
        filename(name)
        if PurePosixPath(name).name in sdk_names|STANDARD_HEADERS:fail('应用文件不能覆盖平台/标准头文件。')
        raw=(app/name).read_bytes()
        if len(raw)>256*1024:fail('单个源码文件超过256KiB。')
        try:text=raw.decode('utf-8-sig')
        except UnicodeError:fail('应用源码必须为UTF-8。')
        if '\0' in text:fail('应用源码包含NUL。')
        clean=uncomment(text).replace('%:','#')
        masked=no_strings(clean)
        if '##' in masked or re.search(r'\b(?:_Pragma|__has_include|__has_embed)\b',masked) or re.search(r'\?\?[=/\'()!<>-]',clean):
            fail('首版不接受token拼接、动态预处理探查/pragma或trigraph。')
        for line in clean.splitlines():
            if re.match(r'^\s*#\s*\S',line) and not re.match(r'^\s*#\s*[A-Za-z_]',line):fail('不接受自定义数值行标记。')
            match=re.match(r'^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)(.*)$',line)
            if not match:continue
            directive,tail=match.groups()
            if directive=='include':
                include=re.fullmatch(r'\s*(?:"([A-Za-z0-9_./-]+)"|<([A-Za-z0-9_./-]+)>)\s*',tail)
                if not include:fail('include必须是明确的工程/平台/标准头文件文字。')
                header=include[1] or include[2]
                if '..' in PurePosixPath(header).parts or header.startswith('/'):fail('include不能越出冻结输入。')
                relative=(PurePosixPath(name).parent/header).as_posix()
                if header not in sdk_names|STANDARD_HEADERS and header not in files and relative not in files:
                    fail('include未登记的文件：'+header)
            elif directive not in {'define','undef','if','ifdef','ifndef','elif','else','endif','error','warning'}:
                fail('不支持的预处理指令：'+directive)
    return dict(files=len(files),bytes=sum((app/f).stat().st_size for f in files),policy='bounded-C11-input-v1',osSandbox=False)


def validate_preprocessed(path,app,sdk,toolchain_root):
    origin=None;dependencies=set();user=[]
    for line in path.read_text(encoding='utf8',errors='strict').splitlines():
        marker=re.match(r'^\s*#\s+\d+\s+"([^"]+)"',line)
        if marker:
            value=marker[1].replace('\\\\','\\')
            if value.startswith('<'):origin=None;continue
            from pathlib import Path
            origin=Path(value).resolve()
            if not any(origin.is_relative_to(root.resolve()) for root in [app,sdk,toolchain_root]):fail('预处理读取了冻结工程/工具链以外的文件。')
            dependencies.add(origin)
        elif origin and origin.is_relative_to(app.resolve()):user.append(line)
    expanded='\n'.join(user)
    # CMSIS maps WFI to one literal asm hint at the calling source line.
    expanded=re.sub(r'\b(?:__asm(?:__)?|asm)\s+(?:__volatile(?:__)?|volatile)\s*\(\s*"(?:wfi|wfe|nop|sev|yield|dsb 0xF|isb 0xF)"\s*(?::\s*:\s*:\s*"memory"\s*)?\)',' ',expanded)
    expanded=re.sub(r'\b__attribute(?:__)?\s*\(\(\s*(?:used|unused|packed|noreturn)\s*\)\)',' ',expanded)
    if re.search(r'\b(?:__asm(?:__)?|asm|__attribute(?:__)?|__declspec|_Pragma)\b',no_strings(expanded)) or '[[' in no_strings(expanded):
        fail('应用预处理结果含未支持的汇编/链接属性；仅允许固定CPU提示及已列安全属性。')
    return sorted(dependencies)
