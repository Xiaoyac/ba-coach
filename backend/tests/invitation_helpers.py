"""Test-only invitation issuer. Production has no registration bypass."""
from itertools import count
from app.db import get_sessionmaker
from app.invitations import new_invitation_code
from app.models import RegistrationInvite


def issue_test_invitation(client):
    async def issue():
        code = new_invitation_code()
        async with get_sessionmaker()() as db:
            db.add(RegistrationInvite(code=code))
            await db.commit()
        return code
    return client.portal.call(issue)


_research_ids = count(1)


def next_research_id():
    """A fresh three-digit 被试编号 from 500–999; tests that pick one use 000–499."""
    return f"{500 + next(_research_ids) % 500:03d}"


def with_test_invitation(client, payload):
    """Legacy auth tests supply an invitation explicitly while testing other fields."""
    payload = {'research_id': next_research_id(), 'phone': '13800138000', **payload}
    if 'invitation_code' in payload:
        return payload
    return {**payload, 'invitation_code': issue_test_invitation(client)}
