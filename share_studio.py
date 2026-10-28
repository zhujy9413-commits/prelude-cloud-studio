#!/usr/bin/env python3
"""Start/stop a temporary Cloudflare homepage; never submits a generation."""
import argparse
from contextlib import closing
import fcntl
import http.client
import json
import os
from pathlib import Path
import queue
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from server import ROOT, config, JobError
from share_runtime import validate_share_origin

RUNTIME = ROOT / '.studio'


def origin_from_line(line):
    match = re.search(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[\s"/\\]|$)', line)
    return validate_share_origin(match[0]) if match else None


def save_state(runtime, data):
    fd, temporary = tempfile.mkstemp(dir=runtime, prefix='.share-')
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
        os.replace(temporary, runtime / 'share-session.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(runtime, filename='share-session.json'):
    try:
        data = json.loads((runtime / filename).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def request_stop(runtime):
    state = load_state(runtime)
    session = state.get('session', '')
    if state.get('status') not in ('starting', 'running') or not re.fullmatch(r'[a-f0-9]{32}', session):
        return False
    marker = runtime / ('share-stop-' + session)
    with marker.open('w') as output:
        os.chmod(marker, 0o600)
        output.write('stop\n')
    return True


def port_is_free(port):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def terminate(process, timeout=8):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_share():
    os.umask(0o077)
    proof = load_state(RUNTIME, 'share-validation.json')
    checked_at = proof.get('checked_at')
    if (proof.get('real_bailian_calls') != 0 or proof.get('production_data_changed') is not False or
            not all(proof.get(key) is True for key in ('public_https_homepage','guest_generation_blocked',
                'activation_and_upload','fake_generation_and_video_range','account_isolation_and_logout')) or
            not isinstance(checked_at, (float, int)) or isinstance(checked_at, bool) or
            not 0 <= time.time() - checked_at <= 86400):
        raise ValueError('尚未完成独立模拟数据的网络验证，正式分享不会开启；请先完成外部连接检查')
    if not (RUNTIME / 'studio.sqlite3').is_file():
        raise ValueError('请先启动一次本机工作室，建立工作室数据')
    with closing(sqlite3.connect((RUNTIME / 'studio.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute("SELECT count(*) FROM jobs WHERE status IN ('SUBMITTING','PENDING','RUNNING','DOWNLOADING','UNCERTAIN')").fetchone()[0]:
            raise ValueError('请先等待或核对当前生成任务，再切换分享入口')
    binary = ROOT / '.tools/cloudflared'
    if not binary.is_file():
        raise ValueError('请先安装并校验项目 .tools/cloudflared 连接工具')
    if not port_is_free(4181):
        raise ValueError('4181 端口已被占用，请先核对原分享进程')
    key, host = config()
    process_lock = (RUNTIME / '.share.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        state = load_state(RUNTIME)
        if state.get('status') == 'running':
            print('当前分享首页：' + validate_share_origin(state.get('origin', '')) + '/', flush=True)
            return
        raise ValueError('已有分享管理进程正在运行，请先查看其窗口') from None
    tunnel = backend = awake = None
    session = secrets.token_hex(16)
    marker = RUNTIME / ('share-stop-' + session)
    state = {'session': session, 'status': 'starting', 'started_at': time.time()}
    save_state(RUNTIME, state)
    log_path = RUNTIME / 'share-cloudflared.log'
    origins = queue.Queue()

    def record_output():
        with log_path.open('w') as log:
            os.chmod(log_path, 0o600)
            for line in tunnel.stdout:
                log.write(line)
                log.flush()
                origin = origin_from_line(line)
                if origin:
                    origins.put(origin)

    try:
        connector_env = {key: value for key, value in os.environ.items()
                         if not key.startswith(('TUNNEL_', 'DASHSCOPE_'))}
        tunnel = subprocess.Popen([str(binary), 'tunnel', '--config', os.devnull, '--no-autoupdate',
            '--url', 'http://127.0.0.1:4181', '--protocol', 'quic', '--output', 'json',
            '--metrics', '127.0.0.1:0', '--management-diagnostics=false'], env=connector_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        reader = threading.Thread(target=record_output, daemon=True)
        reader.start()
        print('正在申请免费临时地址；连接日志仅保存在本机私有目录', flush=True)
        deadline = time.monotonic() + 50
        origin = None
        while time.monotonic() < deadline and tunnel.poll() is None and not marker.exists():
            try:
                origin = origins.get(timeout=.5)
                break
            except queue.Empty:
                continue
        if not origin:
            raise ValueError('临时地址尚未建立，请核对本机网络及私有连接日志；未向公网提供工作室')
        state['origin'] = origin
        save_state(RUNTIME, state)
        print('临时地址已分配；正在准备邀请工作室', flush=True)
        if not port_is_free(4173):
            print('等待原本机服务退出；不会自行停止占用 4173 端口的程序', flush=True)
        deadline = time.monotonic() + 300
        while not port_is_free(4173):
            if marker.exists() or tunnel.poll() is not None or time.monotonic() >= deadline:
                raise ValueError('本机原服务尚未退出，分享入口未启用')
            time.sleep(.5)
        env = {**os.environ, 'DASHSCOPE_API_KEY': key, 'DASHSCOPE_API_HOST': host,
               'PRELUDE_PUBLIC_ORIGIN': origin, 'PRELUDE_DATA_DIR': str(RUNTIME),
               'PRELUDE_INVITE_ONLY': '1', 'PRELUDE_LOCAL_PORT': '4173', 'PRELUDE_TUNNEL_PORT': '4181',
               'GUNICORN_CMD_ARGS': ''}
        backend_log = (RUNTIME / 'share-gunicorn.log').open('w')
        os.chmod(RUNTIME / 'share-gunicorn.log', 0o600)
        try:
            backend = subprocess.Popen([sys.executable, '-m', 'gunicorn', '-c', str(ROOT / 'deploy/gunicorn-share.conf.py'),
                'share_runtime:create_app()'], cwd=ROOT, env=env, stdout=backend_log, stderr=backend_log)
        finally:
            backend_log.close()
        deadline = time.monotonic() + 20
        ready = False
        while backend.poll() is None and time.monotonic() < deadline:
            connection = http.client.HTTPConnection('127.0.0.1', 4173, timeout=2)
            try:
                connection.request('GET', '/api/health')
                response = connection.getresponse()
                ready = response.status == 200 and json.loads(response.read()).get('ok') is True
            except (OSError, ValueError):
                pass
            finally:
                connection.close()
            if ready:
                break
            time.sleep(.2)
        if not ready:
            raise ValueError('邀请工作室尚未就绪，请核对本机私有运行日志')
        if sys.platform == 'darwin' and Path('/usr/bin/caffeinate').is_file():
            awake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-w', str(tunnel.pid)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state['status'] = 'running'
        save_state(RUNTIME, state)
        print('分享首页：' + origin + '/', flush=True)
        print('本机首页：http://127.0.0.1:4173/', flush=True)
        print('已保留通行证、个人额度和每日总预算；本次启动未生成视频', flush=True)
        while backend.poll() is None and tunnel.poll() is None and not marker.exists():
            time.sleep(.5)
        terminate(tunnel)
        terminate(awake)
        state['status'] = 'closed'
        save_state(RUNTIME, state)
        marker.unlink(missing_ok=True)
        print('临时分享已关闭，本机工作室继续运行；结束此窗口会停止本机服务', flush=True)
        while backend.poll() is None:
            time.sleep(.5)
    finally:
        terminate(tunnel)
        terminate(awake)
        terminate(backend, timeout=115)
        state['status'] = 'closed'
        save_state(RUNTIME, state)
        marker.unlink(missing_ok=True)
        process_lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start', 'stop', 'status'))
    args = parser.parse_args()
    if args.command == 'stop':
        print('已请求关闭临时分享，本机工作室会继续运行' if request_stop(RUNTIME) else '当前没有可关闭的临时分享')
        return
    if args.command == 'status':
        state = load_state(RUNTIME)
        print('分享首页：' + validate_share_origin(state.get('origin', '')) + '/' if state.get('status') == 'running' else '临时分享未开启')
        return
    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    try:
        run_share()
    except KeyboardInterrupt:
        pass
    except (ValueError, JobError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
