"""Railway entrypoint: preview by default; optional existing Telegram worker."""
import json
import os
from pathlib import Path
from datetime import date, datetime
from bot import MSK, imap_messages, scan, render


def main():
    mode = os.getenv('RUN_MODE', 'preview')
    if mode == 'telegram':
        from worker import main as worker_main
        return worker_main()
    if mode != 'preview':
        raise ValueError('RUN_MODE must be preview or telegram')
    required = ('IMAP_USER', 'IMAP_PASSWORD', 'BOT_CONFIG_JSON')
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise ValueError('Missing variables: ' + ', '.join(missing))
    cfg = json.loads(os.environ['BOT_CONFIG_JSON'])
    today = datetime.now(MSK).date()
    start = date.fromisoformat(os.getenv('REPORT_START') or str(today.replace(day=1)))
    end = date.fromisoformat(os.getenv('REPORT_END') or str(today))
    if start > end or (end - start).days > 366:
        raise ValueError('Report period must be 0..366 days')
    output = Path(os.getenv('OUTPUT_DIR', '/data'))
    output.mkdir(parents=True, exist_ok=True)
    print('Preview started; no uploads and no Telegram messages', flush=True)
    report = scan(imap_messages(cfg, start, end), cfg, start, end, output)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'report.txt').write_text(render(report), encoding='utf-8')
    # Log counts only; mail text lives in the private volume, never deployment logs.
    print('Preview complete: documents=%d review=%d' %
          (len(report['documents']), len(report['review'])), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Cloud run failed: ' + type(exc).__name__ + '. Check configuration and connectivity.', flush=True)
        raise SystemExit(1)
