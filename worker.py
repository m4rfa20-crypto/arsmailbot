"""Telegram-controlled Railway worker. Only configured user/chat/topic may run it."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import http_client as http


def authorized(message):
    return (str(message.get('from', {}).get('id')) == os.environ['TELEGRAM_OWNER_ID']
            and str(message.get('chat', {}).get('id')) == os.environ['TELEGRAM_CHAT_ID']
            and str(message.get('message_thread_id', '')) == os.environ['TELEGRAM_THREAD_ID'])


def main():
    for key in ('TELEGRAM_TOKEN', 'TELEGRAM_OWNER_ID', 'TELEGRAM_CHAT_ID', 'TELEGRAM_THREAD_ID',
                'IMAP_USER', 'IMAP_PASSWORD', 'BOT_CONFIG_JSON'):
        if not os.getenv(key):
            raise ValueError('Missing variable: ' + key)
    root = Path(os.getenv('OUTPUT_DIR', '/data'))
    root.mkdir(parents=True, exist_ok=True)
    cursor = root / 'telegram-offset'
    offset = int(cursor.read_text()) if cursor.exists() else None
    endpoint = 'https://api.telegram.org/bot' + os.environ['TELEGRAM_TOKEN'] + '/'
    def tell(text):
        r = http.post(endpoint + 'sendMessage', json={
            'chat_id': os.environ['TELEGRAM_CHAT_ID'],
            'message_thread_id': int(os.environ['TELEGRAM_THREAD_ID']), 'text': text}, timeout=30)
        r.raise_for_status()
        if not r.json().get('ok'): raise RuntimeError('Telegram send failed')
    print('Worker ready; waiting for authorized commands', flush=True)
    while True:
        try:
            r = http.post(endpoint + 'getUpdates', json={
                'offset': offset if offset is not None else -1,
                'timeout': 30, 'allowed_updates': ['message']}, timeout=40)
            r.raise_for_status()
            payload = r.json()
            if not payload.get('ok'): raise RuntimeError('Telegram polling failed')
            for update in payload['result']:
                # Persist before execution: never replay commands after a crash.
                offset = update['update_id'] + 1
                cursor.write_text(str(offset))
                msg = update.get('message', {})
                if not authorized(msg): continue
                words = msg.get('text', '').split()
                if not words: continue
                command = words[0].split('@')[0]
                if command in ('/start', '/status'):
                    tell('НОВЬ: бот готов. /report 2026-10-01 2026-10-23 — проверочная сводка, без записи на Диск.')
                elif command == '/report':
                    if len(words) != 3:
                        tell('Формат: /report ГГГГ-ММ-ДД ГГГГ-ММ-ДД')
                        continue
                    from datetime import date
                    try:
                        start, end = map(date.fromisoformat, words[1:])
                        if start > end or (end-start).days > 366: raise ValueError()
                    except ValueError:
                        tell('Проверь даты: период не более 366 дней.')
                        continue
                    tell('Проверяю письма. Результат появится в этой теме.')
                    result = subprocess.run([sys.executable, 'bot.py', '--start', str(start), '--end', str(end),
                                             '--output', str(root), '--publish', '--no-disk'], timeout=900)
                    if result.returncode:
                        tell('Отчёт не отправлен или уже отправлялся. Требуется проверка журнала запуска и доставки.')
        except Exception as exc:
            # Never log request URLs/tokens or mail contents.
            print('Worker error: ' + type(exc).__name__, flush=True)
            time.sleep(5)

if __name__ == '__main__': main()
