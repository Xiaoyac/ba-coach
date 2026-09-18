"""Separate durable worker; run only after migration and VAPID configuration."""
import argparse
import asyncio
import json
from pathlib import Path
from app.db import get_sessionmaker, dispose_db
from app.pa_push import available, tick, vapid_subject
from app.config import get_settings
from app.pa_push_checks import tick_checks


def validate_keys():
    import base64
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    settings = get_settings()
    path = Path(settings.pa_push_vapid_private_key_path)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('VAPID key must use P-256')
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()
    if public != settings.pa_push_vapid_public_key: raise ValueError('VAPID public/private keys do not match')
    subject = urlsplit(settings.pa_push_vapid_subject)
    if subject.scheme not in {'mailto', 'https'}: raise ValueError('VAPID subject must be a contact URL')
    from py_vapid import Vapid02
    Vapid02.from_file(str(path)).sign({'sub': vapid_subject(), 'aud': 'https://fcm.googleapis.com'})


async def main(once=False, check=False):
    if not available(): raise SystemExit('PA push is disabled or unconfigured; no notifications sent')
    if not Path(get_settings().pa_push_vapid_private_key_path).is_file():
        raise SystemExit('VAPID private key file missing')
    validate_keys()
    try:
        if check:
            from sqlalchemy import select
            from app.push_schema import metadata
            async with get_sessionmaker()() as db:
                for table in metadata.tables.values(): await db.execute(select(table).limit(0))
            print('READY: key and schema checks passed; no notifications sent')
            return
        while True:
            try: print(json.dumps({'checks': await tick_checks(get_sessionmaker())}), flush=True)
            except Exception as exc:
                print(json.dumps({'check_worker_error': type(exc).__name__}), flush=True)
                if once: raise SystemExit(1)
            try: print(json.dumps(await tick(get_sessionmaker())), flush=True)
            except Exception as exc:
                # Never leak endpoint/auth details through exception text.
                print(json.dumps({'worker_error': type(exc).__name__}), flush=True)
                if once: raise SystemExit(1)
            if once: break
            await asyncio.sleep(30)
    finally: await dispose_db()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--check', action='store_true', help='Read-only preflight; never sends notifications')
    args=parser.parse_args()
    asyncio.run(main(args.once,args.check))
