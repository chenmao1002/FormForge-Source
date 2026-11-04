"""Isolated SDK boundary. Importing this module never enumerates hardware.

Invoked only by an explicitly authorized firmware.device job. Tests inject drivers.
"""
import base64
import hashlib
import json
import sys
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class PyOCDDriver:
    @staticmethod
    def discover():
        from pyocd.core.helpers import ConnectHelper
        return [dict(selector=p.unique_id, description=p.description) for p in
                ConnectHelper.get_all_connected_probes(blocking=False)]

    def __init__(self, request):
        from pyocd.core.helpers import ConnectHelper
        from pyocd.core.session import Session
        probes = [p for p in ConnectHelper.get_all_connected_probes(blocking=False)
                  if p.unique_id == request['selector']]
        if len(probes) != 1: raise ValueError('Selected probe is absent or ambiguous')
        (Path.cwd()/'disabled-user-script.py').write_text('# No project scripts.\n', encoding='utf8')
        # Use a fresh job directory, no YAML or user script discovery; never auto-unlock.
        self.profile=request.get('deviceProfile') or dict(targetName='stm32g030f6px',deviceIdAddress=0x40015800,
            deviceId=0x466,flashSizeAddress=0x1fff75e0,uidAddress=0x1fff7590,size=32768,eraseSectors=[(0,2048)])
        self.chip=request.get('target','STM32G030F6P6')
        self.session = Session(probes[0], options=dict(target_override=self.profile['targetName'],
            pack=request['pack'], project_dir=str(Path.cwd()), no_config=True,
            user_script=str(Path.cwd()/'disabled-user-script.py'), auto_unlock=False,
            connect_mode='halt', resume_on_disconnect=False, frequency=1000000))
        try:
            self.session.open()
            self.target = self.session.target
        except BaseException:
            self.session.close()
            raise

    def identity(self):
        t = self.target
        if t.is_locked(): raise ValueError('Target is protected; automatic unlocking is disabled')
        # STM32G0 vendor CMSIS header: UID_BASE / FLASHSIZE_BASE. G03x/04x device ID.
        device_id = t.read32(self.profile['deviceIdAddress']) & 0xfff
        size = t.read16(self.profile['flashSizeAddress']) * 1024
        if device_id != self.profile['deviceId'] or size != self.profile['size']:
            raise ValueError('Target device family or Flash capacity does not match selected target')
        uid = bytes(t.read_memory_block8(self.profile['uidAddress'], 12)).hex()
        if uid in ('00'*12, 'ff'*12): raise ValueError('Invalid silicon UID')
        region = t.memory_map.get_region_for_address(0x08000000)
        sectors = region.flm.sector_sizes if region and region.flm else []
        if not region or region.length != size or [list(row) for row in sectors] != [list(row) for row in self.profile['eraseSectors']]:
            raise ValueError('Pack Flash geometry does not match plan')
        return dict(chip=self.chip, siliconId=uid, flashSize=size, deviceId=device_id,
                    protected=False, identityScope='device family + capacity + UID; exact package selected by operator')

    def write(self, segments):
        from pyocd.flash.loader import FlashLoader
        loader = FlashLoader(self.session, chip_erase='sector', smart_flash=False,
                             trust_crc=False, keep_unwritten=False, no_reset=True)
        for address, data in segments: loader.add_data(address, data)
        loader.commit()

    def read(self, address, size):
        return bytes(self.target.read_memory_block8(address, size))

    def reset(self): self.target.reset()
    def close(self): self.session.close()


class ESPDriver:
    @staticmethod
    def discover():
        from serial.tools.list_ports import comports
        return [dict(selector=p.device, description=p.description, serialNumber=p.serial_number,
                     vid=p.vid, pid=p.pid) for p in comports()]

    def __init__(self, request):
        import esptool
        self.api = esptool
        # Exact local port only. Do not allow pyserial URLs or implicit port selection.
        if request['selector'] not in {p['selector'] for p in self.discover()}:
            raise ValueError('Selected local serial port is absent')
        self.esp = esptool.connect_esp(port=request['selector'], chip='auto',
                                       initial_baud=115200, connect_attempts=3)

    def identity(self):
        e = self.esp
        if e.secure_download_mode: raise ValueError('Secure download mode is unsupported')
        protected = bool(e.get_secure_boot_enabled() or e.get_flash_encryption_enabled())
        if protected: raise ValueError('Secure boot/encrypted target is unsupported; no override permitted')
        self.api.attach_flash(e)
        size = self.api.detect_flash_size(e)
        if not size or not size.endswith('MB'): raise ValueError('Cannot establish Flash capacity')
        return dict(chip=e.CHIP_NAME.lower().replace('-', ''), siliconId=bytes(e.read_mac()).hex(),
                    flashSize=int(size[:-2])*1024*1024, flashId=e.flash_id(), protected=False,
                    identityScope='ROM chip + base MAC + JEDEC capacity')

    def write(self, segments):
        self.esp = self.api.run_stub(self.esp)
        self.api.write_flash(self.esp, segments, flash_mode='keep', flash_freq='keep',
                             flash_size='keep', force=False, erase_all=False, compress=False, no_compress=True)

    def read(self, address, size):
        if not self.esp.IS_STUB: self.esp = self.api.run_stub(self.esp)
        return self.api.read_flash(self.esp, address, size, no_progress=True)

    def reset(self): self.api.reset_chip(self.esp, 'hard-reset')
    def close(self): self.esp.__exit__(None, None, None)


def execute(request, report, save, drivers=None):
    drivers = drivers or dict(pyocd=PyOCDDriver, esptool=ESPDriver)
    driver_type = drivers[request['backend']]
    report.update(phase='discover' if request['action']=='discover' else 'connect',
                  outcome='no-write-attempted', verified=False, resetPerformed=False)
    save(report)
    if request['action'] == 'discover':
        report.update(devices=driver_type.discover(), phase='completed')
        save(report)
        return report
    driver = driver_type(request)
    try:
        identity = dict(backend=request['backend'], selector=request['selector'], **driver.identity())
        if identity['chip'] != request['target']: raise ValueError('Connected chip does not match selected target')
        report.update(identity=identity, identitySha256=digest(identity), phase='identified')
        save(report)
        if request['action'] == 'identify': return report
        if report['identitySha256'] != request['expectedIdentitySha256']:
            raise ValueError('Device identity changed since identification')
        if identity['flashSize'] != request['targetProfile']['size']:
            raise ValueError('Device Flash capacity differs from frozen plan')
        segments = [(r['address'], base64.b64decode(r['data'], validate=True)) for r in request['segments']]
        if request['action'] == 'program':
            # Persist before calling SDK: a crash/cancel during write is conservatively partial/unknown.
            report.update(phase='write', outcome='possibly-partial')
            save(report)
            driver.write([(r['address'], base64.b64decode(r['data'], validate=True))
                          for r in request.get('writeSegments', request['segments'])])
        report['phase'] = 'verify'
        save(report)
        checked = []
        for address, data in segments:
            actual = hashlib.sha256()
            for offset in range(0, len(data), 4096):
                part = data[offset:offset+4096]
                read = driver.read(address+offset, len(part))
                if read != part: raise ValueError('Flash readback mismatch at '+hex(address+offset))
                actual.update(read)
            checked.append(dict(address=address, size=len(data), sha256=actual.hexdigest()))
        report.update(verified=True, verifiedRanges=checked, verificationMethod='full-byte-readback',
                      outcome='written-and-verified' if request['action']=='program' else 'verified')
        save(report)
        if request.get('resetAfter'):
            report['phase'] = 'reset'; save(report)
            driver.reset(); report['resetPerformed'] = True
        report['phase'] = 'completed'; save(report)
        return report
    finally:
        driver.close()


def main():
    request_path, report_path = map(Path, sys.argv[1:3])
    report = dict(schemaVersion='formforge.device-report/1')
    def save(value):
        temp = report_path.with_suffix('.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
        temp.replace(report_path)
    try:
        execute(json.loads(request_path.read_text(encoding='utf8')), report, save)
    except BaseException as error:
        report.update(error=str(error), failedPhase=report.get('phase'), phase='failed')
        save(report)
        raise


if __name__ == '__main__': main()
