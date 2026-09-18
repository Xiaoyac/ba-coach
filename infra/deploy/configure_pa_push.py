"""Manage only the new server-local push environment; never print key contents."""
import argparse
import base64
import os
from pathlib import Path

ENV = Path('/etc/bacoach/pa-push.env')
PUBLIC = Path('/opt/bacoach/secrets/pa-vapid-public.txt')


def configure(disable=False):
    if os.geteuid() != 0:
        raise SystemExit('Root required for production configuration')
    if ENV.is_symlink():
        raise SystemExit('Refusing symlink environment file')
    public = PUBLIC.read_text(encoding='ascii').strip()
    point = base64.urlsafe_b64decode(public + '=' * (-len(public) % 4))
    if len(point) != 65 or point[0] != 4:
        raise SystemExit('Invalid P-256 public key')
    content = ('PA_PUSH_ENABLED=' + ('false' if disable else 'true') + '\n'
        + 'PA_PUSH_VAPID_PUBLIC_KEY=' + public + '\n'
        + 'PA_PUSH_VAPID_PRIVATE_KEY_PATH=/opt/bacoach/secrets/pa-vapid-private.pem\n'
        + 'PA_PUSH_VAPID_SUBJECT=https://bacoach.xyz\n')
    if disable:
        # Only disable our own feature; preserve all other configuration lines.
        existing = ENV.read_text(encoding='utf-8')
        lines = existing.splitlines()
        if sum(line.startswith('PA_PUSH_ENABLED=') for line in lines) != 1:
            raise SystemExit('Unexpected configuration; manual review required')
        content = '\n'.join('PA_PUSH_ENABLED=false' if line.startswith('PA_PUSH_ENABLED=') else line for line in lines) + '\n'
        temporary = ENV.with_name('pa-push.env.disable-tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(temporary, ENV)
    else:
        fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
    print('Server-only push environment ' + ('disabled' if disable else 'created') + '; no key contents printed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disable', action='store_true')
    configure(parser.parse_args().disable)
