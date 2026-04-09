"""
Email Tool — SendGrid integration for campaigns and transactional emails.

Supports:
  - Single transactional email (appointment confirmations, follow-ups)
  - Bulk campaign sends with personalisation
  - Template-based sends (using SendGrid dynamic templates)

In demo mode, emails are logged but not sent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.src.config import settings

logger = logging.getLogger(__name__)


class EmailTool:
    """
    SendGrid email sender.

    Usage:
        tool = EmailTool()
        await tool.send(
            to_email="customer@example.com",
            subject="Your appointment is confirmed!",
            body_text="Hi Alice, ...",
        )
    """

    async def send(
        self,
        to_email: str,
        subject: str,
        body_html: str = "",
        body_text: str = "",
        to_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        template_id: Optional[str] = None,
        template_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Send a single email via SendGrid.

        Returns: {"success": True, "message_id": str}
        """
        if not settings.SENDGRID_API_KEY:
            logger.info(
                "[DEMO EMAIL] To: %s | Subject: %s | Body: %.80s...",
                to_email, subject, body_text or body_html,
            )
            return {"success": True, "message_id": "DEMO_EMAIL_ID", "_demo": True}

        try:
            from sendgrid import SendGridAPIClient  # type: ignore
            from sendgrid.helpers.mail import Mail, To, From, Subject, Content  # type: ignore

            message = Mail()
            message.from_email = From(settings.SENDGRID_FROM_EMAIL, settings.SENDGRID_FROM_NAME)
            message.to = [To(to_email, to_name or "")]
            message.subject = Subject(subject)

            if template_id:
                message.template_id = template_id
                if template_data:
                    from sendgrid.helpers.mail import DynamicTemplateData  # type: ignore
                    message.dynamic_template_data = template_data
            else:
                if body_html:
                    message.content = [Content("text/html", body_html)]
                elif body_text:
                    message.content = [Content("text/plain", body_text)]

            if reply_to:
                from sendgrid.helpers.mail import ReplyTo  # type: ignore
                message.reply_to = ReplyTo(reply_to)

            sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
            response = sg.send(message)
            logger.info("Email sent to %s: status=%d", to_email, response.status_code)
            return {
                "success": True,
                "message_id": response.headers.get("X-Message-Id", "unknown"),
                "status_code": response.status_code,
            }
        except Exception as exc:
            logger.exception("SendGrid send failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def send_bulk(
        self,
        recipients: List[Dict[str, Any]],
        subject: str,
        body_template: str,
        from_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send personalised bulk email to a list of recipients.

        Each recipient dict should have: email, name, and any template variables.
        Uses simple string interpolation; for production use SendGrid templates.

        Returns: {"sent": int, "failed": int}
        """
        sent = 0
        failed = 0

        for recipient in recipients:
            email = recipient.get("email")
            if not email:
                failed += 1
                continue

            # Simple personalisation via format
            personalised_body = body_template.format(
                name=recipient.get("name", "Valued Customer"),
                **{k: v for k, v in recipient.items() if k not in ("email", "name")},
            )

            result = await self.send(
                to_email=email,
                to_name=recipient.get("name"),
                subject=subject,
                body_text=personalised_body,
            )
            if result.get("success"):
                sent += 1
            else:
                failed += 1

        logger.info("Bulk email completed: sent=%d failed=%d", sent, failed)
        return {"sent": sent, "failed": failed, "total": len(recipients)}
