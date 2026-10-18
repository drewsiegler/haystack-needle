"""A small local SMTP server for tests: EHLO, optional STARTTLS, AUTH PLAIN, one or more messages."""

import base64
import datetime
import ipaddress
import socketserver
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def make_cert(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate for 127.0.0.1."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self):
        fake = self.server.fake
        tls = False
        mail_from, rcpts = None, []
        self.reply("220 fake ESMTP ready")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode().rstrip("\r\n")
            verb = command.split(" ", 1)[0].upper()
            if verb == "EHLO":
                lines = ["fake"] + (["STARTTLS"] if fake.tls_context and not tls else []) + ["AUTH PLAIN"]
                for i, ext in enumerate(lines):
                    self.reply(("250 " if i == len(lines) - 1 else "250-") + ext)
            elif verb == "STARTTLS" and fake.tls_context and not tls:
                self.reply("220 go ahead")
                try:
                    self.request = fake.tls_context.wrap_socket(self.request, server_side=True)
                except ssl.SSLError:
                    return  # the client didn't trust the certificate and hung up, as it should
                self.rfile = self.request.makefile("rb")
                self.wfile = self.request.makefile("wb")
                tls = True
            elif verb == "AUTH":
                user = base64.b64decode(command.split()[2]).split(b"\0")[1].decode()
                fake.logins.append((user, tls))
                self.reply("235 ok")
            elif verb == "MAIL" and fake.limit is not None and len(fake.messages) >= fake.limit:
                self.reply("451 4.7.0 Hourly external limit reached")
            elif verb == "MAIL":
                mail_from, rcpts = command, []
                self.reply("250 ok")
            elif verb == "RCPT" and any(f"<{a}>" in command for a in fake.refuse):
                self.reply(f"550 5.1.1 {command.split(':', 1)[1]}: no such mailbox")  # quotes the address
            elif verb == "RCPT":
                rcpts.append(command)
                self.reply("250 ok")
            elif verb == "DATA":
                self.reply("354 go ahead")
                data = b""
                while (chunk := self.rfile.readline()) not in (b".\r\n", b""):
                    data += chunk
                fake.messages.append((mail_from, rcpts, data, tls))
                self.reply("250 queued")
            elif verb == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("502 not here")


class FakeSMTP:
    def __init__(self, tls_context: ssl.SSLContext | None):
        self.tls_context = tls_context
        self.logins: list[tuple[str, bool]] = []
        self.messages: list[tuple] = []
        self.refuse: set[str] = set()  # addresses to refuse
        self.limit: int | None = None  # messages to take before answering like a sending limit
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.fake = self
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
