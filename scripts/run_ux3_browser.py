"""Start isolated local API/preview and run selected UX3 browser checks."""
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {'requests': ['check_ux3_requests.py'], 'virtual': ['check_ux3_virtual.py'], 'reactions': ['check_ux3_reactions.py'], 'reads': ['check_ux3_reads.py'],
          'realtime': ['check_ux3_realtime.py'], 'channels': ['check_ux3_channels.py', '--opaque'],
          'chat': ['check_ux3.py'], 'shell': ['check_ux2.py']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks', nargs='+', choices=CHECKS, default=list(CHECKS))
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    parsed = urlsplit(os.environ.get('DATABASE_URL', ''))
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55432 or parsed.path != '/ma_soi_ux2':
        raise SystemExit('Use only disposable local ma_soi_ux2 at localhost:55432')
    for port in (8026, 4176):
        try:
            connection = socket.create_connection(('127.0.0.1', port), timeout=.3)
        except OSError:
            continue
        connection.close()
        raise SystemExit(f'Port {port} already in use; this runner will not reuse an existing service')
    if not args.skip_build:
        subprocess.run(['npm', 'run', 'build', '--prefix', 'frontend'], cwd=ROOT, check=True)
    env = {**os.environ, 'APP_ENV': 'local', 'APP_ORIGINS': 'http://127.0.0.1:4176',
           'API_PROXY_TARGET': 'http://127.0.0.1:8026'}
    env.pop('REDIS_URL', None)
    log_dir = Path(tempfile.mkdtemp(prefix='ma-soi-ux3-browser-'))
    processes, logs = [], []
    commands = [([sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1',
                  '--port', '8026', '--no-access-log'], 'http://127.0.0.1:8026/api/health/live'),
                (['npm', 'run', 'preview', '--prefix', 'frontend', '--', '--host', '127.0.0.1',
                  '--port', '4176', '--strictPort'], 'http://127.0.0.1:4176')]
    try:
        for index, (command, url) in enumerate(commands):
            log = (log_dir / f'service-{index}.log').open('w');logs.append(log)
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
            processes.append(process)
            deadline = time.monotonic() + 20
            with httpx.Client(trust_env=False, timeout=1) as client:
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f'Test service exited; inspect {log_dir}')
                    try:
                        if client.get(url).status_code == 200: break
                    except httpx.HTTPError: pass
                    if time.monotonic() > deadline: raise RuntimeError(f'Test service did not become ready; inspect {log_dir}')
                    time.sleep(.2)
        print('UX3 test API and preview ready', flush=True)
        for name in args.checks:
            print('Running browser check: ' + name, flush=True)
            script, *arguments = CHECKS[name]
            subprocess.run([sys.executable, '-u', 'scripts/' + script, *arguments], cwd=ROOT, env=env, check=True, timeout=600 if name=='virtual' else 240)
    finally:
        for process in processes:
            if process.poll() is None: os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL);process.wait(timeout=5)
        for log in logs: log.close()
        print('Test services stopped. Logs: ' + str(log_dir), flush=True)


if __name__ == '__main__': main()
