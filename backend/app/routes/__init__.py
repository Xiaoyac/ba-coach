from .assessment import router as assessment_router
from .admin_accounts import router as admin_accounts_router
from .admin_knowledge import router as admin_knowledge_router
from .admin_prompts import router as admin_prompts_router
from .admin_sandbox import router as admin_sandbox_router
from .auth import router as auth_router
from .chat import router as chat_router
from .conversations import router as conversation_router
from .issue_reports import router as issue_reports_router
from .profile import router as profile_router

__all__ = [
    "assessment_router",
    "admin_accounts_router",
    "admin_knowledge_router",
    "admin_prompts_router",
    "admin_sandbox_router",
    "auth_router",
    "chat_router",
    "conversation_router",
    "issue_reports_router",
    "profile_router",
]
