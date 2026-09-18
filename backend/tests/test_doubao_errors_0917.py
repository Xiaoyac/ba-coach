from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.doubao import DoubaoProvider, _api_error_message


def api_error(body, status=429):
    return openai.APIStatusError(
        "synthetic upstream error", body=body,
        response=httpx.Response(status, request=httpx.Request("POST", "https://example.invalid")),
    )


@pytest.mark.parametrize("nested", [True, False])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.asyncio
async def test_quota_error_is_actionable_without_exposing_account(nested, streaming):
    body = {"code": "SetLimitExceeded", "message": "account-private-id"}
    provider = DoubaoProvider(Settings(_env_file=None, doubao_api_key="synthetic", doubao_model="synthetic"))
    provider._client.chat.completions.create = AsyncMock(side_effect=api_error({"error": body} if nested else body))
    try:
        with pytest.raises(ProviderError) as exc:
            if streaming:
                async for _ in provider.stream(system="synthetic", messages=[]):
                    pass
            else:
                await provider.complete(system="synthetic", messages=[])
        assert "安全体验" in str(exc.value)
        assert "额外费用" in str(exc.value)
        assert "account-private-id" not in str(exc.value)
    finally:
        await provider._client.close()


@pytest.mark.parametrize("body,status", [({"code": "RateLimitExceeded"}, 429), ({"code": "SetLimitExceeded"}, 400), (None, 500)])
def test_other_errors_are_not_misreported_as_quota(body, status):
    message = _api_error_message(api_error(body, status))
    assert f"Doubao API error {status}" in message
    assert "安全体验" not in message
