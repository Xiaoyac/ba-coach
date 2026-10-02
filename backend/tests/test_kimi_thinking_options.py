from types import SimpleNamespace

import pytest

from app.generation_policy import native_thinking_options


@pytest.mark.parametrize("enabled", [False, True])
def test_dashscope_kimi_omits_unsupported_qwen_budget(enabled):
    settings = SimpleNamespace(
        deepseek_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        deepseek_model="kimi-k3",
        qwen_thinking_budget=1024,
    )
    assert native_thinking_options(settings, "deepseek", enabled=enabled) == {
        "enable_thinking": enabled
    }


def test_qwen_router_override_keeps_its_own_budget():
    settings = SimpleNamespace(
        deepseek_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        deepseek_model="kimi-k3",
        qwen_thinking_budget=1024,
    )
    assert native_thinking_options(
        settings, "deepseek", enabled=True, model="qwen3.8-flash"
    ) == {"enable_thinking": True, "thinking_budget": 1024}


def test_direct_moonshot_keeps_native_thinking_format():
    settings = SimpleNamespace(
        deepseek_base_url="https://api.moonshot.cn/v1",
        deepseek_model="kimi-k3",
    )
    assert native_thinking_options(settings, "deepseek", enabled=False) == {
        "thinking": {"type": "disabled"}
    }
