import hashlib
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
import bot
import cloud_entry
from http_client import Response
from test_bot import fixture


def response(status, data):
    return Response(status, json.dumps(data).encode())


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.data = b'example document bytes'
        self.row = {'file': '337.xlsx', 'disk_folder': 'disk:/Example/337/approved',
                    'sha256': hashlib.sha256(self.data).hexdigest()}
        self.meta = {'type': 'file', 'size': len(self.data), 'sha256': self.row['sha256']}
        self.env = patch.dict(os.environ, YANDEX_DISK_TOKEN='synthetic-test-token')
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_scan_keeps_attachments_only_in_memory(self):
        with tempfile.TemporaryDirectory() as d:
            payloads = {}
            report = bot.scan([fixture()], {'sender': 'approver@example.com'},
                              date(2026,10,1), date(2026,10,23), Path(d), payloads)
            row = report['documents'][0]
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertNotIn('local', row)
            self.assertEqual(hashlib.sha256(payloads[row['sha256']]).hexdigest(), row['sha256'])
            json.dumps(report)

    def test_diagnostics_include_reply_without_attachment_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            report = bot.scan([fixture('Посмотрю завтра')], {'sender':'approver@example.com'},
                              date(2026,10,1), date(2026,10,23), Path(d))
            self.assertEqual(report['review'][0]['reply_text'], 'Посмотрю завтра')
            self.assertEqual(report['review'][0]['attachment_names'], ['337 смета.xlsx'])
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_existing_identical_file_is_not_uploaded(self):
        with patch('bot.requests.get', return_value=response(200, self.meta)), patch('bot.requests.put') as put:
            bot.disk_upload(self.row, self.data)
            put.assert_not_called()

    def test_existing_other_version_is_not_overwritten(self):
        with patch('bot.requests.get', return_value=response(200, {**self.meta, 'sha256':'different'})), patch('bot.requests.put') as put:
            with self.assertRaises(RuntimeError):
                bot.disk_upload(self.row, self.data)
            put.assert_not_called()

    def test_upload_bytes_and_verify(self):
        answers = [response(404, {}), response(200, {'href':'https://uploader.disk.yandex.net/upload', 'method':'PUT'}), response(200, self.meta)]
        with patch('bot.requests.get', side_effect=answers) as get, patch('bot.requests.put', return_value=response(201, {})) as put:
            bot.disk_upload(self.row, self.data)
            self.assertIs(put.call_args.kwargs['data'], self.data)
            self.assertFalse(put.call_args.kwargs['allow_redirects'])
            self.assertNotIn('headers', put.call_args.kwargs)
            self.assertEqual(get.call_args_list[1].kwargs['params']['overwrite'], 'false')

    def test_unexpected_upload_host_not_contacted(self):
        answers = [response(404, {}), response(200, {'href':'https://other.example/upload'})]
        with patch('bot.requests.get', side_effect=answers), patch('bot.requests.put') as put:
            with self.assertRaises(RuntimeError):
                bot.disk_upload(self.row, self.data)
            put.assert_not_called()

    def test_unverified_upload_is_not_success(self):
        answers = [response(404, {}), response(200, {'href':'https://uploader.disk.yandex.net/upload'})] + [response(404, {})] * 3
        with patch('bot.requests.get', side_effect=answers), patch('bot.requests.put', return_value=response(202, {})), patch('bot.time.sleep'):
            with self.assertRaises(RuntimeError):
                bot.disk_upload(self.row, self.data)

    def test_preview_never_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            env = {'RUN_MODE':'preview','IMAP_USER':'user@example.com','IMAP_PASSWORD':'test',
                   'BOT_CONFIG_JSON':json.dumps({'sender':'approver@example.com'}),
                   'OUTPUT_DIR':d,'REPORT_START':'2026-10-01','REPORT_END':'2026-10-23'}
            with patch.dict(os.environ, env, clear=True), patch('cloud_entry.imap_messages', return_value=[fixture()]), patch('bot.requests.put') as put, patch('bot.requests.post') as post:
                cloud_entry.main()
                put.assert_not_called()
                post.assert_not_called()
            self.assertEqual({p.name for p in Path(d).iterdir()}, {'report.json','report.txt'})
