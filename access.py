"""Invitation-only identities for the small Prelude beta. No external auth service."""
from contextlib import contextmanager
import hashlib
import re
import secrets
import sqlite3
import time
import unicodedata

PASSWORD_ROUNDS = 600_000
SESSION_SECONDS = 30 * 86400


class AccessError(Exception):
    def __init__(self, message, status=400, code='access_invalid'):
        super().__init__(message)
        self.status, self.code = status, code


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def username(value):
    name = unicodedata.normalize('NFKC', value.strip()) if isinstance(value, str) else ''
    if not re.fullmatch(r'[\w-]{2,24}', name):
        raise AccessError('通行名请用 2–24 个中文、字母、数字、下划线或短横线')
    return name, name.casefold()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()


class Access:
    def __init__(self, db_path):
        self.path = db_path
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    allowance INTEGER NOT NULL, quota_from REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, seconds INTEGER NOT NULL,
                    expires_at REAL NOT NULL, used_by TEXT, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS access_attempts (bucket TEXT NOT NULL, attempted_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS access_attempt_bucket ON access_attempts(bucket, attempted_at);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def invite(self, seconds=30, days=7):
        if type(seconds) is not int or not 2 <= seconds <= 300 or type(days) is not int or not 1 <= days <= 30:
            raise AccessError('邀请码额度为 2–300 秒，有效期为 1–30 天')
        code, identifier = secrets.token_urlsafe(24), secrets.token_hex(8)
        with self.db() as db:
            db.execute('INSERT INTO invitations(id,code_hash,seconds,expires_at) VALUES(?,?,?,?)',
                       (identifier, digest(code), seconds, time.time() + days * 86400))
        return identifier, code

    def account_for(self, token_hash):
        with self.db() as db:
            row = db.execute('''SELECT a.* FROM accounts a JOIN account_sessions s ON s.account_id=a.id
                WHERE s.token_hash=? AND s.expires_at>? AND a.enabled=1''', (token_hash, time.time())).fetchone()
        return dict(row) if row else None

    def quota(self, account_id, db=None):
        if db is None:
            with self.db() as connection:
                return self.quota(account_id, connection)
        row = db.execute('SELECT * FROM accounts WHERE id=? AND enabled=1', (account_id,)).fetchone()
        if row is None:
            raise AccessError('请先使用内测通行证登录', 401, 'login_required')
        used = db.execute('''SELECT COALESCE(SUM(duration),0) FROM jobs WHERE owner=? AND created_at>=?
            AND status NOT IN ('FAILED','REJECTED')''', (account_id, row['quota_from'])).fetchone()[0]
        return {'name': row['name'], 'total_seconds': row['allowance'], 'used_seconds': used,
                'remaining_seconds': max(0, row['allowance'] - used)}

    def _attempt(self, name_key, peer):
        # Persist reservations before hashing, so parallel attempts cannot bypass the limit.
        buckets = [(digest('peer:' + peer), 40), (digest('name:' + name_key), 6)]
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM access_attempts WHERE attempted_at<?', (now - 900,))
            if any(db.execute('SELECT COUNT(*) FROM access_attempts WHERE bucket=?', (key,)).fetchone()[0] >= limit
                   for key, limit in buckets):
                raise AccessError('尝试次数较多，请 15 分钟后再试', 429, 'login_limited')
            db.executemany('INSERT INTO access_attempts VALUES(?,?)', [(key, now) for key, _ in buckets])

    def authenticate(self, action, payload, guest_owner, old_token_hash, peer):
        if not isinstance(payload, dict):
            raise AccessError('请填写通行证信息')
        raw_name = payload.get('name')
        attempt_name = unicodedata.normalize('NFKC', raw_name[:100]).strip().casefold() if isinstance(raw_name, str) else ''
        self._attempt(attempt_name, str(peer))
        display, name_key = username(raw_name)
        password = payload.get('password')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise AccessError('密码请填写 12–128 个字符，可使用一段容易记住的短语')
        if action not in ('activate', 'login'):
            raise AccessError('不支持的通行证操作')
        token = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        with self.db() as db:
            row = db.execute('SELECT * FROM accounts WHERE name_key=?', (name_key,)).fetchone()
        # Use the same expensive check for missing usernames to reduce account probing.
        hashed = password_hash(password, row['salt'] if row and action == 'login' else salt)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if action == 'activate':
                code = payload.get('invitation')
                invitation = db.execute('SELECT * FROM invitations WHERE code_hash=?',
                                        (digest(code.strip()[:200]) if isinstance(code, str) else '',)).fetchone()
                if not invitation or invitation['used_by'] or invitation['revoked'] or invitation['expires_at'] <= time.time():
                    raise AccessError('邀请码无效、已使用或已过期，请向邀请人核对', 403, 'invite_invalid')
                if db.execute('SELECT 1 FROM accounts WHERE name_key=?', (name_key,)).fetchone():
                    raise AccessError('这个通行名已被使用，请换一个，或切换到登录', 409, 'name_taken')
                if db.execute('SELECT 1 FROM accounts WHERE id=?', (guest_owner,)).fetchone():
                    raise AccessError('请先退出当前通行证', 409, 'already_signed_in')
                account_id = 'account-' + secrets.token_hex(16)
                db.execute('INSERT INTO accounts(id,name,name_key,salt,password_hash,allowance,quota_from) VALUES(?,?,?,?,?,?,?)',
                           (account_id, display, name_key, salt, hashed, invitation['seconds'], time.time()))
                db.execute('UPDATE invitations SET used_by=? WHERE id=?', (account_id, invitation['id']))
                # Registration binds this browser's existing work; historical work does not spend the new allowance.
                db.execute('UPDATE jobs SET owner=? WHERE owner=?', (account_id, guest_owner))
                db.execute('UPDATE reference_images SET owner=? WHERE owner=?', (account_id, guest_owner))
            else:
                current = db.execute('SELECT * FROM accounts WHERE name_key=?', (name_key,)).fetchone()
                if not row or not current or not current['enabled'] or current['password_hash'] != row['password_hash'] or not secrets.compare_digest(hashed, current['password_hash']):
                    raise AccessError('通行名或密码不正确，请重新核对', 401, 'login_invalid')
                account_id = current['id']
            db.execute('DELETE FROM account_sessions WHERE token_hash=? OR expires_at<=?', (old_token_hash, time.time()))
            db.execute('INSERT INTO account_sessions VALUES(?,?,?)', (digest(token), account_id, time.time() + SESSION_SECONDS))
            db.execute('DELETE FROM access_attempts WHERE bucket=?', (digest('name:' + name_key),))
        return token, account_id

    def logout(self, token_hash):
        with self.db() as db:
            db.execute('DELETE FROM account_sessions WHERE token_hash=?', (token_hash,))
