"""One read policy for UI, prompt context and compatibility projection."""
from .schemas import Supporter

LEGACY_SUPPORTER_FIELDS = frozenset(f"supporter{i}_{part}" for i in (1,2) for part in ("relation","nickname","influence"))


def effective_supporters(profile, extension) -> list[Supporter]:
    # [] means explicitly cleared, whereas NULL means not migrated. Never
    # resurrect legacy people after an explicit clear.
    if extension is not None and extension.supporters is not None:
        return [Supporter.model_validate(value) for value in extension.supporters]
    result = []
    for i in (1,2):
        relation = getattr(profile, f"supporter{i}_relation")
        nickname = getattr(profile, f"supporter{i}_nickname")
        if relation or nickname:
            result.append(Supporter(relation=relation or "未说明（旧档案）", nickname=nickname,
                influence=getattr(profile, f"supporter{i}_influence")))
    return result
