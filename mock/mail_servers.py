"""A tiny fake SMTP and IMAP server for tests and the demo. Not a real mail server.

SMTP: EHLO, AUTH PLAIN, MAIL, RCPT, DATA, RSET, NOOP, QUIT. Accepted messages land in `MailStore.outbox`.
IMAP: CAPABILITY, LOGIN, SELECT (says its UIDVALIDITY), UID SEARCH (ALL, UNSEEN, UID a:b or a:*), UID FETCH (RFC822, RFC822.SIZE, BODYSTRUCTURE, and BODY.PEEK of the whole message,
HEADER, a numbered part, or n.MIME, with an optional <start.length> window), UID STORE (flags), NOOP, LOGOUT.
Messages in `MailStore.inbox` are what the IMAP client sees. It speaks only what Python's smtplib and imaplib send.
"""

from __future__ import annotations

import email
import re
import socketserver
import threading
from dataclasses import dataclass, field


@dataclass
class MailStore:
    outbox: list[dict] = field(default_factory=list)  # accepted by SMTP: {"from","to","data"}
    inbox: list[dict] = field(default_factory=list)  # visible to IMAP: {"uid","raw","seen"}
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: dict[str, str] = field(default_factory=lambda: {"demo": "demo-password"})
    uidvalidity: int = 4001  # a real server changes this number when it renumbers a mailbox

    def deliver(self, raw: bytes) -> int:
        with self.lock:
            uid = len(self.inbox) + 1
            self.inbox.append({"uid": uid, "raw": raw, "seen": False})
            return uid


def _quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _structure(part) -> str:
    """The BODYSTRUCTURE of a message as an IMAP server writes it: nested lists, a multipart ends with its subtype, a text part carries size, line count and disposition."""
    if part.is_multipart():
        return "(" + "".join(_structure(p) for p in part.get_payload()) + f" {_quote(part.get_content_subtype().upper())})"
    payload = part.get_payload()
    size = len(payload.encode("utf-8", "surrogateescape")) if isinstance(payload, str) else 0
    charset = part.get_content_charset()
    params = f"({_quote('CHARSET')} {_quote(charset)})" if charset else "NIL"
    enc = _quote(str(part.get("Content-Transfer-Encoding", "7bit")).upper())
    disp = part.get_content_disposition()
    name = part.get_filename()
    dsp = f"({_quote(disp.upper())} ({_quote('FILENAME')} {_quote(name)}))" if disp and name else (f"({_quote(disp.upper())} NIL)" if disp else "NIL")
    head = f"({_quote(part.get_content_maintype().upper())} {_quote(part.get_content_subtype().upper())} {params} NIL NIL {enc} {size}"
    if part.get_content_maintype() == "text":
        return head + f" {payload.count(chr(10)) + 1} NIL {dsp} NIL NIL)"
    return head + f" NIL {dsp} NIL NIL)"


def _numbered(msg, prefix: str = "") -> dict:
    """{"1": part, "1.1": part, ...}: the parts of a message by their IMAP section number."""
    if not msg.is_multipart():
        return {prefix or "1": msg}
    out: dict = {}
    for i, child in enumerate(msg.get_payload(), start=1):
        out.update(_numbered(child, f"{prefix}.{i}" if prefix else str(i)))
    return out


def _header_bytes(part) -> bytes:
    return b"".join(f"{k}: {v}\r\n".encode("utf-8", "surrogateescape") for k, v in part.items()) + b"\r\n"


def section_bytes(raw: bytes, spec: str) -> bytes | None:
    """What `BODY[spec]` holds: HEADER, a part number (its body), or `n.MIME` (its own header lines). None when there is no such part."""
    msg = email.message_from_bytes(raw)
    spec = spec.upper()
    if spec.startswith("HEADER"):
        return re.split(rb"\r?\n\r?\n", raw, maxsplit=1)[0] + b"\r\n\r\n"
    parts = _numbered(msg)
    mime = spec.endswith(".MIME")
    part = parts.get(spec[:-5] if mime else spec)
    if part is None or (not mime and part.is_multipart()):
        return None
    if mime:
        return _header_bytes(part)
    payload = part.get_payload()
    return payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else b""


class _SMTP(socketserver.StreamRequestHandler):
    store: MailStore

    def _w(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())
        self.wfile.flush()

    def handle(self) -> None:
        self._w("220 fake-smtp ready")
        mail_from, rcpts = "", []
        while True:
            line = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
            if not line:
                return
            cmd = line.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO"):
                self.wfile.write(b"250-fake-smtp\r\n250 AUTH PLAIN\r\n")
                self.wfile.flush()
            elif cmd == "AUTH":
                self._w("235 ok")
            elif cmd == "MAIL":
                mail_from, rcpts = line.split(":", 1)[1].strip().strip("<>").split(">")[0], []
                self._w("250 ok")
            elif cmd == "RCPT":
                rcpts.append(line.split(":", 1)[1].strip().strip("<>").split(">")[0])
                self._w("250 ok")
            elif cmd == "DATA":
                self._w("354 end with a dot")
                chunks = []
                while True:
                    row = self.rfile.readline()
                    if row in (b".\r\n", b".\n", b""):
                        break
                    chunks.append(row[1:] if row.startswith(b"..") else row)
                with self.store.lock:
                    self.store.outbox.append({"from": mail_from, "to": list(rcpts), "data": b"".join(chunks).decode(errors="replace")})
                self._w("250 queued")
            elif cmd in ("RSET", "NOOP"):
                self._w("250 ok")
            elif cmd == "QUIT":
                self._w("221 bye")
                return
            else:
                self._w("502 not supported")


class _IMAP(socketserver.StreamRequestHandler):
    store: MailStore

    def _w(self, data: bytes) -> None:
        self.wfile.write(data)
        self.wfile.flush()

    def handle(self) -> None:
        self._w(b"* OK fake-imap ready\r\n")
        authed = False
        while True:
            raw = self.rfile.readline().decode(errors="replace").strip()
            if not raw:
                return
            tag, _, rest = raw.partition(" ")
            cmd, _, arg = rest.partition(" ")
            cmd = cmd.upper()
            if cmd == "CAPABILITY":
                self._w(b"* CAPABILITY IMAP4rev1\r\n" + tag.encode() + b" OK done\r\n")
            elif cmd == "LOGIN":
                m = re.findall(r'"([^"]*)"|(\S+)', arg)
                user, pw = (m[0][0] or m[0][1]), (m[1][0] or m[1][1])
                if self.store.users.get(user) == pw:
                    authed = True
                    self._w(tag.encode() + b" OK logged in\r\n")
                else:
                    self._w(tag.encode() + b" NO invalid credentials\r\n")
            elif cmd == "SELECT" and authed:
                n = len(self.store.inbox)
                self._w(f"* {n} EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY {self.store.uidvalidity}] UIDs valid\r\n{tag} OK [READ-WRITE] selected\r\n".encode())
            elif cmd == "UID" and authed:
                sub, _, a2 = arg.partition(" ")
                sub = sub.upper()
                if sub == "SEARCH":
                    with self.store.lock:
                        crit = a2.upper()
                        span = re.search(r"UID (\d+):(\d+|\*)", crit)  # a real server answers "n:*" with the newest message even when it is older than n
                        top = max([m["uid"] for m in self.store.inbox] or [0])
                        lo, hi = (int(span.group(1)), top if span.group(2) == "*" else int(span.group(2))) if span else (1, top)
                        uids = [m["uid"] for m in self.store.inbox if ("UNSEEN" not in crit or not m["seen"]) and lo <= m["uid"] <= hi]
                        if span and span.group(2) == "*" and not uids and self.store.inbox:
                            uids = [top]
                    self._w(("* SEARCH " + " ".join(map(str, uids))).strip().encode() + b"\r\n" + tag.encode() + b" OK done\r\n")
                elif sub == "FETCH":
                    uid = int(a2.split(" ", 1)[0])
                    with self.store.lock:
                        msg = next((m for m in self.store.inbox if m["uid"] == uid), None)
                    if msg is None:
                        self._w(tag.encode() + b" OK done\r\n")
                    elif "RFC822.SIZE" in a2.upper():
                        self._w(f"* {uid} FETCH (UID {uid} RFC822.SIZE {len(msg['raw'])})\r\n{tag} OK done\r\n".encode())
                    elif "BODYSTRUCTURE" in a2.upper():
                        self._w(f"* {uid} FETCH (UID {uid} BODYSTRUCTURE {_structure(email.message_from_bytes(msg['raw']))})\r\n{tag} OK done\r\n".encode())
                    elif (sec := re.search(r"BODY\.PEEK\[([^\]]+)\](?:<(\d+)\.(\d+)>)?", a2, re.I)):
                        data = section_bytes(msg["raw"], sec.group(1))
                        if data is None:
                            self._w(f"* {uid} FETCH (UID {uid} BODY[{sec.group(1)}] NIL)\r\n{tag} OK done\r\n".encode())
                        else:
                            start = int(sec.group(2) or 0)
                            window = f"<{start}>" if sec.group(2) else ""
                            data = data[start:start + int(sec.group(3))] if sec.group(3) else data
                            self._w(f"* {uid} FETCH (UID {uid} BODY[{sec.group(1)}]{window} {{{len(data)}}}\r\n".encode() + data + b")\r\n" + tag.encode() + b" OK done\r\n")
                    else:
                        body = msg["raw"]
                        self._w(f"* {uid} FETCH (UID {uid} RFC822 {{{len(body)}}}\r\n".encode() + body + b")\r\n" + tag.encode() + b" OK done\r\n")
                elif sub == "STORE":
                    uid = int(a2.split(" ", 1)[0])
                    with self.store.lock:
                        for m in self.store.inbox:
                            if m["uid"] == uid and "\\Seen" in a2:
                                m["seen"] = True
                    self._w(f"* {uid} FETCH (FLAGS (\\Seen))\r\n{tag} OK done\r\n".encode())
                else:
                    self._w(tag.encode() + b" BAD unsupported\r\n")
            elif cmd == "NOOP":
                self._w(tag.encode() + b" OK done\r\n")
            elif cmd == "LOGOUT":
                self._w(b"* BYE\r\n" + tag.encode() + b" OK bye\r\n")
                return
            else:
                self._w(tag.encode() + b" BAD unsupported\r\n")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeMailServers:
    """Starts both servers on free local ports. Use as a context manager."""

    def __init__(self, store: MailStore | None = None, host: str = "127.0.0.1", smtp_port: int = 0, imap_port: int = 0):
        self.store = store or MailStore()
        smtp_h = type("SMTPH", (_SMTP,), {"store": self.store})
        imap_h = type("IMAPH", (_IMAP,), {"store": self.store})
        self._smtp, self._imap = _Server((host, smtp_port), smtp_h), _Server((host, imap_port), imap_h)
        self.host = host
        self.smtp_port, self.imap_port = self._smtp.server_address[1], self._imap.server_address[1]
        self._threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self._smtp, self._imap)]

    def __enter__(self) -> FakeMailServers:
        for t in self._threads:
            t.start()
        return self

    def __exit__(self, *exc) -> None:
        for s in (self._smtp, self._imap):
            s.shutdown()
            s.server_close()


if __name__ == "__main__":  # python -m mock.mail_servers
    with FakeMailServers(smtp_port=2525, imap_port=1143) as srv:
        print(f"fake smtp on {srv.smtp_port}, fake imap on {srv.imap_port} (user demo, password demo-password). Ctrl C to stop.")
        threading.Event().wait()
