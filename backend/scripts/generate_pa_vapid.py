"""Generate server-only keys. Never overwrite an existing key or print secrets."""
import argparse
import base64
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def generate(directory):
    target = Path(directory).resolve()
    target.mkdir(mode=0o700, parents=False, exist_ok=True)
    private = target / 'pa-vapid-private.pem'
    public = target / 'pa-vapid-public.txt'
    if private.exists() or public.exists(): raise SystemExit('Keys already exist; refusing rotation/overwrite')
    key = ec.generate_private_key(ec.SECP256R1())
    data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    encoded = base64.urlsafe_b64encode(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=')
    for path, content in ((private, data), (public, encoded + b'\n')):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream: stream.write(content)
    print('Created server-only VAPID key files; contents not printed. Keep private key outside releases.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    generate(parser.parse_args().directory)
