"""Run with the managed flash Python. Data-only APIs: never enumerate/connect."""
import base64
import contextlib
import hashlib
import importlib.metadata
import io
import json
import sys


def inspect_esp(request):
    from esptool.bin_image import LoadFirmwareImage
    from esptool.cmds import merge_bin
    from esptool.targets import CHIP_DEFS
    chip = request['target']
    data = base64.b64decode(request['data'], validate=True)
    address, role = request['address'], request['role']
    options = request['options']
    chip_class = CHIP_DEFS[chip]
    offset = chip_class.BOOTLOADER_FLASH_OFFSET - address if role == 'merged' else 0
    image_required = role in ('bootloader', 'application', 'merged')
    if role == 'bootloader' and address != chip_class.BOOTLOADER_FLASH_OFFSET:
        raise ValueError('Bootloader address does not match the chip ROM layout.')
    candidate = data[offset:] if offset >= 0 else b''
    checked = False
    if image_required or (candidate and candidate[0] == 0xe9 and role == 'firmware'):
        image = LoadFirmwareImage(chip, candidate)
        if image.chip_id != chip_class.IMAGE_CHIP_ID:
            raise ValueError('Image chip ID does not match selected target.')
        if image.checksum != image.calculate_checksum(): raise ValueError('ESP image checksum mismatch.')
        if image.append_digest and image.stored_digest != image.calc_digest:
            raise ValueError('ESP image SHA-256 mismatch.')
        checked = True
    # Public merge API applies the same bootloader-header settings without I/O.
    if role == 'merged' and any(v != 'keep' for v in options.values()):
        raise ValueError('Merged images require keep header settings; supply separate images to change them.')
    effective = merge_bin([(address, data)], chip=chip, target_offset=address, **options)
    if len(effective) != len(data): raise ValueError('Unexpected image size change.')
    return dict(data=base64.b64encode(effective).decode('ascii'), imageValidated=checked,
                toolVersion=importlib.metadata.version('esptool'),
                scope='bootloader-only-in-merged' if role == 'merged' else 'image-header-segments-checksum-digest' if checked else 'raw-data')


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        with contextlib.redirect_stdout(io.StringIO()):
            result = inspect_esp(request)
        print(json.dumps(dict(ok=True, result=result)))
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        sys.exit(1)
