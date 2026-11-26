"""A mailbox over IMAP (read unseen mail) and a sender over SMTP. Works with Gmail (app password), Outlook and most hosts.

Both connections verify the server's certificate and host name. A plain (unencrypted) connection is accepted only to a mail server on this same
computer. Mail is read with PEEK, so reading never changes a flag.

What is new is decided by the message's UID, not by the unread flag. A person who opens a customer's mail on a phone before the next run does not hide it from the desk, and a mailbox
that refuses to mark mail read (read only credentials) cannot make the same 50 messages come back and starve the new ones. `fetch_since` takes the last UID the desk stored (and the mailbox's
UIDVALIDITY, which changes when a mailbox is renumbered) and reads what came after it. The very first run reads the mail that is unread right now and nothing older.
`mark_seen` is still called after the messages are stored, so a person can see in the mailbox what the desk has taken in.

A message over MAX_MESSAGE_BYTES is not downloaded whole. Its structure (BODYSTRUCTURE) is read, and only its plain or HTML text part is fetched, so a mail that is large because of a
screenshot or a PDF still becomes a normal ticket. A message whose text cannot be read that way, or cannot be read at all, becomes a stub ticket that needs a person (intake.py), so no mail
disappears without a trace.
"""

from __future__ import annotations

import email
import imaplib
import ipaddress
import itertools
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import make_msgid, parseaddr

from deskfern.adapters.base import Batch, DeliveryUnknown, InboundTicket
from deskfern.util import clean_header
from sendora_core.adapters import AdapterError
from sendora_core.safety import write_action

log = logging.getLogger("deskfern.imap")
RESULT = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s*=\s*([A-Za-z]+)")
MAX_BODY = 20000
MAX_PER_RUN = 50  # messages taken in one run; the rest are read in the next run, so a flooded inbox never fills memory or the database in one go
MAX_EXAMINED_PER_RUN = 300  # messages looked at in one run (their size is asked for first), so a long run of oversize mail cannot hold up the small ones for ever
MAX_MESSAGE_BYTES = 2_000_000  # a message larger than this is not downloaded (its size is asked for first); it stays unread in the inbox for a person
_SIZE = re.compile(rb"RFC822\.SIZE\s+(\d+)", re.I)
_TOKEN = re.compile(rb'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|(NIL\b)|(\d+)|([^\s()"]+))', re.I)
MAX_HEADER_BYTES = 65_536  # the header block of a large message is asked for with this ceiling


def is_loopback(host: str) -> bool:
    if (host or "").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address((host or "").strip("[]")).is_loopback
    except ValueError:
        return False


def sender_is_authenticated(msg, authserv_id: str) -> bool:
    """True only when the mail provider you named says the sender's domain passed DMARC.

    A sender can type any header into a message, including a forged Authentication-Results. So only the TOP header counts (a receiving server
    adds its own above everything that came with the message), and only when it starts with the name you set in INBOUND_AUTHSERV_ID.
    With no name set nothing is ever verified."""
    wanted = (authserv_id or "").strip().lower()
    headers = msg.get_all("Authentication-Results") or []
    if not wanted or not headers:
        return False
    parts = [p.strip() for p in str(headers[0]).split(";")]
    first = parts[0].split()
    if not first or first[0].lower() != wanted:
        return False
    results: dict[str, str] = {}
    for part in parts[1:]:
        m = RESULT.match(part)
        if m:
            results.setdefault(m.group(1).lower(), m.group(2).lower())
    return results.get("dmarc") == "pass"


def _content(part) -> str:
    try:
        return part.get_content()
    except (LookupError, ValueError):  # an unknown or broken charset: read the bytes and replace what cannot be read
        raw = part.get_payload(decode=True) or b""
        return raw.decode("utf-8", errors="replace")


def _plain_from_html(html: str) -> str:
    from deskfern.crawler import parse_html

    return parse_html(html)[1]


def _text_of(msg) -> str:
    """The words of a message: its plain text part, or, for a mail that only has an HTML part, that part with the tags taken out (scripts and styles dropped)."""
    plain = msg.get_body(preferencelist=("plain",))
    text = _content(plain) if plain is not None else ""
    if not text.strip():
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            text = _plain_from_html(_content(html))
    return text[:MAX_BODY]


def _whole(v) -> int:
    """A saved cursor number, or 0 when what was saved is not a number (a damaged row): the mailbox is then read as on a first run instead of failing every run."""
    try:
        return max(int(v or 0), 0)
    except (TypeError, ValueError):
        return 0


def _uidvalidity(c) -> str:
    """The mailbox's UIDVALIDITY as text (it changes when the mailbox is renumbered), or "" when the server did not say."""
    _, data = c.response("UIDVALIDITY")
    return (data[0] or b"").decode(errors="replace").strip() if data and data[0] else ""


def _highest_uid(c) -> int:
    _, data = c.uid("SEARCH", None, "ALL")
    ids = [int(x) for x in (data[0] or b"").split()]
    return max(ids) if ids else 0


def _size_of(c, uid: str) -> int | None:
    try:
        _, parts = c.uid("FETCH", uid, "(RFC822.SIZE)")
    except imaplib.IMAP4.error:
        return None
    for p in parts or []:
        m = _SIZE.search(p if isinstance(p, bytes) else (p[0] if p else b""))
        if m:
            return int(m.group(1))
    return None


def _parse_list(raw: bytes, after: bytes = b"") -> list:
    """The first parenthesised list in an IMAP answer (after the word `after`, when given) as nested Python lists (strings, numbers, None for NIL). Raises ValueError for anything unbalanced."""
    cut = raw.upper().find(after.upper()) if after else 0
    start = raw.find(b"(", max(cut, 0))
    if cut < 0 or start < 0:
        raise ValueError("no list")
    stack: list[list] = []
    pos = start
    while pos < len(raw):
        m = _TOKEN.match(raw, pos)
        if m is None:
            break
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            if not stack:
                return done
            stack[-1].append(done)
        elif stack:
            if m.group(3) is not None:
                stack[-1].append(m.group(3).decode("utf-8", "replace"))
            elif m.group(4):
                stack[-1].append(None)
            elif m.group(5):
                stack[-1].append(int(m.group(5)))
            else:
                stack[-1].append(m.group(6).decode("utf-8", "replace"))
    raise ValueError("unbalanced list")


def text_parts(structure) -> dict[str, tuple[str, int | None]]:
    """Where the readable text of a multipart message is: {"plain": (section, size), "html": (section, size)}, the first of each that is not an attachment.
    A message that is a single part has none (its whole body is the text, and that is what made it too large)."""
    found: dict[str, tuple[str, int | None]] = {}

    def walk(node, section: str) -> None:
        if not isinstance(node, list) or not node:
            return
        if isinstance(node[0], list):  # multipart: its parts, then its subtype
            for n, child in enumerate(itertools.takewhile(lambda c: isinstance(c, list), node), start=1):  # the parts come first; the subtype and the extension data after them are not parts
                walk(child, f"{section}.{n}" if section else str(n))
            return
        if len(node) < 7 or not section:
            return
        kind, sub = str(node[0]).lower(), str(node[1]).lower()
        disposition = node[9] if len(node) > 9 else None
        if kind != "text" or sub not in ("plain", "html") or (isinstance(disposition, list) and disposition and str(disposition[0]).lower() == "attachment"):
            return
        found.setdefault(sub, (section, node[6] if isinstance(node[6], int) else None))

    walk(structure, "")
    return found


def _literal(parts) -> bytes:
    """The bytes of the first literal in a FETCH answer. An answer with no literal (an empty part) is empty."""
    for p in parts or []:
        if isinstance(p, tuple) and len(p) >= 2:
            return p[1] or b""
    return b""


class _Base:
    def __init__(self, settings):
        self.settings = settings

    def _need(self, *names: str) -> None:
        missing = [n.upper() for n in names if not getattr(self.settings, n)]
        if missing:
            raise AdapterError(f"Set {', '.join(missing)} in your environment first (see docs/04-ENVIRONMENT-VARIABLES.md).")

    def _smtp(self) -> smtplib.SMTP:
        """A verified connection: the certificate and the host name are checked. Plain text is allowed only to this same computer."""
        s = self.settings
        ctx = ssl.create_default_context()
        if s.smtp_port == 465:
            c = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ctx)
        else:
            if not s.smtp_use_tls and not is_loopback(s.smtp_host):
                raise AdapterError("SMTP_USE_TLS=false is only allowed for a mail server on this same computer (127.0.0.1). Turn it back on.")
            c = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            if s.smtp_use_tls:
                try:
                    c.starttls(context=ctx)
                except Exception:
                    c.close()
                    raise
        if s.smtp_user:
            c.login(s.smtp_user, s.smtp_password)
        return c

    def _imap(self) -> imaplib.IMAP4:
        s = self.settings
        if s.imap_use_ssl:
            c = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ssl.create_default_context(), timeout=20)
        elif is_loopback(s.imap_host):
            c = imaplib.IMAP4(s.imap_host, s.imap_port, timeout=20)
        else:
            raise AdapterError("IMAP_USE_SSL=false is only allowed for a mail server on this same computer (127.0.0.1). Turn it back on.")
        c.login(s.smtp_user, s.smtp_password)
        return c


def _imap_failure(exc: Exception, host: str) -> AdapterError:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return AdapterError(f"The certificate of {host} could not be verified, so the inbox was not read. Check IMAP_HOST and the port.")
    if isinstance(exc, imaplib.IMAP4.error):
        return AdapterError("The inbox rejected the login or the command. Check the user name and app password.")
    return AdapterError(f"Could not reach the inbox at {host}: {type(exc).__name__}.")


class ImapInbox(_Base):
    cursor_key = "imap_cursor"  # the State row the desk saves this mailbox's cursor in

    validity = ""  # the mailbox's UIDVALIDITY of the run in progress, part of the id of a message that has no Message-ID

    def _ticket_of(self, msg, text: str, uid: str) -> InboundTicket:
        sender = parseaddr(str(msg["From"] or ""))
        return InboundTicket(f"em-{clean_header(str(msg['Message-ID'] or uid))}", "email", sender[1].lower(), clean_header(str(msg["Subject"] or "(no subject)")), text, sender[0],
                             verified=sender_is_authenticated(msg, self.settings.inbound_authserv_id), ack=uid)

    def _read_one(self, c, uid: str, salvage: bool = True) -> InboundTicket | None:
        """One message as a ticket. A message over the size limit is not downloaded: its text part alone is read (`salvage`), or, when that is not possible, a stub is returned
        (`InboundTicket.stub` is set) so a person is told. Without `salvage` it is None. Raises for a message that cannot be read."""
        size = _size_of(c, uid)
        if size is not None and size > MAX_MESSAGE_BYTES:
            log.warning("did not download one message because it is larger than %d bytes (uid %s)", MAX_MESSAGE_BYTES, uid)
            return self._salvage(c, uid) if salvage else None
        _, parts = c.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: reading does not mark the mail as read
        if not parts or not parts[0] or len(parts[0][1]) > MAX_MESSAGE_BYTES:
            return None
        msg = email.message_from_bytes(parts[0][1], policy=default_policy)
        return self._ticket_of(msg, _text_of(msg), uid)

    def _headers(self, c, uid: str):
        """The header lines of a message (a window of MAX_HEADER_BYTES), or None when they cannot be read."""
        try:
            _, parts = c.uid("FETCH", uid, f"(BODY.PEEK[HEADER]<0.{MAX_HEADER_BYTES}>)")
            raw = _literal(parts)
            return email.message_from_bytes(raw, policy=default_policy) if raw else None
        except Exception as exc:  # a stub is better than nothing, and the header is only a nicety for it
            log.warning("could not read the header of a message (uid %s): %s", uid, type(exc).__name__)
            return None

    def _salvage(self, c, uid: str) -> InboundTicket:
        """A message that is too large to download whole: the ticket from its text part alone, or a stub for a person."""
        msg = self._headers(c, uid)
        try:
            _, parts = c.uid("FETCH", uid, "(BODYSTRUCTURE)")
            wanted = text_parts(_parse_list(b" ".join(p if isinstance(p, bytes) else b"" for p in parts or []), after=b"BODYSTRUCTURE"))
            if msg is not None and wanted:
                return self._ticket_of(msg, self._text_from_sections(c, uid, wanted), uid)
        except Exception as exc:
            log.warning("could not read the text part of a large message (uid %s): %s", uid, type(exc).__name__)
        return self.stub(c, uid, "too_large", msg)

    def _text_from_sections(self, c, uid: str, wanted: dict) -> str:
        """The words of a large message from its plain part (or its HTML part with the tags taken out). Each part is fetched alone and only when it is within the size limit."""
        text = ""
        for kind in ("plain", "html"):
            section, size = wanted.get(kind, ("", None))
            if not section or size is None or size > MAX_MESSAGE_BYTES:
                continue
            _, head = c.uid("FETCH", uid, f"(BODY.PEEK[{section}.MIME])")
            _, body = c.uid("FETCH", uid, f"(BODY.PEEK[{section}])")
            raw_body = _literal(body)
            if len(raw_body) > MAX_MESSAGE_BYTES:
                continue
            part = email.message_from_bytes(_literal(head) + raw_body, policy=default_policy)
            text = _content(part) if kind == "plain" else _plain_from_html(_content(part))
            if text.strip():
                break
        if not text.strip():
            raise ValueError("no readable text part")
        return text[:MAX_BODY]

    def stub(self, c, uid: str, kind: str, msg=None) -> InboundTicket:
        """A message the desk could not read, as a ticket for a person (see intake.py). The sender and subject are used when the header could be read."""
        msg = msg if msg is not None else self._headers(c, uid)
        sender = parseaddr(str(msg["From"] or "")) if msg is not None else ("", "")
        mid = clean_header(str(msg["Message-ID"] or "")) if msg is not None else ""
        subject = clean_header(str(msg["Subject"] or "")) if msg is not None else ""
        return InboundTicket(f"em-{mid}" if mid else f"em-uid-{self.validity or 'x'}-{uid}", "email", sender[1].lower(), subject, "", sender[0], ack=uid, stub=kind)

    def _connect(self):
        self._need("imap_host", "smtp_user")
        c = self._imap()
        c.select("INBOX")
        return c

    @staticmethod
    def _logout(c) -> None:
        try:
            c.logout()
        except Exception:
            pass

    def fetch_tickets(self) -> list[InboundTicket]:
        """Unread mail, left unread (the older, stateless way: the desk itself uses `fetch_since`). Each ticket carries its IMAP uid in `ack`."""
        out: list[InboundTicket] = []
        try:
            c = self._connect()
            try:
                _, data = c.uid("SEARCH", None, "UNSEEN")
                for uid in (data[0] or b"").split():
                    if len(out) >= MAX_PER_RUN:
                        break  # the rest wait for the next run
                    try:
                        t = self._read_one(c, uid.decode(), salvage=False)
                    except Exception as exc:  # one unreadable message must not hide the rest
                        log.warning("skipped one unreadable message (uid %s): %s", uid.decode(errors="replace"), type(exc).__name__)
                        continue
                    if t is not None:
                        out.append(t)
            finally:
                self._logout(c)
        except AdapterError:
            raise
        except (imaplib.IMAP4.error, ssl.SSLError, OSError) as exc:
            raise _imap_failure(exc, self.settings.imap_host) from exc
        return out

    def fetch_since(self, cursor: dict) -> Batch:
        """The mail that came after the last message the desk stored. `cursor` is {"validity", "last_uid", "boot_until"} as returned by the run before, or empty on the first run.

        First run (or the mailbox was renumbered, its UIDVALIDITY changed): the mail that is unread right now is read, up to the highest UID the mailbox has at that moment,
        and nothing older. After that every message with a higher UID than the last one looked at is read, read or unread, so mail a person opened first is not lost. A message that
        cannot be read, or is too large (and has no readable text part), comes back as a stub ticket (see `stub`) and the cursor moves past it, so it can never block the mail behind it."""
        out = Batch()
        try:
            c = self._connect()
            try:
                validity = self.validity = _uidvalidity(c)
                if str(cursor.get("validity", "")) != validity:
                    if cursor.get("validity"):
                        log.warning("the mailbox was renumbered (UIDVALIDITY changed); reading its unread mail again. Mail already stored is recognised and not made into a second ticket.")
                    cursor = {"validity": validity, "last_uid": 0, "boot_until": _highest_uid(c)}
                last, until = _whole(cursor.get("last_uid")), _whole(cursor.get("boot_until"))
                criteria = f"UNSEEN UID {last + 1}:{until}" if last < until else f"UID {last + 1}:*"
                _, data = c.uid("SEARCH", None, criteria)
                uids = sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > last)  # "n:*" always includes the newest message, even when it is older than n
                examined = 0
                for uid in uids:
                    if len(out.tickets) >= MAX_PER_RUN or examined >= MAX_EXAMINED_PER_RUN:
                        break
                    examined += 1
                    last = uid
                    try:
                        t = self._read_one(c, str(uid))
                    except Exception as exc:
                        log.warning("could not read one message (uid %s): %s", uid, type(exc).__name__)
                        t = None
                    out.tickets.append(t or self.stub(c, str(uid), "unreadable"))  # never dropped: a message the desk cannot read becomes a ticket that needs a person
                else:
                    if last < until:
                        last = until  # nothing unread is left in the range that was already in the mailbox on the first run
                out.cursor = {"validity": validity, "last_uid": last, "boot_until": until}
            finally:
                self._logout(c)
        except AdapterError:
            raise
        except (imaplib.IMAP4.error, ssl.SSLError, OSError) as exc:
            raise _imap_failure(exc, self.settings.imap_host) from exc
        return out

    def mark_seen(self, acks: list[str]) -> None:
        """Mark these messages read. Called only after they were stored. A failure here is harmless: the mail comes again and is recognised."""
        if not acks:
            return
        self._need("imap_host", "smtp_user")
        try:
            c = self._imap()
            try:
                c.select("INBOX")
                for uid in acks:
                    c.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            finally:
                try:
                    c.logout()
                except Exception:
                    pass
        except AdapterError:
            raise
        except (imaplib.IMAP4.error, ssl.SSLError, OSError) as exc:
            raise _imap_failure(exc, self.settings.imap_host) from exc

    def healthcheck(self) -> tuple[bool, str]:
        try:
            self._need("imap_host", "smtp_user")
            self._imap().logout()
            return True, "Connected to the inbox."
        except AdapterError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Could not connect: {type(exc).__name__}. Check host, port and password."


class SmtpSender(_Base):
    @write_action("email.send")
    def send(self, *, from_addr: str, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
        self._need("smtp_host")
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = clean_header(from_addr), clean_header(to), clean_header(subject)
        m["Message-ID"] = make_msgid(domain=from_addr.rsplit("@", 1)[-1] or "localhost")
        ref = clean_header(in_reply_to)
        if re.fullmatch(r"<[^<>\s]+>", ref):  # only a real looking Message-ID, so a reply stays in the customer's thread
            m["In-Reply-To"] = m["References"] = ref
        m.set_content(body)
        host = self.settings.smtp_host
        try:
            c = self._smtp()  # connect, check the certificate, sign in: nothing has been sent yet, so any failure here is safe to try again
        except smtplib.SMTPAuthenticationError as exc:
            raise AdapterError("The mail server rejected the user name or password. For Gmail use an app password.") from exc
        except ssl.SSLCertVerificationError as exc:
            raise AdapterError(f"The certificate of {host} could not be verified, so nothing was sent. Check SMTP_HOST and the port.") from exc
        except AdapterError:
            raise
        except (smtplib.SMTPException, OSError) as exc:
            raise AdapterError(f"Could not send through {host}: {type(exc).__name__}.") from exc
        try:
            with c:
                c.send_message(m)
        except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError, ssl.SSLError) as exc:  # the line dropped or went quiet after the message may have been handed over
            raise DeliveryUnknown(f"The connection to {host} ended before it confirmed the message ({type(exc).__name__}). It may or may not have been delivered.") from exc
        except smtplib.SMTPException as exc:  # the server answered with a refusal: it did not take the message
            raise AdapterError(f"Could not send through {host}: {type(exc).__name__}.") from exc
        except OSError as exc:
            raise DeliveryUnknown(f"The connection to {host} failed while sending ({type(exc).__name__}). It may or may not have been delivered.") from exc
        return m["Message-ID"]

    def healthcheck(self) -> tuple[bool, str]:
        try:
            self._need("smtp_host")
            with self._smtp() as c:
                c.noop()
            return True, "Connected to the mail server."
        except AdapterError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Could not connect: {type(exc).__name__}."
