"""Private web playback derivatives. Originals and model accounting are untouched."""
import fcntl
import os
import re
import struct
from pathlib import Path
import shutil
import subprocess
import tempfile
from functools import lru_cache


def encoder():
    configured = os.environ.get('PRELUDE_FFMPEG')
    path = configured or str(Path(__file__).resolve().parent / '.tools/ffmpeg')
    return path if Path(path).is_file() and os.access(path, os.X_OK) else shutil.which('ffmpeg')


class MediaValidationError(Exception):
    pass


def validate_container(path):
    """Reject short/truncated atoms before a file can replace a saved result."""
    path = Path(path)
    size, offset, kinds = path.stat().st_size, 0, set()
    with path.open('rb') as stream:
        while offset < size:
            stream.seek(offset)
            header = stream.read(8)
            if len(header) != 8:
                raise MediaValidationError('视频文件不完整，正在重新获取原结果')
            length, kind = struct.unpack('>I4s', header)
            minimum = 8
            if length == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    raise MediaValidationError('视频文件不完整，正在重新获取原结果')
                length, minimum = struct.unpack('>Q', extended)[0], 16
            elif length == 0:
                length = size - offset
            if length < minimum or offset + length > size:
                raise MediaValidationError('视频文件不完整，正在重新获取原结果')
            kinds.add(kind)
            offset += length
    if not {b'ftyp', b'moov', b'mdat'} <= kinds:
        raise MediaValidationError('视频封装不完整，正在重新获取原结果')


def validate_media(path, duration, expected_size=None):
    """Decode the entire local film; never fetch nested network resources."""
    validate_container(path)
    binary = encoder()
    if not binary:
        raise MediaValidationError('成片校验工具暂不可用，请联系邀请人检查服务')
    try:
        result = subprocess.run([binary, '-nostdin', '-hide_banner', '-v', 'info', '-xerror',
            '-err_detect', 'explode', '-protocol_whitelist', 'file,pipe', '-i', str(path),
            '-map', '0:v:0', '-an', '-sn', '-dn', '-progress', 'pipe:1', '-nostats',
            '-threads', '2', '-f', 'null', '-'], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        raise MediaValidationError('成片校验暂未完成，请稍后核对原结果') from None
    frames = re.findall(r'^frame=(\d+)$', result.stdout, re.M)
    times = re.findall(r'^out_time_us=(\d+)$', result.stdout, re.M)
    video = re.search(r'Stream[^\n]*Video: h264[^\n]*?\b(\d{2,5})x(\d{2,5})\b', result.stderr)
    seconds = int(times[-1]) / 1_000_000 if times else 0
    if (result.returncode or not frames or int(frames[-1]) < 1 or not video
            or abs(seconds - duration) > .6
            or (expected_size and tuple(map(int, video.groups())) != tuple(expected_size))):
        raise MediaValidationError('成片完整性校验未通过，正在重新获取原结果')
    if re.search(r'Stream[^\n]*Audio:', result.stderr):
        try:
            audio = subprocess.run([binary, '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
                '-protocol_whitelist', 'file,pipe', '-i', str(path), '-map', '0:a:0', '-vn',
                '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            if audio.returncode:
                raise MediaValidationError('成片音轨校验未通过，正在重新获取原结果')
        except (OSError, subprocess.SubprocessError):
            raise MediaValidationError('成片音轨校验暂未完成，请稍后核对原结果') from None
    return {'frames': int(frames[-1]), 'duration': seconds, 'width': int(video[1]), 'height': int(video[2])}


@lru_cache(maxsize=64)
def stream_type(path, modified):
    """Read the codec configuration from our private fragmented MP4 init segment."""
    with Path(path).open('rb') as handle:
        header = handle.read(256 * 1024)
    offset = header.find(b'avcC')
    if offset < 4 or offset + 8 > len(header) or header[offset+4] != 1:
        return None
    length = int.from_bytes(header[offset-4:offset], 'big')
    if length < 12 or offset - 4 + length > len(header) or b'mvex' not in header:
        return None
    video = 'avc1.' + header[offset+5:offset+8].hex()
    return 'video/mp4; codecs="' + video + (', mp4a.40.2' if b'mp4a' in header else '') + '"'


def prepare_stream(source, directory):
    """Repackage existing encoded packets for incremental delivery; no re-encoding."""
    source, directory = Path(source), Path(directory)
    binary = encoder()
    if not binary or not source.is_file():
        return False
    directory.mkdir(exist_ok=True, mode=0o700)
    target = directory / source.name
    with (directory / (source.stem + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            return bool(stream_type(str(target), target.stat().st_mtime_ns))
        fd, temporary = tempfile.mkstemp(dir=directory, suffix='.mp4')
        os.close(fd)
        try:
            subprocess.run([binary, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy',
                '-movflags', '+empty_moov+default_base_moof+frag_keyframe', '-frag_duration', '1000000',
                '-y', temporary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
            validate_container(temporary)
            if not stream_type(temporary, Path(temporary).stat().st_mtime_ns):
                return False
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            return True
        except (OSError, subprocess.SubprocessError, MediaValidationError):
            return False
        finally:
            Path(temporary).unlink(missing_ok=True)


def prepare_media(original, directory):
    original, directory = Path(original), Path(directory)
    binary = encoder()
    if not binary or not original.is_file() or original.stat().st_size < 1024:
        return False
    directory.mkdir(exist_ok=True, mode=0o700)
    preview, poster = directory / original.name, directory / (original.stem + '.jpg')
    with (directory / (original.stem + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if preview.is_file() and poster.is_file() and min(preview.stat().st_mtime_ns, poster.stat().st_mtime_ns) >= original.stat().st_mtime_ns:
            return True
        temporary = []
        try:
            for destination, options in [
                (poster, ['-ss', '0.1', '-frames:v', '1', '-vf', 'scale=480:480:force_original_aspect_ratio=decrease:force_divisible_by=2', '-q:v', '3']),
                (preview, ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                           '-maxrate', '2200k', '-bufsize', '4400k', '-pix_fmt', 'yuv420p',
                           '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart']),
            ]:
                fd, name = tempfile.mkstemp(dir=directory, suffix=destination.suffix)
                os.close(fd)
                temporary.append(name)
                command = [binary, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(original),
                           '-sn', '-dn', *options, '-threads', '2', '-y', name]
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=90)
                if Path(name).stat().st_size < 32:
                    return False
                # Keep the smaller stream; never replace or recompress the original.
                if destination == preview and Path(name).stat().st_size >= original.stat().st_size:
                    continue
                os.chmod(name, 0o600)
                os.replace(name, destination)
            return preview.is_file()
        except (OSError, subprocess.SubprocessError):
            return False  # A derivative failure must not fail/re-submit a generated film.
        finally:
            for name in temporary:
                Path(name).unlink(missing_ok=True)
