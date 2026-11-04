import argparse
import os
import sys

import uvicorn

from .app import create_app
from .config import Config


def main():
    parser = argparse.ArgumentParser(description="FormForge 本地工程服务")
    parser.add_argument("--port", type=int, default=17840)
    args = parser.parse_args()
    config = Config.default()
    config.port = args.port
    lock = (config.local / "service.lock").open("a+b")
    lock.seek(0)
    if os.name == "nt":
        import msvcrt
        if lock.read(1) == b"":
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("FormForge 已有一个服务使用此数据目录。请打开正在运行的页面。", file=sys.stderr)
            return 1
    else:
        import fcntl
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 1
    try:
        uvicorn.run(create_app(config), host="127.0.0.1", port=config.port, log_level="warning")
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
