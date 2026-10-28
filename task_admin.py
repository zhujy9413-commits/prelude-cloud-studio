#!/usr/bin/env python3
"""Run locally after checking the original task in the provider console."""
import argparse
from pathlib import Path
import sqlite3
from task_recovery import resolve


def main():
    parser = argparse.ArgumentParser(description='待核对任务管理；不会发起模型生成')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / '.studio')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='列出待核对作品，不输出密钥或模型任务编号')
    review = commands.add_parser('resolve', help='仅在百炼控制台核实原任务后使用；结论会留审计记录')
    review.add_argument('job_id')
    review.add_argument('outcome', choices=['attach', 'not-submitted', 'failed', 'completed-unavailable'])
    review.add_argument('--evidence', required=True, help='查询时间、核对结论等，不含密钥或下载地址')
    review.add_argument('--task-id-file', type=Path, help='attach 时提供只含原任务编号的私有文件')
    args = parser.parse_args()
    path = args.data_dir / 'studio.sqlite3'
    if not path.is_file():
        parser.error('数据库不存在，请核对 --data-dir')
    if args.command == 'list':
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            for row in db.execute("SELECT id,datetime(created_at,'unixepoch','localtime'),task_id IS NOT NULL FROM jobs WHERE status='UNCERTAIN' ORDER BY created_at"):
                print(f'{row[0]} | {row[1]} | ' + ('已绑定原任务，可在作品中重新查询' if row[2] else '未取得原任务编号，需要控制台核对'))
        return
    try:
        task_id = args.task_id_file.read_text().strip() if args.task_id_file else None
        status = resolve(args.data_dir, args.job_id, args.outcome, args.evidence, task_id)
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(1, '处理未完成：请检查依据、原任务编号和作品状态，或等当前查询结束后再试。不会重新提交生成。\n')
    print('核对已记录，作品状态：' + status)


if __name__ == '__main__':
    main()
