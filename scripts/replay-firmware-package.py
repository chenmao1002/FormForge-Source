"""Verify an expected FormForge package, then rebuild using its frozen engine.

Invoked explicitly for package acceptance, never by the source registry or by
firmware application code. Does not execute the generated ARM image or flash.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import zipfile


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--gcc')
    args=parser.parse_args();package=Path(args.package).resolve();output=Path(args.output).resolve()
    if sha(package)!=args.expected_sha256:raise ValueError('Package SHA mismatch')
    workspace=Path(__file__).resolve().parents[1]
    if not output.is_relative_to(workspace) or output.exists():raise ValueError('Replay requires a new output directory inside FormForge')
    output.mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:
        names=set();size=0
        for row in archive.infolist():
            name=row.filename;p=PurePosixPath(name)
            canonical=p.as_posix()+('/' if row.is_dir() else '')
            if p.is_absolute() or PureWindowsPath(name).is_absolute() or '..' in p.parts or '\\' in name or ':' in name or canonical!=name or name.casefold() in names or (row.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe/duplicate ZIP entry')
            names.add(name.casefold());size+=row.file_size
            if size>64*1024*1024:raise ValueError('Package exceeds replay allowance')
            target=output.joinpath(*p.parts)
            if not target.is_relative_to(output):raise ValueError('ZIP path escapes output')
        archive.extractall(output)
    revision=json.loads((output/'formforge-manifest.json').read_text(encoding='utf8'));source=output/'source'
    expected={row['path']:row['sha256'] for row in revision['manifest']}
    actual={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()}
    if actual!=set(expected) or any(sha(source/name)!=digest for name,digest in expected.items()):raise ValueError('Source manifest mismatch')
    native=json.loads((output/'formforge-output-hashes.json').read_text(encoding='utf8'))
    if any(sha(output/'outputs'/name)!=digest for name,digest in native.items()):raise ValueError('Output manifest mismatch')
    # No imports from the active checkout; this engine closure came from the
    # package whose expected digest was checked above. Avoid writes into source.
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(source/'engine/src'))
    from formforge.config import Config
    from formforge.adapters.firmware_compiler import FirmwareCompiler
    from formforge.adapters.firmware_hardware import parse_hardware,validate_roles
    design=json.loads((source/'design.json').read_text(encoding='utf8'))
    hardware=parse_hardware(source/'hardware/netlist.xml')
    if hardware['hardwareHash']!=design['hardwareDependency']['hardwareHash']:raise ValueError('Hardware hash mismatch')
    validate_roles(hardware,design['hardwareDependency']['targetReference'],design['pinBindings'],design.get('template'))
    compiler=FirmwareCompiler(Config(output/'runtime',arm_gcc=args.gcc))
    if compiler.issue or compiler.engineHash!=design['engineHash'] or compiler.toolchainHash!=design['toolchainHash']:raise ValueError('Frozen engine/toolchain identity mismatch: '+str(compiler.issue))
    result=compiler.build(source,output/'rebuild',lambda:None,lambda phase:print(phase,flush=True),180)
    if not result['checksPassed']:raise ValueError(result.get('error'))
    images={name:sha(output/'rebuild'/name) for name in ['firmware.elf','firmware.hex','firmware.bin']}
    if any(digest!=native['build/'+name] for name,digest in images.items()):raise ValueError('Rebuilt ELF/HEX/BIN bytes differ')
    if {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()}!=actual or any(sha(source/name)!=digest for name,digest in expected.items()):raise ValueError('Replay changed immutable source')
    proof=dict(passed=True,engineImportedFrom=str(source/'engine/src'),engineHash=compiler.engineHash,toolchainHash=compiler.toolchainHash,
        filesVerified=len(expected),originalOutputsVerified=len(native),imageHashes=images,imageChecks=result['imageChecks'],sourceUnchanged=True,
        hardwareVerified=False,behaviorVerified=False,flashingPerformed=False)
    (output/'replay-verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf8')
    print('INDEPENDENT REBUILD VERIFIED',len(expected),'source files; ELF/HEX/BIN exact',flush=True)


if __name__=='__main__':main()
