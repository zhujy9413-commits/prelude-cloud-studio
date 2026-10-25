#!/usr/bin/env python3
"""Prepare one cloudscape job, submit once, then query/download it separately."""
import argparse
import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'wan2.6-i2v-flash'
PRICE = {'720P': Decimal('0.15'), '1080P': Decimal('0.25')}
STATE = ROOT / 'video-production/.state/cloudscape-task.json'


class JobError(Exception):
    pass


class ApiRejection(JobError):
    def __init__(self, status):
        self.status = status
        super().__init__('API rejected the request with HTTP ' + str(status) + '; response body withheld.')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise JobError('API redirected; stopped without forwarding credentials.')


def config():
    values = {}
    path = ROOT / '.env.local'
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise JobError('.env.local must only be readable by its owner (chmod 600).')
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    key = os.getenv('DASHSCOPE_API_KEY') or values.get('DASHSCOPE_API_KEY', '')
    host = os.getenv('DASHSCOPE_API_HOST') or values.get('DASHSCOPE_API_HOST', '')
    if not key:
        raise JobError('Configure DASHSCOPE_API_KEY locally before calling the API.')
    if not re.fullmatch(r'ws-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com', host):
        raise JobError('Use the Beijing workspace API Host shown in your console.')
    return key, host


def prompt():
    text = (ROOT / 'video-production/cloudscape-video-brief.txt').read_text()
    return text.split('图生视频提示词\n', 1)[1].split('\n\n验收', 1)[0].strip()


def plan(resolution='720P', duration=10):
    image = ROOT / 'video-production/cloudscape-start-frame.png'
    if not image.is_file() or not image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
        raise JobError('The original PNG start frame is missing or invalid.')
    if image.stat().st_size > 10 * 1024 * 1024:
        raise JobError('Start frame exceeds 10 MiB.')
    if resolution not in PRICE or duration not in (5, 10):
        raise JobError('Use 720P/1080P and 5/10 seconds.')
    return {
        'model': MODEL, 'resolution': resolution, 'duration': duration,
        'audio': False, 'video_count': 1,
        'list_price_cny': str(PRICE[resolution] * duration),
        'price_scope': 'Beijing; before any free quota; verify account billing separately',
        'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
        'prompt_sha256': hashlib.sha256(prompt().encode()).hexdigest(),
    }


def api(path, key, host, payload=None):
    headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers.update({'Content-Type': 'application/json', 'X-DashScope-Async': 'enable'})
    request = urllib.request.Request('https://' + host + path, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        # Do not print response bodies: providers can echo the request or credentials.
        if error.code in (400, 401, 403, 404, 422):
            raise ApiRejection(error.code) from None
        raise JobError('API returned HTTP ' + str(error.code) + '; response body withheld.') from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise JobError('API response unavailable. Do not resubmit; check the saved job and console.') from None


def save_state(record):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=STATE.parent, prefix='.task-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, STATE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_state():
    if not STATE.exists():
        raise JobError('No saved task. Prepare and submit a job first.')
    return json.loads(STATE.read_text())


def submit(resolution, duration, max_cost):
    details = plan(resolution, duration)
    if Decimal(details['list_price_cny']) > Decimal(max_cost):
        raise JobError('Estimated list price exceeds the specified budget.')
    key, host = config()
    record = {**details, 'host': host, 'status': 'SUBMITTING',
              'created_at': datetime.now(timezone.utc).isoformat()}
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Reserve the task before POST. A crash or timeout must not cause a second charge.
    try:
        fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise JobError('A task record already exists. Query it; do not submit a duplicate.') from None
    with os.fdopen(fd, 'w') as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    image = ROOT / 'video-production/cloudscape-start-frame.png'
    payload = {
        'model': MODEL,
        'input': {'prompt': prompt(), 'img_url': 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode()},
        'parameters': {'resolution': resolution, 'duration': duration,
                       'audio': False, 'prompt_extend': False, 'watermark': False},
    }
    try:
        response = api('/api/v1/services/aigc/video-generation/video-synthesis', key, host, payload)
        output = response.get('output', {})
        task_id = output.get('task_id')
        if not task_id or not re.fullmatch(r'[a-zA-Z0-9-]{10,100}', task_id):
            raise JobError('No usable task ID returned. Check the console before any new submission.')
        record.update(task_id=task_id, status=output.get('task_status', 'PENDING'))
    except ApiRejection as error:
        record.update(status='SUBMISSION_REJECTED', http_status=error.status)
        save_state(record)
        raise
    except JobError:
        record['status'] = 'SUBMISSION_UNCERTAIN'
        save_state(record)
        raise
    save_state(record)
    return public_state(record)


def public_state(record):
    return {key: record[key] for key in
            ('model', 'resolution', 'duration', 'audio', 'video_count', 'list_price_cny', 'task_id', 'status', 'downloaded_to')
            if key in record}


def query():
    record = read_state()
    if not record.get('task_id'):
        raise JobError('Submission outcome is uncertain. Inspect the console; no automatic retry is allowed.')
    key, host = config()
    if host != record['host']:
        raise JobError('Workspace changed; use the original task workspace.')
    response = api('/api/v1/tasks/' + record['task_id'], key, host)
    output = response.get('output', {})
    status = output.get('task_status')
    if status not in ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'):
        raise JobError('Unrecognized task response. Saved state kept; no new task created.')
    record['status'] = status
    if status == 'SUCCEEDED' and output.get('video_url'):
        record['video_url'] = output['video_url']
        record['usage'] = response.get('usage', {})
    save_state(record)
    return public_state(record)


def download():
    record = read_state()
    if record.get('status') != 'SUCCEEDED' or not record.get('video_url'):
        raise JobError('Task is not ready for download. Query status first.')
    url = record['video_url']
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or not (
        (parsed.hostname or '').endswith('.aliyuncs.com') or (parsed.hostname or '').endswith('.alicdn.com')
    ):
        raise JobError('Unexpected output host; review the result before downloading.')
    output = ROOT / 'video-production/cloudscape-raw.mp4'
    if output.exists():
        raise JobError('Raw video already exists; leaving it untouched.')
    fd, temp = tempfile.mkstemp(dir=output.parent, suffix='.mp4.part')
    try:
        total = 0
        with os.fdopen(fd, 'wb') as handle, urllib.request.urlopen(url, timeout=60) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise JobError('Output exceeds 100 MiB; download stopped.')
                handle.write(chunk)
        with open(temp, 'rb') as handle:
            header = handle.read(32)
        if total < 32 or header[4:8] != b'ftyp':
            raise JobError('Response is not an MP4 file; it was not installed into the site.')
        os.rename(temp, output)
        record['downloaded_to'] = str(output.relative_to(ROOT))
        save_state(record)
        return public_state(record)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JobError('Download unavailable; keep the task ID and retry download only.') from None
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'submit', 'status', 'download'))
    parser.add_argument('--resolution', choices=tuple(PRICE), default='720P')
    parser.add_argument('--duration', type=int, choices=(5, 10), default=10)
    parser.add_argument('--max-cost-cny', default='1.50', help='Per-submission list-price limit; does not query balance')
    args = parser.parse_args()
    try:
        if args.action == 'plan':
            result = plan(args.resolution, args.duration)
            result['key_configured'] = bool(config()[0])
        elif args.action == 'submit':
            result = submit(args.resolution, args.duration, args.max_cost_cny)
        elif args.action == 'status':
            result = query()
        else:
            result = download()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (JobError, KeyError, ValueError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
