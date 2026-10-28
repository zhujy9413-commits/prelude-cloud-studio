"""Local operator recovery. Never submits, cancels, or guesses a provider outcome."""
from contextlib import contextmanager
import fcntl
import re
import sqlite3
import time
from pathlib import Path


@contextmanager
def job_lock(directory, job_id):
    if not re.fullmatch(r'[a-f0-9-]{36}', job_id):
        raise ValueError('作品编号格式不正确')
    root = Path(directory) / 'task-locks'
    root.mkdir(exist_ok=True, mode=0o700)
    with (root / (job_id + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS job_reviews (
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, created_at REAL NOT NULL,
        action TEXT NOT NULL, previous_status TEXT NOT NULL, next_status TEXT NOT NULL,
        evidence TEXT NOT NULL)''')


def resolve(directory, job_id, outcome, evidence, task_id=None):
    """Operator-only evidence based resolution, serialized with the worker."""
    if not isinstance(evidence, str) or not 8 <= len(evidence.strip()) <= 500:
        raise ValueError('请填写 8–500 字的核对依据（不含密钥或签名下载地址）')
    if re.search(r'https?://|sk-[a-zA-Z0-9]|Bearer\s', evidence, re.I):
        raise ValueError('核对依据不能包含网址或密钥，请记录查询时间和核对结论')
    if outcome not in ('attach', 'not-submitted', 'failed', 'completed-unavailable'):
        raise ValueError('不支持的核对结论')
    if outcome == 'attach' and (not isinstance(task_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{10,100}', task_id)):
        raise ValueError('原任务编号格式不正确')
    path = Path(directory) / 'studio.sqlite3'
    if not path.is_file():
        raise ValueError('未找到现有工作室数据库，请先检查目录')
    with job_lock(directory, job_id), sqlite3.connect(path, timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or row['status'] != 'UNCERTAIN':
            raise ValueError('仅能核对待人工确认的任务；任务状态可能已经改变')
        if outcome == 'not-submitted' and row['task_id']:
            raise ValueError('已有原任务编号，不能按未提交释放额度')
        if outcome == 'attach':
            if row['task_id'] or db.execute('SELECT 1 FROM jobs WHERE task_id=?', (task_id,)).fetchone():
                raise ValueError('不能替换已绑定的任务编号或复用其他作品的任务')
            status, error = 'PENDING', ''
        else:
            status = {'not-submitted': 'REJECTED', 'failed': 'FAILED', 'completed-unavailable': 'UNAVAILABLE'}[outcome]
            error = {'not-submitted': '经邀请人核对，本次未提交生成，额度已释放',
                     'failed': '经邀请人核对，原任务生成失败，额度已释放',
                     'completed-unavailable': '原任务已生成，但成片暂不可获取，请联系邀请人'}[outcome]
        schema(db)
        db.execute('UPDATE jobs SET status=?, error=?, task_id=COALESCE(?,task_id), next_check_at=NULL, updated_at=? WHERE id=?',
                   (status, error, task_id if outcome == 'attach' else None, time.time(), job_id))
        db.execute('INSERT INTO job_reviews(job_id,created_at,action,previous_status,next_status,evidence) VALUES(?,?,?,?,?,?)',
                   (job_id, time.time(), outcome, row['status'], status, evidence.strip()))
        return status
