from app.config import Settings


def test_default_compatibility_provider_uses_qwen_max_wire_model():
    settings = Settings(_env_file=None)

    assert settings.deepseek_model == "qwen-max"
    assert settings.deepseek_router_model == "qwen-max"
    assert settings.qwen_thinking_budget == 1024


def test_existing_provider_endpoint_contract_can_be_overridden_without_renaming_env_fields():
    settings = Settings(
        _env_file=None,
        deepseek_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        deepseek_model="qwen-max",
        deepseek_router_model="qwen-max",
    )

    assert settings.deepseek_base_url.endswith("/compatible-mode/v1")
    assert settings.deepseek_model == settings.deepseek_router_model == "qwen-max"
