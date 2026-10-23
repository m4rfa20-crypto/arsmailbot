"""НОВЬ: deterministic approval-report prototype, Python 3.12+."""
import argparse
import hashlib
import imaplib
import io
import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import http_client as requests
from openpyxl import load_workbook

from threading_mail import index_messages, inherited_parts, mailbox_wire, fetch_uid, fetch_parent_messages

MSK = ZoneInfo('Europe/Moscow')


def fresh_text(msg):
    part = msg.get_body(preferencelist=('plain', 'html'))
    if not part:
        return ''
    body = part.get_content()
    if part.get_content_type() == 'text/html':
        # HTML-only messages require review in v0.1: don't risk quoted approvals.
        return ''
    kept = []
    for line in body.splitlines():
        if re.match(r'^\s*(>|От:|From:|On .+wrote:|.*писал\(а\):|[-_]{3,})', line, re.I):
            break
        kept.append(line)
    return '\n'.join(kept).strip()


def approved(text):
    # Fail closed on questions, conditions, negation or discussion of changes.
    if re.search(r'\?|\bне\b|\bесли\b|\bпосле\b|\bно\b|замечан|исправ|доработ|предваритель|отмен|отозв|частич', text, re.I):
        return False
    return bool(re.search(r'(?im)^\s*(?:да[,!]?\s*)?(?:(?:акт|смета)(?:\s*№?\s*\d+)?\s+согласован[ао]?|согласовано|согласовываю|утверждаю)(?:[.!\s]|$)', text))


def identify(filename):
    stem = Path(filename).stem
    num = re.match(r'^\s*(\d{1,6})(?:[\s._-]|$)', stem)
    if not num:
        num = re.search(r'(?:смета|акт)\s*№?\s*(\d{1,6})\b', stem, re.I)
    if not num:
        return None, None
    kind = 'акт' if re.search(r'\bакт\b', stem, re.I) else 'смета'
    # Numeric-name xlsx is estimate by configured project convention.
    return kind, num.group(1)


def amount(data, filename, rule):
    if not rule or not filename.lower().endswith('.xlsx'):
        return None
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    try:
        value = wb[rule['sheet']][rule['cell']].value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None  # Formula without cached result: never evaluate/guess.
        result = Decimal(str(value))
        return str(result.quantize(Decimal('.01'))) if result.is_finite() and result >= 0 else None
    finally:
        wb.close()


def scan(raw_messages, cfg, start, end, output):
    rows, review, hashes = [], [], set()
    messages = [BytesParser(policy=policy.default).parsebytes(raw) for raw in raw_messages]
    index = index_messages(messages)
    for msg in messages:
        if parseaddr(str(msg.get('From', '')))[1].lower() != cfg['sender'].lower():
            continue
        try:
            timestamp = parsedate_to_datetime(str(msg['Date']))
            if timestamp.tzinfo is None:
                raise ValueError('missing timezone')
            day = timestamp.astimezone(MSK).date()
        except (TypeError, ValueError):
            review.append({'reason': 'Некорректная дата письма', 'subject': str(msg.get('Subject', ''))})
            continue
        if not start <= day <= end:
            continue
        text = fresh_text(msg)
        subject = str(msg.get('Subject', ''))
        if not cfg.get('project_mailbox') and not re.search(r'\bнов[ьи]\b', subject + '\n' + text, re.I):
            continue
        if not approved(text):
            review.append({'reason': 'Нет однозначного согласования', 'subject': subject})
            continue
        attachments = [p for p in msg.iter_attachments() if p.get_filename()]
        source = msg
        if not attachments:
            attachments, source, error = inherited_parts(msg, index, cfg.get('attachment_senders', []))
            if error:
                review.append({'reason': error, 'subject': subject})
                continue
            # Fallback is limited to acts, as requested. Ignore inline signature images.
            candidates = [p for p in attachments if p.get_filename().lower().endswith('.xlsx')
                          and identify(p.get_filename())[0] == 'акт']
            requested = re.findall(r'акт\s*№?\s*(\d+)\b', text, re.I)
            if requested:
                candidates = [p for p in candidates if identify(p.get_filename())[1] in requested]
            if len(candidates) != 1:
                review.append({'reason': 'Нельзя однозначно выбрать акт из исходного письма', 'subject': subject})
                continue
            attachments = candidates
        for part in attachments:
            name = Path(part.get_filename().replace('\\', '/')).name
            data = part.get_payload(decode=True)
            if not data or len(data) > 20 * 1024 * 1024 or not name.lower().endswith('.xlsx'):
                review.append({'reason': 'Формат или размер требует проверки', 'file': name})
                continue
            sha = hashlib.sha256(data).hexdigest()
            if sha in hashes:
                continue
            hashes.add(sha)
            kind, number = identify(name)
            if not number:
                review.append({'reason': 'Не определён номер', 'file': name})
                continue
            key = kind + ':' + number
            try:
                total = amount(data, name, cfg.get('amount_cells', {}).get(key))
            except Exception:
                total = None
            local = output / 'attachments' / sha / name
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            mid = str(msg.get('Message-ID', ''))
            rows.append(dict(kind=kind, number=number, amount=total, sha256=sha, file=name,
                             local=str(local), subject=subject, date=str(day), message_id=mid,
                             attachment_source_message_id=str(source.get('Message-ID', '')),
                             attachment_source_sender=parseaddr(str(source.get('From', '')))[1],
                             attachment_source_subject=str(source.get('Subject', '')),
                             inherited=source is not msg,
                             mail_search='https://mail.yandex.ru/#search?request=' + quote(subject),
                             disk_folder=cfg.get('disk_folders', {}).get(key), conflict=False))
    groups = {}
    for row in rows:
        groups.setdefault((row['kind'], row['number']), []).append(row)
    for group in groups.values():
        if len(group) > 1:
            for row in group:
                row['conflict'] = True
            review.append({'reason': 'Несколько версий документа; исключены из итога',
                           'document': group[0]['kind'] + ' ' + group[0]['number']})
    return {'project': 'НОВЬ', 'start': str(start), 'end': str(end), 'documents': rows, 'review': review}


def render(report):
    lines = [f"НОВЬ · {report['start']} — {report['end']}"]
    for kind in ('смета', 'акт'):
        rows = [r for r in report['documents'] if r['kind'] == kind and not r['conflict']]
        known = [r for r in rows if r['amount'] is not None]
        total = sum((Decimal(r['amount']) for r in known), Decimal(0))
        lines.append(f"{kind.capitalize()}: {len(rows)} шт. Известная сумма: {total:,.2f} ₽; без суммы: {len(rows)-len(known)}")
    for r in report['documents']:
        value = 'КОНФЛИКТ ВЕРСИЙ' if r['conflict'] else (r['amount'] + ' ₽' if r['amount'] else 'сумма требует проверки')
        lines.extend([f"{r['kind']} №{r['number']}: {value}", 'Поиск письма: ' + r['mail_search']])
        if r.get('inherited'):
            lines.append('Акт взят из письма, на которое дано согласование: ' + r['attachment_source_sender'])
        if r.get('disk_link'):
            lines.append('Файл: ' + r['disk_link'])
    lines.append(f"На проверку: {len(report['review'])}. Согласование не означает оплату.")
    return '\n'.join(lines)


def imap_messages(cfg, start, end):
    months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    def fmt(d):
        return f'{d.day:02d}-{months[d.month-1]}-{d.year}'
    with imaplib.IMAP4_SSL(os.getenv('IMAP_HOST', 'imap.yandex.ru'), 993, timeout=45) as client:
        client.login(os.environ['IMAP_USER'], os.environ['IMAP_PASSWORD'])
        status, _ = client.select(mailbox_wire(cfg['mailbox']), readonly=True)
        if status != 'OK':
            raise RuntimeError('Не удалось открыть IMAP-папку')
        # Search SENT date with one-day padding; exact Moscow bounds checked locally.
        status, data = client.uid('search', None, 'SENTSINCE', fmt(start-timedelta(days=1)),
                                  'SENTBEFORE', fmt(end+timedelta(days=2)))
        if status != 'OK':
            raise RuntimeError('IMAP search failed')
        messages = [fetch_uid(client, uid) for uid in data[0].split()]
        parents = fetch_parent_messages(client, messages, cfg.get('parent_mailboxes', [cfg['mailbox']]))
        yield from messages
        yield from parents


def disk_upload(row):
    base = 'https://cloud-api.yandex.net/v1/disk/resources'
    headers = {'Authorization': 'OAuth ' + os.environ['YANDEX_DISK_TOKEN']}
    # Only configured, existing destinations. Never infer folder numbering conventions.
    path = row['disk_folder'].rstrip('/') + '/' + row['file']
    params = {'path': path}
    r = requests.get(base, headers=headers, params=params, timeout=45)
    if r.status_code == 200:
        expected = hashlib.md5(Path(row['local']).read_bytes()).hexdigest()
        if r.json().get('md5') != expected:
            raise RuntimeError('На Диске уже есть другая версия: ' + row['file'])
    elif r.status_code == 404:
        r = requests.get(base + '/upload', headers=headers,
                         params={**params, 'overwrite': 'false'}, timeout=45)
        r.raise_for_status()
        with open(row['local'], 'rb') as f:
            uploaded = requests.put(r.json()['href'], data=f, timeout=120)
        uploaded.raise_for_status()
        check = requests.get(base, headers=headers, params=params, timeout=45)
        check.raise_for_status()
        if check.json().get('md5') != hashlib.md5(Path(row['local']).read_bytes()).hexdigest():
            raise RuntimeError('Загрузка пока не подтверждена; повторите проверку')
    else:
        r.raise_for_status()
    return 'https://disk.yandex.ru/client/disk' + quote('/' + path.removeprefix('disk:/').lstrip('/'))


def publish(report, dbpath, upload=True):
    db = sqlite3.connect(dbpath)
    db.execute('CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY, status TEXT)')
    for row in report['documents']:
        if not upload or row['conflict'] or not row['disk_folder']:
            continue
        row['disk_link'] = disk_upload(row)
    content = render(report)
    # Stable report key, excludes local paths. Pending delivery is never automatically retried.
    key = hashlib.sha256(content.encode()).hexdigest()
    if db.execute('SELECT status FROM deliveries WHERE key=?', (key,)).fetchone():
        raise RuntimeError('Этот отчёт уже отправлялся или ожидает ручной проверки доставки')
    chunks = []
    for line in content.splitlines():
        if len(line) > 3000:
            raise RuntimeError('Слишком длинная строка отчёта')
        if not chunks or len(chunks[-1]) + len(line) + 1 > 3000:
            chunks.append(line)
        else:
            chunks[-1] += '\n' + line
    db.execute('INSERT INTO deliveries VALUES (?, ?)', (key, 'pending'))
    db.commit()
    for chunk in chunks:
        r = requests.post('https://api.telegram.org/bot' + os.environ['TELEGRAM_TOKEN'] + '/sendMessage',
                          json={'chat_id': os.environ['TELEGRAM_CHAT_ID'],
                                'message_thread_id': int(os.environ['TELEGRAM_THREAD_ID']),
                                'text': chunk}, timeout=45)
        if r.status_code != 200 or not r.json().get('ok'):
            raise RuntimeError('Telegram delivery failed; check pending delivery manually')
    db.execute('UPDATE deliveries SET status=? WHERE key=?', ('sent', key))
    db.commit()
    db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='config.json')
    p.add_argument('--start', required=True, type=date.fromisoformat)
    p.add_argument('--end', required=True, type=date.fromisoformat)
    p.add_argument('--eml-dir', type=Path)
    p.add_argument('--output', type=Path, default=Path('output'))
    p.add_argument('--publish', action='store_true')
    p.add_argument('--no-disk', action='store_true')
    a = p.parse_args()
    if a.start > a.end:
        p.error('start > end')
    cfg = json.loads(os.environ['BOT_CONFIG_JSON']) if os.getenv('BOT_CONFIG_JSON') else json.loads(Path(a.config).read_text())
    a.output.mkdir(parents=True, exist_ok=True)
    messages = (f.read_bytes() for f in sorted(a.eml_dir.glob('*.eml'))) if a.eml_dir else imap_messages(cfg, a.start, a.end)
    report = scan(messages, cfg, a.start, a.end, a.output)
    if a.publish:
        publish(report, a.output / 'state.sqlite3', upload=not a.no_disk)
    (a.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (a.output / 'report.txt').write_text(render(report))
    print('Отчёт готов: ' + str(a.output / 'report.txt'))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # requests exception strings may contain bot tokens: don't log them.
        print('Ошибка выполнения (' + type(exc).__name__ + '). Проверьте настройки и статус доставки.')
        raise SystemExit(1)
