#!/usr/bin/env python3
"""Make a private complete backup during a stopped-service maintenance window."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def backup(data_dir, output_dir):
    source = Path(data_dir).resolve()
    destination = Path(output_dir).resolve()
    if destination == source or source in destination.parents:
        raise ValueError('备份目录必须位于工作室数据目录之外')
    database = source / 'studio.sqlite3'
    if not database.is_file():
        raise ValueError('该目录没有工作室数据库')
    with (source / '.server.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('请先停止工作室服务，在维护窗口备份；不会自动停止正在运行的服务') from None
        paths = sorted(source.rglob('*'))
        if any(path.is_symlink() for path in paths):
            raise ValueError('数据目录中有符号链接，请人工核对后备份')
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('数据库完整性检查未通过，请先核对原数据')
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.prelude-', suffix='.part', dir=destination)
        os.close(fd)
        target = destination / ('prelude-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + os.urandom(4).hex() + '.tar.gz')
        try:
            with tarfile.open(temporary, 'w:gz') as archive:
                for path in paths:
                    if path.name == '.server.lock' or path.suffix == '.part':
                        continue
                    archive.add(path, arcname='prelude-data/' + str(path.relative_to(source)), recursive=False)
            os.replace(temporary, target)
            return target
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=os.environ.get('PRELUDE_DATA_DIR'))
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir is None:
        parser.error('请传入 --data-dir 或设置 PRELUDE_DATA_DIR')
    os.umask(0o077)
    try:
        target = backup(args.data_dir, args.output_dir)
    except ValueError as error:
        parser.error(str(error))
    print('私有备份已保存：' + str(target))


if __name__ == '__main__':
    main()
