"""Production queue smoke with an isolated QA account and non-device endpoint.

Exercises durable timing and error reporting, never claims browser delivery.
Subscription is removed afterwards; credentials stay in ignored QA storage.
"""
import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from full_cycle_acceptance_0920 import BASE, Client


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


client = Client('PushDelay0921-' + uuid.uuid4().hex[:8])
public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
device = client.api('POST', '/api/push/subscriptions', json={
    'endpoint': 'https://fcm.googleapis.com/wp/qa-non-device-' + uuid.uuid4().hex,
    'keys': {'p256dh': encoded(public), 'auth': encoded(os.urandom(16))}})
try:
    started = datetime.now(timezone.utc)
    check = client.api('POST', '/api/push/checks', json={'device_id': device['id'], 'delay_seconds': 5})
    delta = (datetime.fromisoformat(check['due_at']) - started).total_seconds()
    assert 4 <= delta <= 8, delta
    observations = [{'state': check['state'], 'seconds': 0}]
    stop = time.monotonic() + 45
    while time.monotonic() < stop:
        time.sleep(2)
        check = next(item for item in client.api('GET', '/api/push/checks')['items'] if item['id'] == check['id'])
        seconds = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        if check['state'] != observations[-1]['state']:
            observations.append({'state': check['state'], 'seconds': seconds})
        if check['state'] not in {'queued', 'attempting'}:
            break
    result = {'selected_delay': 5, 'due_after_seconds': round(delta, 2), 'observations': observations,
              'final_state': check['state'], 'http_status': check['http_status'],
              'browser_displayed': bool(check['displayed_at']), 'synthetic_endpoint': True}
    (client.folder / 'push-delay-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    assert check['state'] in {'unreachable', 'timeout', 'failed'}, result
    assert observations[-1]['seconds'] < 30, result
finally:
    response = client.s.delete(BASE + '/api/push/subscriptions/' + device['id'], timeout=15)
    response.raise_for_status()
