"""Send email through Proton's SMTP submission server.

The connection switches to TLS with STARTTLS before logging in; if that fails,
nothing is sent. The login, the token and the alert address come from the
environment (Actions secrets) and are never printed.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

HOST = "smtp.protonmail.ch"
PORT = 587
SENDER = "Haystack Needle <alerts@andrewsiegler.com>"
SECRETS = ("SMTP_USERNAME", "SMTP_PASSWORD", "ALERT_TO")


class MailError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    to: str

    def __repr__(self) -> str:  # keep the token and address out of tracebacks and logs
        return "Settings(…)"


def settings_from_env(env=os.environ) -> Settings:
    missing = [name for name in SECRETS if not env.get(name)]
    if missing:
        raise MailError(f"Can't send email: set the {', '.join(missing)} secret"
                        f"{'s' if len(missing) > 1 else ''} in the repo's Actions secrets.")
    return Settings(env["SMTP_USERNAME"], env["SMTP_PASSWORD"], env["ALERT_TO"])


def compose(subject: str, text: str, html: str | None, to: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = SENDER
    message["To"] = to
    message["Subject"] = " ".join(subject.split())
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=SENDER.split("@")[1].rstrip(">"))
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message


def send(messages: list[EmailMessage], settings: Settings, host: str = HOST, port: int = PORT,
         context: ssl.SSLContext | None = None, timeout: float = 60) -> None:
    context = context or ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.ehlo()
        try:
            if not smtp.has_extn("starttls"):
                raise MailError("not offered")
            smtp.starttls(context=context)
        except (MailError, smtplib.SMTPException, ssl.SSLError, OSError) as error:
            raise MailError(f"Didn't send: couldn't switch to an encrypted connection (STARTTLS) "
                            f"with {host} ({type(error).__name__}). Nothing was sent and the "
                            f"login was never offered.") from None
        if not isinstance(smtp.sock, ssl.SSLSocket):
            raise MailError(f"Didn't send: the connection to {host} isn't encrypted.")
        smtp.ehlo()
        try:
            smtp.login(settings.username, settings.password)
        except smtplib.SMTPAuthenticationError:
            raise MailError(f"Didn't send: {host} refused the login. Check the SMTP_USERNAME "
                            "and SMTP_PASSWORD secrets (the password is a Proton SMTP token).") from None
        for message in messages:
            smtp.send_message(message)
