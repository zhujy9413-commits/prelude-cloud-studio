#!/usr/bin/env python3
"""Local Prelude studio: same-origin UI, persistent jobs, server-side Bailian calls."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import fcntl
import io
from http import cookies
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from access import Access, AccessError
from task_recovery import job_lock, schema as recovery_schema
from generation_spec import (MODEL, IMAGE_MODEL, SIZES, API_PATH, encoded as request_bytes,
                             digest as request_digest, snapshot as generation_snapshot,
                             payload as generation_payload, frozen_payload, public_record)

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:
    raise SystemExit('图片上传需要 Pillow，请先运行 python3 -m pip install -r requirements.txt') from None

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'video-production'))
from generate_cloudscape import config, JobError, NoRedirect

REFERENCES = {'cloud': 'cloudscape.webp', 'city': 'city.webp', 'lunar': 'lunar.webp'}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
UPLOAD_TYPES = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}
ACTIVE = ('SUBMITTING', 'PENDING', 'RUNNING', 'DOWNLOADING', 'PREPARING', 'UNCERTAIN')
TERMINAL = ('SUCCEEDED', 'FAILED', 'REJECTED', 'UNCERTAIN', 'UNAVAILABLE')
TIMING_FIELDS = ('submitted_at', 'generation_completed_at', 'download_completed_at', 'ready_at')


class StudioError(Exception):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status, self.code = status, code


class ProviderError(Exception):
    def __init__(self, message, rejected=False):
        super().__init__(message)
        self.rejected = rejected


def provider_message(code):
    code = str(code).lower()
    if any(word in code for word in ('inspection', 'sensitive', 'inappropriate', 'contentfilter')):
        return '这段描述未通过内容审核，请调整后再试'
    if any(word in code for word in ('apikey', 'accessdenied', 'unauthorized')):
        return '视频服务认证失败，请检查服务端密钥与模型权限'
    if any(word in code for word in ('arrear', 'balance', 'quota', 'allocation')):
        return '可用额度不足或账号状态受限，请检查百炼账户'
    if any(word in code for word in ('throttl', 'limit', 'overload')):
        return '模型服务繁忙，请稍后再试'
    return '模型未能完成这次生成，请稍后调整描述再试'


class Bailian:
    def __init__(self):
        self.key, self.host = config()

    def request(self, path, payload=None):
        headers = {'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'}
        data = None
        if payload is not None:
            data = request_bytes(payload)
            headers.update({'Content-Type': 'application/json', 'X-DashScope-Async': 'enable'})
        request = urllib.request.Request('https://' + self.host + path, data=data, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
                body = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(body, dict):
                raise ValueError('Unexpected response')
            return body
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read(65536))
                code = body.get('code', '') if isinstance(body, dict) else ''
            except (ValueError, OSError):
                code = ''
            raise ProviderError(provider_message(code), rejected=error.code in (400, 401, 403, 404, 422, 429)) from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, HTTPException, JobError):
            raise ProviderError('服务连接暂时中断，正在核对原任务，请勿重复提交') from None

    def submit(self, job):
        reference = job.get('reference_preset')
        try:
            image_bytes = (job.get('_reference_bytes') or
                           ((ROOT / 'dist/assets' / REFERENCES[reference]).read_bytes() if reference else None))
            body = frozen_payload(job, image_bytes)
        except (OSError, ValueError, KeyError, TypeError):
            raise ProviderError('生成记录或参考图不匹配，本次尚未提交，请联系邀请人核对', rejected=True) from None
        return self.request(API_PATH, body)

    def query(self, task_id):
        return self.request('/api/v1/tasks/' + task_id)

    def download(self, url, destination):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or not (
            (parsed.hostname or '').endswith('.aliyuncs.com') or (parsed.hostname or '').endswith('.alicdn.com')
        ):
            raise ProviderError('视频下载地址异常，已保留原任务供核对')
        fd, temp = tempfile.mkstemp(dir=destination.parent, suffix='.part')
        try:
            total = 0
            # No authorization header is sent to the result host. No redirects followed.
            with os.fdopen(fd, 'wb') as handle, urllib.request.build_opener(NoRedirect()).open(url, timeout=60) as response:
                declared = response.headers.get('Content-Length')
                if declared is not None and (not declared.isdigit() or not 32 <= int(declared) <= 100 * 1024 * 1024):
                    raise ProviderError('视频文件长度异常，原任务已保留供核对')
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise ProviderError('视频文件超过本地保存上限，原任务已保留')
                    handle.write(chunk)
            if declared is not None and total != int(declared):
                raise ProviderError('视频下载尚未完整，正在重新获取原结果')
            validate_container(temp)
            os.replace(temp, destination)
        except (urllib.error.URLError, OSError, HTTPException, JobError, MediaValidationError):
            raise ProviderError('视频已生成，正在重新获取原结果') from None
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


from media import prepare_media, prepare_stream, stream_type, validate_media, validate_container, MediaValidationError, encoder


class Studio:
    def __init__(self, data_dir, provider=None, daily_limit_cents=3000, poll_seconds=15, invite_only=False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.media_dir = self.data_dir / 'videos'
        self.media_dir.mkdir(exist_ok=True, mode=0o700)
        self.playback_dir = self.data_dir / 'playback'
        self.playback_dir.mkdir(exist_ok=True, mode=0o700)
        self.stream_dir = self.data_dir / 'streams'
        self.stream_dir.mkdir(exist_ok=True, mode=0o700)
        self.reference_dir = self.data_dir / 'references'
        self.reference_dir.mkdir(exist_ok=True, mode=0o700)
        self.db_path = self.data_dir / 'studio.sqlite3'
        self.provider = provider
        self.daily_limit = daily_limit_cents
        self.poll_seconds = poll_seconds
        self.invite_only = invite_only
        self.stop = threading.Event()
        self.wake = threading.Event()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                prompt TEXT NOT NULL, aspect TEXT NOT NULL, duration INTEGER NOT NULL,
                cost_cents INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                status TEXT NOT NULL, task_id TEXT, error TEXT NOT NULL DEFAULT '',
                UNIQUE(owner, request_id))''')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_job_per_request ON jobs(request_id)')
            if 'title' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'favorite' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0')
            if 'deleted_at' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN deleted_at REAL')
            if 'model' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN model TEXT NOT NULL DEFAULT 'wan2.6-t2v'")
            if 'reference_preset' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN reference_preset TEXT')
            if 'reference_id' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN reference_id TEXT')
            for field in TIMING_FIELDS:
                if field not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {field} REAL')
            for field, declaration in (('recovery_attempts', 'INTEGER NOT NULL DEFAULT 0'), ('next_check_at', 'REAL'), ('last_checked_at', 'REAL')):
                if field not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {field} {declaration}')
            for field, declaration in (('request_spec', 'TEXT'), ('request_fingerprint', 'TEXT'),
                                       ('request_recorded_at', 'REAL'), ('request_started_at', 'REAL')):
                if field not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {field} {declaration}')
            db.execute('''CREATE TRIGGER IF NOT EXISTS frozen_generation_record
                BEFORE UPDATE OF request_spec,request_fingerprint,request_recorded_at ON jobs
                WHEN OLD.request_spec IS NOT NULL AND
                  (NEW.request_spec IS NOT OLD.request_spec OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
                   OR NEW.request_recorded_at IS NOT OLD.request_recorded_at)
                BEGIN SELECT RAISE(ABORT,'generation record is immutable'); END''')
            recovery_schema(db)
            db.execute('''CREATE TABLE IF NOT EXISTS reference_images (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL, aspect TEXT NOT NULL,
                size INTEGER NOT NULL, digest TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(owner,digest))''')
            db.execute("UPDATE jobs SET status='UNCERTAIN', error=? WHERE status='SUBMITTING' AND task_id IS NULL",
                       ('提交时服务重启，结果需要人工核对；不会自动重新提交',))
        os.chmod(self.db_path, 0o600)
        self.access = Access(self.db_path)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_jobs(self, owner, deleted=False):
        with self.db() as db:
            condition = 'IS NOT NULL' if deleted else 'IS NULL'
            return [self.public(dict(row)) for row in db.execute('SELECT * FROM jobs WHERE owner=? AND deleted_at ' + condition + ' ORDER BY created_at DESC', (owner,))]

    def get(self, job_id, owner=None):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or (owner is not None and row['owner'] != owner):
            raise StudioError('未找到这条作品', 404, 'not_found')
        return dict(row)

    def public(self, job):
        uploaded = self.get_reference(job['reference_id'], job['owner']) if job.get('reference_id') else None
        available = job['status'] == 'SUCCEEDED' and job['deleted_at'] is None
        original = self.media_dir / (job['id'] + '.mp4')
        playback = self.playback_dir / original.name
        stream = self.stream_dir / original.name
        poster = self.playback_dir / (job['id'] + '.jpg')
        base = '/api/generations/' + job['id']
        return {**{key: job[key] for key in ('id', 'request_id', 'title', 'prompt', 'aspect', 'duration', 'status', 'created_at', 'error')},
                'favorite': bool(job['favorite']), 'deleted_at': job['deleted_at'],
                'model': job['model'], 'reference_preset': job['reference_preset'],
                'reference_id': job.get('reference_id'),
                'reference_name': uploaded['name'] if uploaded else None,
                'reference_image': uploaded['image_url'] if uploaded else '/assets/' + REFERENCES[job['reference_preset']] if job['reference_preset'] in REFERENCES else None,
                'resolution': '720P', 'estimated_cost_cny': f"{job['cost_cents']/100:.2f}",
                # Local observations, not the provider's exact generation timestamps.
                # Historical jobs stay null rather than guessing from updated_at.
                'timings': {field: job.get(field) for field in TIMING_FIELDS},
                'generation_record': public_record(job),
                'recovery': {'can_check': job['status'] == 'UNCERTAIN' and bool(job.get('task_id')),
                             'needs_operator': job['status'] == 'UNCERTAIN' and (not job.get('task_id') or job.get('next_check_at') is None),
                             'last_checked_at': job.get('last_checked_at')},
                'video_url': base + '/video' if available else None,
                'playback_url': base + ('/preview' if playback.is_file() else '/video') if available else None,
                'poster_url': base + '/poster' if available and poster.is_file() else None,
                'playback_optimized': available and playback.is_file(),
                'stream_url': base + '/stream' if available and stream.is_file() else None,
                'stream_type': stream_type(str(stream), stream.stat().st_mtime_ns) if available and stream.is_file() else None,
                'stream_size_bytes': stream.stat().st_size if available and stream.is_file() else None,
                'original_size_bytes': original.stat().st_size if available and original.is_file() else None,
                'playback_size_bytes': playback.stat().st_size if available and playback.is_file() else None}

    def get_reference(self, reference_id, owner):
        if not isinstance(reference_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', reference_id):
            raise StudioError('参考图无效，请重新上传', 400, 'invalid_reference')
        with self.db() as db:
            row = db.execute('SELECT * FROM reference_images WHERE id=? AND owner=?', (reference_id, owner)).fetchone()
        if row is None:
            raise StudioError('未找到这张参考图，请重新上传', 404, 'reference_not_found')
        return {key: row[key] for key in ('id', 'name', 'width', 'height', 'aspect', 'size')} | {'image_url': '/api/references/' + reference_id + '/image'}

    def upload_reference(self, owner, content, content_type, name):
        if content_type not in UPLOAD_TYPES:
            raise StudioError('请选择 JPG、PNG 或 WebP 图片', 415, 'image_type')
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise StudioError('图片请控制在 10 MB 以内', 413, 'image_size')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as source:
                    if source.format != UPLOAD_TYPES[content_type] or getattr(source, 'n_frames', 1) != 1:
                        raise StudioError('请选择单张静态的 JPG、PNG 或 WebP 图片', 400, 'image_type')
                    width, height = source.size
                    if not 240 <= min(width, height) or max(width, height) > 8000 or width * height > MAX_IMAGE_PIXELS:
                        raise StudioError('图片短边至少 240 像素，最长边不超过 8000 像素，总像素不超过 2400 万', 400, 'image_dimensions')
                    if not .25 <= width / height <= 4:
                        raise StudioError('图片过于狭长，请使用长宽比 1:4 到 4:1 之间的图片', 400, 'image_ratio')
                    source.verify()
                with Image.open(io.BytesIO(content)) as source:
                    image = ImageOps.exif_transpose(source).convert('RGBA')
                    image.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
                    clean = Image.new('RGB', image.size, (11, 16, 37))
                    clean.paste(image, mask=image.getchannel('A'))
                    output = io.BytesIO()
                    clean.save(output, format='JPEG', quality=92, optimize=True)
                    normalized = output.getvalue()
                    width, height = clean.size
        except StudioError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise StudioError('这张图片无法读取，请换一张完整的图片', 400, 'invalid_image') from None
        aspect = next((key for key, ratio in (('16:9', 16/9), ('9:16', 9/16), ('1:1', 1)) if abs(width/height-ratio) < .005), None)
        divisor = math.gcd(width, height)
        aspect = aspect or f'{width//divisor}:{height//divisor}'
        name = re.sub(r'[\x00-\x1f\x7f]', '', str(name or '我的参考图')).replace('\\', '/').rsplit('/', 1)[-1][:80] or '我的参考图'
        digest = hashlib.sha256(normalized).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM reference_images WHERE owner=? AND digest=?', (owner, digest)).fetchone()
            if existing:
                reference_id = existing['id']
            else:
                total = db.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM reference_images WHERE owner=?', (owner,)).fetchone()
                if total[0] >= 100 or total[1] + len(normalized) > 200 * 1024 * 1024:
                    raise StudioError('当前工作室的参考图存储已满，请先使用已有参考图', 429, 'reference_limit')
                reference_id = str(uuid.uuid4())
                target = self.reference_dir / (reference_id + '.jpg')
                try:
                    with target.open('xb') as handle:
                        os.chmod(target, 0o600)
                        handle.write(normalized)
                    db.execute('INSERT INTO reference_images VALUES(?,?,?,?,?,?,?,?,?)', (reference_id, owner, name, width, height, aspect, len(normalized), digest, time.time()))
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
        return self.get_reference(reference_id, owner)

    def create(self, owner, payload):
        if not isinstance(payload, dict):
            raise StudioError('提交内容格式不正确')
        prompt = payload.get('prompt')
        aspect, duration = payload.get('aspect'), payload.get('duration')
        reference = payload.get('reference_preset')
        reference_id = payload.get('reference_id')
        request_id = payload.get('request_id', '')
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 1200:
            raise StudioError('请输入 1 到 1200 字的画面描述')
        original_prompt = prompt
        prompt = prompt.strip()
        if not isinstance(aspect, str) or (not reference_id and aspect not in SIZES) or type(duration) is not int or not 2 <= duration <= 15:
            raise StudioError('请选择支持的画幅，并填写 2 到 15 之间的整数秒数')
        if reference is not None and (not isinstance(reference, str) or reference not in REFERENCES):
            raise StudioError('请选择工作室提供的参考首帧')
        if reference is not None and reference_id is not None:
            raise StudioError('每次生成只能使用一张参考首帧')
        uploaded = self.get_reference(reference_id, owner) if reference_id is not None else None
        if uploaded and aspect != uploaded['aspect']:
            raise StudioError('画幅需要跟随参考图，请重新选择图片', 400, 'reference_aspect')
        if reference and aspect != '16:9':
            raise StudioError('当前示例首帧使用 16:9 横屏，请移除首帧后再调整画幅')
        if reference and not (ROOT / 'dist/assets' / REFERENCES[reference]).is_file():
            raise StudioError('参考首帧暂不可用，请移除后再试', 503, 'reference_unavailable')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
            raise StudioError('请求标识无效，请刷新后重试')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['owner'] != owner:
                    raise StudioError('浏览器会话已改变，请先核对原作品；不会重复提交', 403, 'request_session_changed')
                if (old['prompt'], old['aspect'], old['duration'], old['reference_preset'], old['reference_id']) != (prompt, aspect, duration, reference, reference_id):
                    raise StudioError('此请求已提交，请先核对原任务', 409, 'request_conflict')
                return self.public(dict(old))
            if self.invite_only or owner.startswith('account-'):
                quota = self.access.quota(owner, db)
                if duration > quota['remaining_seconds']:
                    raise StudioError(f"内测还剩 {quota['remaining_seconds']} 秒，请缩短时长或联系邀请人", 429, 'personal_limit')
            if self.provider is None:
                raise StudioError('服务端尚未配置百炼密钥', 503, 'not_configured')
            if not encoder():
                raise StudioError('成片校验服务尚未准备好，请联系邀请人', 503, 'media_not_ready')
            if db.execute('SELECT 1 FROM jobs WHERE status IN (' + ','.join('?' for _ in ACTIVE) + ') LIMIT 1', ACTIVE).fetchone():
                raise StudioError('已有视频正在生成或待核对，请等待原任务完成', 409, 'busy')
            china = timezone(timedelta(hours=8))
            day_start = datetime.now(china).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            spent = db.execute("SELECT COALESCE(SUM(cost_cents),0) FROM jobs WHERE created_at>=? AND status NOT IN ('FAILED','REJECTED')", (day_start,)).fetchone()[0]
            cost = duration * (30 if reference or reference_id else 60)
            if spent + cost > self.daily_limit:
                raise StudioError('今天的本地生成预算已达到上限，请明天继续', 429, 'daily_limit')
            now, job_id = time.time(), str(uuid.uuid4())
            try:
                reference_bytes = ((self.reference_dir / (reference_id + '.jpg')).read_bytes() if reference_id else
                                   (ROOT / 'dist/assets' / REFERENCES[reference]).read_bytes() if reference else None)
                spec = generation_snapshot({'prompt': prompt, 'aspect': aspect, 'duration': duration,
                                            'reference_id': reference_id, 'reference_preset': reference},
                                           original_prompt, reference_bytes)
                fingerprint = request_digest(request_bytes(generation_payload(spec, reference_bytes)))
            except (OSError, ValueError, KeyError):
                raise StudioError('参考图或生成设置暂不可读取，本次尚未提交', 503, 'record_unavailable') from None
            db.execute('''INSERT INTO jobs(id,owner,request_id,prompt,aspect,duration,cost_cents,created_at,updated_at,status,model,reference_preset,reference_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (job_id, owner, request_id, prompt, aspect, duration, cost, now, now, 'SUBMITTING', IMAGE_MODEL if reference or reference_id else MODEL, reference, reference_id))
            db.execute('UPDATE jobs SET request_spec=?,request_fingerprint=?,request_recorded_at=? WHERE id=?',
                       (request_bytes(spec).decode(), fingerprint, now, job_id))
        self.wake.set()
        return self.public(self.get(job_id))

    def update(self, job_id, **values):
        values['updated_at'] = time.time()
        with self.db() as db:
            db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))

    def rename(self, job_id, owner, title):
        if self.get(job_id, owner)['deleted_at'] is not None:
            raise StudioError('请先从最近删除中恢复这条作品', 409, 'trashed')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60 or any(ord(char) < 32 for char in title):
            raise StudioError('请输入 1 到 60 字的作品名称')
        self.update(job_id, title=title.strip())
        return self.public(self.get(job_id, owner))

    def manage(self, job_id, owner, action, payload):
        if not isinstance(payload, dict):
            raise StudioError('提交内容格式不正确')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone()
            if row is None:
                raise StudioError('未找到这条作品', 404, 'not_found')
            if action == 'favorite':
                if type(payload.get('favorite')) is not bool:
                    raise StudioError('收藏状态必须为布尔值')
                if row['deleted_at'] is not None:
                    raise StudioError('请先恢复这条作品，再进行收藏', 409, 'trashed')
                db.execute('UPDATE jobs SET favorite=?, updated_at=? WHERE id=?', (int(payload['favorite']), time.time(), job_id))
            elif action == 'trash':
                if row['status'] not in TERMINAL or row['status'] == 'UNCERTAIN':
                    raise StudioError('请等这条任务结束后再删除，生成不会因此取消', 409, 'active_job')
                db.execute('UPDATE jobs SET deleted_at=COALESCE(deleted_at,?), updated_at=? WHERE id=?', (time.time(), time.time(), job_id))
            elif action == 'restore':
                db.execute('UPDATE jobs SET deleted_at=NULL, updated_at=? WHERE id=?', (time.time(), job_id))
            else:
                raise StudioError('不支持的作品操作')
        return self.public(self.get(job_id, owner))

    def check_task(self, job_id, owner):
        try:
            with job_lock(self.data_dir, job_id):
                job = self.get(job_id, owner)
                if job['status'] == 'UNCERTAIN':
                    if not job['task_id']:
                        raise StudioError('请将作品编号交给邀请人，在百炼控制台核对原任务；不会重复生成', 409, 'operator_required')
                    if job.get('last_checked_at') and time.time() - job['last_checked_at'] < self.poll_seconds:
                        raise StudioError('刚刚查询过原任务，请稍等片刻再查', 429, 'check_cooldown')
                    self.update(job_id, next_check_at=0, error='正在重新查询原任务，不会重新生成')
                self.wake.set()
                return self.public(self.get(job_id, owner))
        except BlockingIOError:
            raise StudioError('正在查询原任务，请稍等片刻', 409, 'checking') from None

    def recovery_needed(self, job_id, message):
        job = self.get(job_id)
        attempts = job['recovery_attempts'] + 1
        retry = bool(job['task_id']) and attempts < 3
        self.update(job_id, status='UNCERTAIN', error=message if retry else '原任务结果需要邀请人核对，记录已保留；不会重新生成', recovery_attempts=attempts,
                    next_check_at=time.time() + self.poll_seconds if retry else None)

    def process(self, job_id):
        try:
            with job_lock(self.data_dir, job_id):
                self._process(job_id)
        except BlockingIOError:
            return  # Another local worker/operator is already handling this exact task.

    def _process(self, job_id):
        job = self.get(job_id)
        if job['status'] == 'UNCERTAIN' and (not job['task_id'] or job['next_check_at'] is None or job['next_check_at'] > time.time()):
            return
        original = self.media_dir / (job_id + '.mp4')
        if job['status'] in ('DOWNLOADING', 'PREPARING') and original.is_file():
            self.finish_media(job, original)
            return
        if not self.provider:
            return
        if job['status'] == 'SUBMITTING':
            try:
                try:
                    if job.get('reference_id'):
                        job['_reference_bytes'] = (self.reference_dir / (job['reference_id'] + '.jpg')).read_bytes()
                    elif job.get('reference_preset'):
                        job['_reference_bytes'] = (ROOT / 'dist/assets' / REFERENCES[job['reference_preset']]).read_bytes()
                    if not job.get('request_spec'):
                        raise ValueError('Missing frozen request')
                    frozen_payload(job, job.get('_reference_bytes'))
                except (OSError, ValueError, KeyError, TypeError):
                    raise ProviderError('生成记录或参考图不匹配，本次尚未提交，请联系邀请人核对', rejected=True) from None
                self.update(job_id, request_started_at=time.time())
                body = self.provider.submit(job)
                output = body.get('output', {})
                task_id = output.get('task_id')
                if not isinstance(task_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{10,100}', task_id):
                    if body.get('code'):
                        raise ProviderError(provider_message(body['code']), rejected=True)
                    raise ProviderError('未取得任务编号，需要人工核对，请勿重复提交')
                self.update(job_id, status='PENDING', task_id=task_id, error='', submitted_at=time.time())
            except ProviderError as error:
                self.update(job_id, status='REJECTED' if error.rejected else 'UNCERTAIN', error=str(error))
            return
        if not job['task_id'] or (job['status'] in TERMINAL and job['status'] != 'UNCERTAIN'):
            return
        self.update(job_id, last_checked_at=time.time())
        try:
            body = self.provider.query(job['task_id'])
            output = body.get('output', {})
            state = output.get('task_status')
            if state in ('PENDING', 'RUNNING'):
                self.update(job_id, status=state, error='', recovery_attempts=0, next_check_at=None)
            elif state == 'SUCCEEDED':
                self.update(job_id, status='DOWNLOADING', error='',
                            generation_completed_at=job.get('generation_completed_at') or time.time())
                self.provider.download(output.get('video_url', ''), original)
                self.update(job_id, status='PREPARING', download_completed_at=time.time())
                self.finish_media(self.get(job_id), original)
            elif state in ('FAILED', 'CANCELED'):
                self.update(job_id, status='FAILED', error=provider_message(output.get('code', '')), next_check_at=None)
            elif state == 'UNKNOWN':
                self.recovery_needed(job_id, '原任务状态暂不可确认，已保留记录供核对；不会自动重新生成')
            else:
                raise ProviderError('正在重新查询原任务，请勿重复提交')
        except ProviderError as error:
            self.recovery_needed(job_id, str(error))

    def finish_media(self, job, original):
        self.update(job['id'], status='PREPARING', error='',
                    download_completed_at=job.get('download_completed_at') or time.time())
        try:
            size = tuple(map(int, SIZES[job['aspect']].split('*'))) if job['model'] == MODEL else None
            validate_media(original, job['duration'], size)
        except (MediaValidationError, OSError):
            # Retain the downloaded bytes privately for diagnosis, never expose a bad film.
            quarantine = self.data_dir / 'quarantine'
            quarantine.mkdir(exist_ok=True, mode=0o700)
            if original.is_file():
                os.replace(original, quarantine / (job['id'] + '.mp4'))
            self.recovery_needed(job['id'], '成片校验暂未通过，正在核对原结果；不会重新生成')
            return
        try:
            prepare_media(original, self.playback_dir)
        except OSError:
            pass  # The saved original remains playable; never re-submit the model task.
        try:
            prepare_stream(self.playback_dir / original.name if (self.playback_dir / original.name).is_file() else original, self.stream_dir)
        except OSError:
            pass  # Incremental delivery is optional; the validated original is retained.
        self.update(job['id'], status='SUCCEEDED', error='', ready_at=time.time(), next_check_at=None, recovery_attempts=0)

    def run_worker(self):
        while not self.stop.is_set():
            self.wake.clear()
            with self.db() as db:
                rows = db.execute("SELECT id FROM jobs WHERE status IN ('SUBMITTING','PENDING','RUNNING','DOWNLOADING','PREPARING') OR (status='UNCERTAIN' AND task_id IS NOT NULL AND next_check_at<=?)", (time.time(),)).fetchall()
            for row in rows:
                if self.stop.is_set():
                    break
                try:
                    self.process(row['id'])
                except Exception:
                    # Never log raw exceptions: they may contain a signed URL or user prompt.
                    job = self.get(row['id'])
                    self.update(row['id'], status='UNCERTAIN' if job['status'] == 'SUBMITTING' else job['status'], error='服务暂时中断，原任务已保留，请勿重复提交')
            self.wake.wait(self.poll_seconds)


class StudioRequest:
    """Shared routes for the loopback development server and production WSGI."""
    session_cookie = 'prelude_session'
    secure_session = False

    def expected_origin(self):
        host = self.headers.get('Host', '')
        allowed = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        if host not in allowed:
            raise StudioError('无效的访问地址', 403, 'host_forbidden')
        return 'http://' + host

    def log_message(self, *_):
        pass  # No prompts, cookies, signed URLs, credentials, or request bodies in logs.

    def guard(self, mutation=False):
        expected = self.expected_origin()
        origin = self.headers.get('Origin')
        if origin and origin != expected:
            raise StudioError('请从工作室页面访问', 403, 'origin_forbidden')
        if mutation and origin != expected:
            raise StudioError('请从工作室页面提交', 403, 'origin_required')
        public_navigation = (self.command in ('GET', 'HEAD') and
                             self.headers.get('Sec-Fetch-Mode') == 'navigate' and
                             self.headers.get('Sec-Fetch-Dest') == 'document' and
                             not urllib.parse.urlsplit(self.path).path.startswith('/api/'))
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not public_navigation:
            raise StudioError('不支持跨站访问', 403, 'cross_site')

    def session(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except cookies.CookieError:
            pass
        token = jar.get(self.session_cookie)
        token = token.value if token else ''
        self.new_cookie = None
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            token = secrets.token_urlsafe(32)
            self.rotate_session(token)
        self.token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.account = self.server.studio.access.account_for(self.token_hash)
        self.owner = self.account['id'] if self.account else self.token_hash
        self.csrf = hashlib.sha256((token + ':csrf').encode()).hexdigest()

    def rotate_session(self, token):
        self.new_cookie = self.session_cookie + '=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000'
        if self.secure_session:
            self.new_cookie += '; Secure'

    def access_state(self):
        return {'invite_only': self.server.studio.invite_only, 'signed_in': self.account is not None,
                'account_id': self.account['id'] if self.account else None,
                'quota': self.server.studio.access.quota(self.account['id']) if self.account else None}

    def common_headers(self, cache_control='no-store', set_cookie=True):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Cache-Control', cache_control)
        if set_cookie and getattr(self, 'new_cookie', None):
            self.send_header('Set-Cookie', self.new_cookie)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.common_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def file_response(self, path, download=False, cache_control='private, no-cache', download_name=None):
        stat = path.stat()
        size = stat.st_size
        etag = f'"{stat.st_mtime_ns:x}-{size:x}"'
        public = cache_control.startswith('public')
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.common_headers(cache_control, set_cookie=not public)
            self.send_header('ETag', etag)
            self.end_headers()
            return
        start, end, partial = 0, size - 1, False
        range_header = self.headers.get('Range')
        if self.headers.get('If-Range') and self.headers['If-Range'] != etag:
            range_header = None
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or not any(match.groups()):
                raise StudioError('无效的视频范围', 416, 'invalid_range')
            left, right = match.groups()
            if left:
                start, end = int(left), min(int(right), size - 1) if right else size - 1
            else:
                start = max(0, size - int(right))
            if start > end or start >= size:
                self.send_response(416)
                self.common_headers()
                self.send_header('Content-Range', 'bytes */' + str(size))
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            partial = True
        self.send_response(206 if partial else 200)
        self.common_headers(cache_control, set_cookie=not public)
        self.send_header('ETag', etag)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + (download_name or 'prelude-' + path.name) + '"')
        self.end_headers()
        if self.command != 'HEAD':
            self.stream_file(path, start, end - start + 1)

    def dispatch(self):
        self.guard(self.command == 'POST')
        self.session()
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        studio = self.server.studio
        if self.command in ('GET', 'HEAD'):
            if path == '/api/bootstrap':
                return self.json_response({'csrf': self.csrf, 'ready': studio.provider is not None and bool(encoder()), 'model': MODEL,
                    'reference_supported': True, 'upload_supported': True, 'upload_max_bytes': MAX_UPLOAD_BYTES, 'reference_model': IMAGE_MODEL,
                    'price_per_second_cny': '0.60', 'daily_limit_cny': f'{studio.daily_limit/100:.2f}', 'poll_seconds': studio.poll_seconds,
                    'access': self.access_state(),
                    'jobs': studio.list_jobs(self.owner), 'deleted_jobs': studio.list_jobs(self.owner, deleted=True)})
            if path == '/api/generations':
                return self.json_response({'jobs': studio.list_jobs(self.owner, deleted=urllib.parse.parse_qs(parsed.query).get('deleted') == ['1'])})
            reference_match = re.fullmatch(r'/api/references/([a-f0-9-]{36})(/image)?', path)
            if reference_match:
                reference = studio.get_reference(reference_match[1], self.owner)
                if reference_match[2]:
                    image = studio.reference_dir / (reference['id'] + '.jpg')
                    if not image.is_file():
                        raise StudioError('参考图暂不可读取，请重新上传', 404, 'reference_not_found')
                    return self.file_response(image)
                return self.json_response(reference)
            match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})(/(?:video|preview|poster|stream))?', path)
            if match:
                job = studio.get(match[1], self.owner)
                if not match[2]:
                    return self.json_response(studio.public(job))
                suffix = match[2]
                media = (studio.media_dir if suffix == '/video' else studio.stream_dir if suffix == '/stream' else studio.playback_dir) / (job['id'] + ('.jpg' if suffix == '/poster' else '.mp4'))
                if job['deleted_at'] is not None or job['status'] != 'SUCCEEDED' or not media.is_file():
                    raise StudioError('视频还没有准备好', 404, 'not_ready')
                return self.file_response(media, download='download' in urllib.parse.parse_qs(parsed.query),
                    download_name=('prelude-flow-' if suffix == '/preview' else 'prelude-original-') + job['id'] + '.mp4' if suffix in ('/preview','/video') else None)
            if path.startswith('/api/'):
                raise StudioError('接口不存在', 404, 'not_found')
            if path == '/':
                path = '/index.html'
            target = (ROOT / 'dist' / path.lstrip('/')).resolve()
            public_root = (ROOT / 'dist').resolve()
            if public_root not in target.parents or any(part.startswith('.') for part in Path(path).parts) or not target.is_file():
                raise StudioError('页面不存在', 404, 'not_found')
            return self.file_response(target, cache_control='public, max-age=86400' if path.startswith('/assets/') else 'no-store')
        rename_match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})/rename', path)
        manage_match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})/(favorite|trash|restore)', path)
        check_match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})/check', path)
        auth_action = path.removeprefix('/api/access/') if path in ('/api/access/activate', '/api/access/login', '/api/access/logout') else None
        if self.command == 'POST' and (path in ('/api/generations', '/api/references') or rename_match or manage_match or check_match or auth_action):
            if not secrets.compare_digest(self.headers.get('X-Prelude-CSRF', ''), self.csrf):
                raise StudioError('页面会话已变化，请刷新后核对任务', 403, 'csrf')
            if not auth_action and studio.invite_only and self.account is None:
                raise StudioError('请先使用内测通行证登录，描述会为你保留', 401, 'login_required')
            if path == '/api/references':
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise StudioError('无法读取图片大小', 400, 'image_size') from None
                if not 0 < size <= MAX_UPLOAD_BYTES:
                    raise StudioError('图片请控制在 10 MB 以内', 413, 'image_size')
                self.prepare_read()
                content = self.rfile.read(size)
                if len(content) != size:
                    raise StudioError('图片上传中断，请重新选择', 400, 'incomplete_upload')
                content_type = self.headers.get('Content-Type', '').split(';')[0].strip().lower()
                name = urllib.parse.unquote(self.headers.get('X-Prelude-File-Name', '我的参考图'))
                return self.json_response(studio.upload_reference(self.owner, content, content_type, name), 201)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise StudioError('请使用 JSON 提交', 415, 'content_type')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    raise StudioError('提交内容过大或为空', 413, 'body_size')
                self.prepare_read()
                payload = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                raise StudioError('提交内容无法读取') from None
            if check_match:
                if not isinstance(payload, dict):
                    raise StudioError('提交内容格式不正确')
                return self.json_response(studio.check_task(check_match[1], self.owner))
            if auth_action:
                if auth_action == 'logout':
                    studio.access.logout(self.token_hash)
                    self.rotate_session(secrets.token_urlsafe(32))
                else:
                    if self.account is not None:
                        raise StudioError('请先退出当前通行证', 409, 'already_signed_in')
                    token, _ = studio.access.authenticate(auth_action, payload, self.owner, self.token_hash, self.client_address[0])
                    self.rotate_session(token)
                return self.json_response({'ok': True})
            if rename_match:
                if not isinstance(payload, dict):
                    raise StudioError('提交内容格式不正确')
                return self.json_response(studio.rename(rename_match[1], self.owner, payload.get('title')))
            if manage_match:
                return self.json_response(studio.manage(manage_match[1], self.owner, manage_match[2], payload))
            return self.json_response(studio.create(self.owner, payload), 202)
        raise StudioError('不支持的请求', 405, 'method')

    def handle_request(self):
        try:
            self.dispatch()
        except (StudioError, AccessError) as error:
            self.json_response({'error': str(error), 'code': error.code}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.json_response({'error': '服务暂时不可用，原任务已保留，请刷新后核对', 'code': 'server_error'}, 500)


class Handler(StudioRequest, BaseHTTPRequestHandler):
    server_version = 'Prelude'

    def prepare_read(self):
        self.connection.settimeout(30)

    def stream_file(self, path, start, length):
        with path.open('rb') as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                block = handle.read(min(256 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    do_GET = do_HEAD = do_POST = StudioRequest.handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4173)
    args = parser.parse_args()
    os.umask(0o077)
    runtime = ROOT / '.studio'
    runtime.mkdir(exist_ok=True, mode=0o700)
    process_lock = (runtime / '.server.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Prelude Studio is already running. Open http://127.0.0.1:4173 instead.') from None
    try:
        provider = Bailian()
    except JobError:
        provider = None
    limit = int(os.environ.get('PRELUDE_DAILY_LIMIT_CENTS', '3000'))
    if not 300 <= limit <= 30000:
        raise SystemExit('PRELUDE_DAILY_LIMIT_CENTS must be between 300 and 30000.')
    invitation_mode = os.environ.get('PRELUDE_INVITE_ONLY', '1')
    if invitation_mode not in ('0', '1'):
        raise SystemExit('PRELUDE_INVITE_ONLY must be 0 or 1.')
    studio = Studio(runtime, provider, daily_limit_cents=limit, invite_only=invitation_mode == '1')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.studio = studio
    worker = threading.Thread(target=studio.run_worker, daemon=True)
    worker.start()
    print(f'Prelude Studio: http://127.0.0.1:{server.server_port}', flush=True)
    print('Bailian configured: ' + str(provider is not None), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        studio.stop.set()
        studio.wake.set()
        server.server_close()


if __name__ == '__main__':
    main()
