#!/usr/bin/env python3
"""Local operator commands. Never starts the generation worker or calls a model."""
import argparse
import getpass
import os
from pathlib import Path
import secrets

from access import Access, AccessError, password_hash, username


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('PRELUDE_DATA_DIR', Path(__file__).resolve().parent / '.studio')),
                        help='工作室私有数据目录；线上与服务使用同一个 PRELUDE_DATA_DIR')
    commands = parser.add_subparsers(dest='command', required=True)
    invite = commands.add_parser('invite', help='创建一次性邀请，密文写入本机私有文件')
    invite.add_argument('--seconds', type=int, default=30)
    invite.add_argument('--days', type=int, default=7)
    commands.add_parser('list', help='只列出邀请编号、状态和账户，不显示邀请码或密码')
    revoke = commands.add_parser('revoke', help='撤销尚未使用的邀请')
    revoke.add_argument('invitation_id')
    limit = commands.add_parser('allowance', help='设置指定通行证的累计总额度，单位秒')
    limit.add_argument('name')
    limit.add_argument('seconds', type=int)
    reset = commands.add_parser('reset-password', help='本机重设密码，并退出该账户所有设备')
    reset.add_argument('name')
    args = parser.parse_args()
    os.umask(0o077)
    runtime = args.data_dir.resolve()
    if not (runtime / 'studio.sqlite3').is_file():
        parser.error('该目录尚无工作室数据库，请核对数据目录或先启动工作室')
    access = Access(runtime / 'studio.sqlite3')
    try:
        if args.command == 'invite':
            directory = runtime / 'invitations'
            directory.mkdir(mode=0o700, exist_ok=True)
            identifier, code = access.invite(args.seconds, args.days)
            target = directory / (identifier + '.txt')
            try:
                with target.open('x') as handle:
                    os.chmod(target, 0o600)
                    handle.write(f'序幕内测邀请\n邀请码：{code}\n可生成总时长：{args.seconds} 秒\n有效期：{args.days} 天，首次开通后失效\n入口：打开序幕首页，点击「内测通行证」→「首次使用」\n')
            except Exception:
                with access.db() as db:
                    db.execute('UPDATE invitations SET revoked=1 WHERE id=?', (identifier,))
                raise
            print('邀请已保存到：' + str(target))
        elif args.command == 'list':
            import time
            with access.db() as db:
                for row in db.execute('SELECT id,seconds,used_by,revoked,expires_at FROM invitations ORDER BY rowid DESC'):
                    state = '已使用' if row['used_by'] else '已撤销' if row['revoked'] else '已过期' if row['expires_at'] <= time.time() else '待使用'
                    print(f"邀请 {row['id']} · {row['seconds']} 秒 · {state}")
                for row in db.execute('SELECT id,name,allowance FROM accounts ORDER BY rowid'):
                    quota = access.quota(row['id'], db)
                    print(f"通行证 {row['name']} · 剩余 {quota['remaining_seconds']} / {row['allowance']} 秒")
        elif args.command == 'revoke':
            with access.db() as db:
                result = db.execute('UPDATE invitations SET revoked=1 WHERE id=? AND used_by IS NULL AND revoked=0', (args.invitation_id,))
                print('邀请已撤销' if result.rowcount else '没有可撤销的邀请；已使用的邀请码不会撤销账户')
        else:
            _, key = username(args.name)
            if args.command == 'allowance' and not 0 <= args.seconds <= 300:
                parser.error('累计总额度请设置在 0–300 秒之间')
            with access.db() as db:
                row = db.execute('SELECT id FROM accounts WHERE name_key=?', (key,)).fetchone()
            if row is None:
                raise AccessError('未找到该通行证')
            if args.command == 'allowance':
                with access.db() as db:
                    db.execute('UPDATE accounts SET allowance=? WHERE id=?', (args.seconds, row['id']))
                print('累计总额度已更新；不会清空历史已用时长或增加每日总预算')
            else:
                password = getpass.getpass('新密码（12–128 字符）：')
                if not 12 <= len(password) <= 128 or password != getpass.getpass('再输入一次：'):
                    raise AccessError('密码长度不符合要求或两次输入不一致')
                salt = secrets.token_hex(16)
                hashed = password_hash(password, salt)
                with access.db() as db:
                    db.execute('UPDATE accounts SET salt=?,password_hash=? WHERE id=?', (salt, hashed, row['id']))
                    db.execute('DELETE FROM account_sessions WHERE account_id=?', (row['id'],))
                print('密码已重设，所有已登录设备已退出')
    except AccessError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
