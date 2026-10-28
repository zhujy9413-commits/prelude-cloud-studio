"""Frozen generation inputs; no keys, hosts, network access, or image bytes stored."""
import base64
import hashlib
import json

MODEL = 'wan2.6-t2v'
IMAGE_MODEL = 'wan2.6-i2v-flash'
SIZES = {'16:9': '1280*720', '9:16': '720*1280', '1:1': '960*960'}
API_PATH = '/api/v1/services/aigc/video-generation/video-synthesis'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def snapshot(job, original_prompt=None, reference_bytes=None):
    referenced = bool(job.get('reference_preset') or job.get('reference_id'))
    parameters = {'duration': job['duration'], 'prompt_extend': True, 'shot_type': 'single', 'watermark': False}
    if referenced:
        if not reference_bytes:
            raise ValueError('Reference bytes missing')
        parameters.update(resolution='720P', audio=True)
        reference = {'kind': 'upload' if job.get('reference_id') else 'preset',
                     'id': job.get('reference_id'), 'preset': job.get('reference_preset'),
                     'mime_type': 'image/jpeg' if job.get('reference_id') else 'image/webp',
                     'sha256': digest(reference_bytes), 'size_bytes': len(reference_bytes)}
    else:
        parameters['size'] = SIZES[job['aspect']]
        reference = None
    original = job['prompt'] if original_prompt is None else original_prompt
    return {'version': 1, 'model': IMAGE_MODEL if referenced else MODEL,
            'original_prompt': original, 'submitted_prompt': job['prompt'],
            'original_prompt_sha256': digest(original.encode()), 'submitted_prompt_sha256': digest(job['prompt'].encode()),
            'parameters': parameters, 'reference': reference,
            'provider_rewritten_prompt': None, 'provider_rewritten_prompt_status': 'not_returned_by_wan2.6'}


def payload(spec, reference_bytes=None):
    """Build exactly the recorded body; verify that the selected image has not changed."""
    if spec.get('version') != 1 or spec.get('model') not in (MODEL, IMAGE_MODEL):
        raise ValueError('Unsupported generation record')
    prompt = spec['submitted_prompt']
    if digest(prompt.encode()) != spec['submitted_prompt_sha256']:
        raise ValueError('Prompt digest mismatch')
    values = {'prompt': prompt}
    reference = spec.get('reference')
    if reference:
        if not reference_bytes or digest(reference_bytes) != reference['sha256'] or len(reference_bytes) != reference['size_bytes']:
            raise ValueError('Reference digest mismatch')
        values['img_url'] = 'data:' + reference['mime_type'] + ';base64,' + base64.b64encode(reference_bytes).decode()
    elif spec['model'] == IMAGE_MODEL:
        raise ValueError('Image model requires a reference')
    return {'model': spec['model'], 'input': values, 'parameters': dict(spec['parameters'])}


def frozen_payload(job, reference_bytes=None):
    raw = job.get('request_spec')
    spec = json.loads(raw) if raw else snapshot(job, reference_bytes=reference_bytes)
    if spec['model'] != job.get('model', spec['model']) or spec['submitted_prompt'] != job['prompt'] or spec['parameters']['duration'] != job['duration']:
        raise ValueError('Job does not match its frozen record')
    body = payload(spec, reference_bytes)
    if job.get('request_fingerprint') and digest(encoded(body)) != job['request_fingerprint']:
        raise ValueError('Request fingerprint mismatch')
    return body


def public_record(job):
    raw = job.get('request_spec')
    if not raw:
        return {'state': 'legacy_unrecorded', 'snapshot': None, 'request_sha256': None,
                'recorded_at': None, 'submission_started_at': None}
    return {'state': 'recorded', 'snapshot': json.loads(raw), 'request_sha256': job.get('request_fingerprint'),
            'recorded_at': job.get('request_recorded_at'), 'submission_started_at': job.get('request_started_at')}
