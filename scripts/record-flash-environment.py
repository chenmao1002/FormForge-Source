"""Record managed packages and the exact G030 pack, without accessing USB."""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import os
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / '.local/cmsis-packs/Keil.STM32G0xx_DFP.2.1.0.pack'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    from pyocd.target.pack.cmsis_pack import CmsisPack
    pack = CmsisPack(PACK)
    device = next(d for d in pack.devices if d.part_number.lower() == 'stm32g030f6px')
    regions = [dict(name=r.name, start=r.start, length=r.length, isFlash=r.is_flash,
                    sectorSize=(r.flm.sector_sizes[0][1] if r.is_flash else None)) for r in device.memory_map]
    flash = next(r for r in regions if r['isFlash'])
    assert flash['start'] == 0x08000000 and flash['length'] == 32768 and flash['sectorSize'] == 2048
    assert digest(PACK) == '3076c0f9a6a9a99f7a2325a2e519bed0ac3a3c1db3553748ceeb5b0676645889'
    packages = []
    for d in sorted(metadata.distributions(), key=lambda d:d.metadata['Name'].lower()):
        files = []
        for relative in d.files or []:
            path = Path(d.locate_file(relative)).resolve()
            if path.is_file() and path.suffix != '.pyc' and path.is_relative_to(ROOT):
                files.append(dict(path=path.relative_to(ROOT).as_posix(), sha256=digest(path)))
        packages.append(dict(name=d.metadata['Name'], version=d.version,
                             license=d.metadata.get('License-Expression') or d.metadata.get('License'),
                             source='https://pypi.org/project/'+d.metadata['Name']+'/'+d.version+'/', files=files))
    record = dict(schemaVersion='formforge.flash-environment/1', recordedAt=datetime.now(timezone.utc).isoformat(),
                  installationIndex=os.environ.get('FORMFORGE_FLASH_INSTALL_INDEX', 'https://pypi.tuna.tsinghua.edu.cn/simple'),
                  python=str(Path(sys.executable).relative_to(ROOT)), pythonSha256=digest(Path(sys.executable)),
                  packageVersions={r['name']:r['version'] for r in packages},
                  pack=dict(path=PACK.relative_to(ROOT).as_posix(), sha256=digest(PACK),
                            source='https://www.keil.com/pack/Keil.STM32G0xx_DFP.2.1.0.pack',
                            version='2.1.0', target=device.part_number, regions=regions,
                            license='License files retained inside the original CMSIS-Pack'),
                  hardwareVerified=False, deviceAccessPerformed=False)
    detail = ROOT / '.local/flash-environment-files.json'
    detail.write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding='utf8')
    record.update(filesManifest=detail.relative_to(ROOT).as_posix(), filesManifestSha256=digest(detail))
    (ROOT/'.local/flash-environment.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(record, ensure_ascii=True))


if __name__ == '__main__': main()
