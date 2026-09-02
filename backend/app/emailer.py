"""Small SMTP delivery boundary for account-security emails.

No endpoint ever returns a raw verification/reset token.  The only intended
way it leaves the server is through this transport, which keeps tests able to
replace one function while production can use any standards-compliant SMTP
provider.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from .config import get_settings


class EmailDeliveryUnavailable(RuntimeError):
    """SMTP is not configured or delivery failed."""


def email_delivery_configured() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_from_email)


def _send_sync(*, recipient: str, subject: str, text: str, html: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        raise EmailDeliveryUnavailable("SMTP is not configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = recipient
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    try:
        with smtp_class(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if not settings.smtp_use_ssl and settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
    except EmailDeliveryUnavailable:
        raise
    except Exception as exc:
        raise EmailDeliveryUnavailable("SMTP delivery failed") from exc


async def send_account_email(
    *, recipient: str, subject: str, text: str, html: str
) -> None:
    """Deliver without blocking FastAPI's event loop on SMTP I/O."""

    await asyncio.to_thread(
        _send_sync,
        recipient=recipient,
        subject=subject,
        text=text,
        html=html,
    )
