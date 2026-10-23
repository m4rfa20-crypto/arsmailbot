"""Exact direct-parent resolution; never match unrelated messages by subject."""
import base64
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime


def message_ids(value):
    return re.findall(r'<[^<>\s]+>', str(value or ''))


def parent_id(msg):
    direct = message_ids(msg.get('In-Reply-To'))
    if direct:
        return direct[0] if len(direct) == 1 else None
    refs = message_ids(msg.get('References'))
    return refs[-1] if refs else None


def index_messages(messages):
    index = {}
    for msg in messages:
        ids = message_ids(msg.get('Message-ID'))
        if len(ids) == 1:
            variants = index.setdefault(ids[0], {})
            variants[msg.as_bytes()] = msg
    return index


def inherited_parts(msg, index, allowed_senders=()):
    key = parent_id(msg)
    candidates = list(index.get(key, {}).values())
    if len(candidates) != 1:
        return [], None, 'Исходное письмо не найдено или неоднозначно'
    parent = candidates[0]
    try:
        then = parsedate_to_datetime(str(parent['Date']))
        now = parsedate_to_datetime(str(msg['Date']))
        if not then.tzinfo or not now.tzinfo or then > now:
            raise ValueError('invalid order')
    except (ValueError, TypeError):
        return [], None, 'Не подтверждён порядок писем'
    sender = parseaddr(str(parent.get('From', '')))[1].lower()
    if allowed_senders and sender not in {x.lower() for x in allowed_senders}:
        return [], None, 'Отправитель исходного письма требует проверки'
    parts = [p for p in parent.iter_attachments() if p.get_filename()]
    if not parts:
        return [], None, 'В исходном письме нет вложений'
    return parts, parent, None


def mailbox_wire(name):
    """RFC 3501 modified UTF-7, quoted mailbox argument."""
    def encode(match):
        return '&' + base64.b64encode(match.group().encode('utf-16-be')).decode().rstrip('=').replace('/', ',') + '-'
    wire = re.sub(r'[^\x20-\x7e]+', encode, name.replace('&', '&-'))
    return '"' + wire.replace('\\', '\\\\').replace('"', '\\"') + '"'


def fetch_uid(client, uid):
    status, payload = client.uid('fetch', uid, '(BODY.PEEK[])')
    if status != 'OK':
        raise RuntimeError('IMAP fetch failed')
    raw = next((p[1] for p in payload if isinstance(p, tuple)), None)
    if raw is None:
        raise RuntimeError('IMAP empty message')
    return raw


def fetch_parent_messages(client, raw_messages, folders):
    """Fetch direct parents outside date bounds from explicitly configured folders."""
    parsed = [BytesParser(policy=policy.default).parsebytes(raw) for raw in raw_messages]
    known = index_messages(parsed)
    needed = {parent_id(m) for m in parsed if not any(p.get_filename() for p in m.iter_attachments())}
    needed.discard(None)
    found = []
    for folder in folders:
        status, _ = client.select(mailbox_wire(folder), readonly=True)
        if status != 'OK':
            raise RuntimeError('IMAP parent folder unavailable')
        for mid in sorted(needed):
            # Search is substring-based; exact Message-ID is checked after fetching.
            quoted = '"' + mid.replace('\\', '\\\\').replace('"', '\\"') + '"'
            status, data = client.uid('search', None, 'HEADER', 'Message-ID', quoted)
            if status != 'OK':
                raise RuntimeError('IMAP parent search failed')
            for uid in data[0].split():
                raw = fetch_uid(client, uid)
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                if message_ids(msg.get('Message-ID')) == [mid]:
                    if msg.as_bytes() not in known.get(mid, {}):
                        found.append(raw)
                        known.setdefault(mid, {})[msg.as_bytes()] = msg
    return found
