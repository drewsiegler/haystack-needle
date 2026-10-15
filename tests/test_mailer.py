import email
import ssl
from email.policy import default

import pytest

from needle import mailer
from smtp_server import FakeSMTP, make_cert

SETTINGS = mailer.Settings("alerts@example.org", "not-a-real-token", "someone@example.org")


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    return make_cert(tmp_path_factory.mktemp("tls"))


def server(cert, starttls: bool) -> FakeSMTP:
    context = None
    if starttls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*cert)
    return FakeSMTP(context)


def test_sends_over_starttls_with_display_name(cert):
    fake = server(cert, starttls=True)
    try:
        message = mailer.compose("Subject here", "Plain text", "<p>HTML</p>", SETTINGS.to)
        mailer.send([message], SETTINGS, host="127.0.0.1", port=fake.port,
                    context=ssl.create_default_context(cafile=str(cert[0])))
    finally:
        fake.close()

    assert fake.logins == [("alerts@example.org", True)]  # logged in only after TLS
    [(_, rcpts, data, tls)] = fake.messages
    assert tls and [r.lower() for r in rcpts] == ["rcpt to:<someone@example.org>"]
    sent = email.message_from_bytes(data, policy=default)
    assert sent["From"] == "Haystack Needle <alerts@andrewsiegler.com>"
    assert sent["Message-ID"].endswith("@andrewsiegler.com>")
    assert sent.get_body(("plain",)).get_content().strip() == "Plain text"


def test_refuses_without_starttls(cert):
    fake = server(cert, starttls=False)
    try:
        with pytest.raises(mailer.MailError, match="STARTTLS"):
            mailer.send([mailer.compose("s", "t", None, SETTINGS.to)], SETTINGS,
                        host="127.0.0.1", port=fake.port)
    finally:
        fake.close()
    assert fake.logins == [] and fake.messages == []


def test_refuses_an_untrusted_certificate(cert):
    fake = server(cert, starttls=True)
    try:
        with pytest.raises(mailer.MailError, match="STARTTLS"):
            mailer.send([mailer.compose("s", "t", None, SETTINGS.to)], SETTINGS,
                        host="127.0.0.1", port=fake.port)  # default trust store doesn't know the test cert
    finally:
        fake.close()
    assert fake.logins == []


def test_missing_secrets_are_named_not_shown():
    with pytest.raises(mailer.MailError) as error:
        mailer.settings_from_env({"SMTP_USERNAME": "u", "SMTP_PASSWORD": "secret-token"})
    assert "ALERT_TO" in str(error.value) and "secret-token" not in str(error.value)
    assert "secret-token" not in repr(SETTINGS) and "someone" not in repr(SETTINGS)
