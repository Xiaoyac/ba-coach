"""Test-only invitation issuer. Production has no registration bypass."""
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


def with_test_invitation(client, payload):
    """Legacy auth tests supply an invitation explicitly while testing other fields."""
    if 'invitation_code' in payload:
        return payload
    return {**payload, 'invitation_code': issue_test_invitation(client)}
