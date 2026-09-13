#!/usr/bin/env bash
# Two synthetic requests through the actual proxy, originating in hms-cpap.
# Read the ESO-synced master key into memory and curl stdin, never into argv/logs.
set -euo pipefail
python3 - <<'PY'
import base64
import json
import subprocess

secret = json.loads(subprocess.check_output(['kubectl', '-n', 'ai', 'get', 'secret', 'litellm', '-o', 'json']))
key = base64.b64decode(secret['data']['LITELLM_MASTER_KEY']).decode()
config = 'header = ' + json.dumps('Authorization: Bearer ' + key) + '\n'
base = 'http://litellm.ai.svc.cluster.local:4000'
model = 'chatgpt/gpt-5.6-sol'


def request(path, payload=None):
    args = ['kubectl', '-n', 'home', 'exec', '-i', 'deployment/hms-cpap', '-c', 'app', '--', 'curl', '--config', '-', '--silent', '--show-error', '--fail-with-body', '--max-time', '90', base + path]
    if payload is not None:
        args += ['-H', 'Content-Type: application/json', '-d', json.dumps(payload)]
    result = subprocess.run(args, input=config, text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(f'{path} failed: {result.stdout[:2000]} {result.stderr[:1000]}')
    return json.loads(result.stdout)


models = request('/v1/models')
assert model in [m['id'] for m in models['data']]
print('PASS authenticated in-cluster model discovery', flush=True)
response = request('/v1/chat/completions', {'model': model, 'messages': [{'role': 'user', 'content': 'Connectivity test. Reply with OK only.'}], 'stream': False, 'temperature': 0.3, 'max_completion_tokens': 2048})
text = response['choices'][0]['message']['content']
assert text and 'OK' in text, text
print('PASS hms-cpap-shaped HTTP completion:', repr(text), flush=True)
PY
