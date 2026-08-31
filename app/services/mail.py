"""Getting a message to a person.

There is exactly one message this project ever sends, and it is a password
reset link. That is why this is fifty lines and not a queue: retries, bounce
handling and templating all exist to solve problems a single transactional
mail does not have.

With no SMTP host configured the link is written to the log instead. That is
deliberate rather than a stub. A self-hosted instance frequently has no mail
relay to hand, and the alternative -- refusing to issue resets at all -- locks
the operator out of their own install. The log is a private channel on a box
they already control, and the message says plainly that it went there.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import Settings

logger = logging.getLogger("beacon.mail")


def _send_over_smtp(settings: Settings, message: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def deliver(settings: Settings, *, to: str, subject: str, body: str) -> bool:
    """Send one message. True if it went over SMTP, False if it went to the log.

    Never raises. A reset that cannot be delivered must still leave the caller
    free to answer the same way it answers everything else -- an exception here
    would turn a mail outage into a way of asking which addresses are
    registered.
    """
    if not settings.smtp_host:
        logger.warning(
            "no SMTP host configured; the message below was not sent",
            extra={"context": {"to": to, "subject": subject, "body": body}},
        )
        return False

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        _send_over_smtp(settings, message)
    except (OSError, smtplib.SMTPException):
        logger.exception("could not send mail", extra={"context": {"subject": subject}})
        return False

    return True
