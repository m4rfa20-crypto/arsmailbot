import os
import tempfile
import unittest
from datetime import date
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch
from test_bot import fixture
import bot
import worker
from threading_mail import parent_id, mailbox_wire, fetch_parent_messages

class ThreadTests(unittest.TestCase):
    def parent(self, number='253', when='Wed, 30 Sep 2026 12:00:00 +0300'):
        msg = BytesParser(policy=policy.default).parsebytes(fixture(sender='dmitry@example.test', when=when))
        msg['Message-ID'] = '<parent@example.test>'
        part = list(msg.iter_attachments())[0]
        part.replace_header('Content-Disposition', 'attachment; filename="'+number+' АКТ.xlsx"')
        return msg
    def reply(self, text='Акт согласован.', parent='<parent@example.test>'):
        msg = EmailMessage()
        msg['From'] = 'approver@example.com'
        msg['Date'] = 'Fri, 23 Oct 2026 12:00:00 +0300'
        msg['Subject'] = 'Re: НОВЬ акт'
        msg['Message-ID'] = '<approval@example.test>'
        if parent: msg['In-Reply-To'] = parent
        msg.set_content(text)
        return msg
    def scan(self, *messages):
        with tempfile.TemporaryDirectory() as d:
            return bot.scan([m.as_bytes() for m in messages], {'sender':'approver@example.com'},
                            date(2026,10,1),date(2026,10,23),Path(d))
    def test_parent_outside_period(self):
        r = self.scan(self.reply(), self.parent())['documents'][0]
        self.assertEqual(r['number'], '253')
        self.assertTrue(r['inherited'])
        self.assertEqual(r['date'], '2026-10-23')
        self.assertEqual(r['attachment_source_message_id'], '<parent@example.test>')
    def test_no_subject_guess(self):
        self.assertFalse(self.scan(self.reply(parent=None),self.parent())['documents'])
    def test_missing_parent(self):
        self.assertFalse(self.scan(self.reply())['documents'])
    def test_wrong_number(self):
        self.assertFalse(self.scan(self.reply('Акт №999 согласован.'),self.parent())['documents'])
    def test_two_acts(self):
        parent=self.parent()
        parent.add_attachment(b'other',maintype='application',subtype='octet-stream',filename='254 АКТ.xlsx')
        self.assertFalse(self.scan(self.reply(),parent)['documents'])
        self.assertEqual(len(self.scan(self.reply('Акт 253 согласован.'),parent)['documents']),1)
    def test_same_id_different_content(self):
        self.assertFalse(self.scan(self.reply(),self.parent(),self.parent('254'))['documents'])
    def test_no_future_parent(self):
        self.assertFalse(self.scan(self.reply(),self.parent(when='Sat, 24 Oct 2026 12:00:00 +0300'))['documents'])
    def test_references(self):
        msg=self.reply(parent=None)
        msg['References']='<older@example.test> <parent@example.test>'
        self.assertEqual(parent_id(msg),'<parent@example.test>')
        self.assertEqual(len(self.scan(msg,self.parent())['documents']),1)
    def test_unicode_mailbox(self):
        self.assertEqual(mailbox_wire('НОВЬ'),'"&BB0EHgQSBCw-"')
        self.assertEqual(mailbox_wire('A&B'),'"A&-B"')
    def test_command_authorization(self):
        with patch.dict(os.environ,TELEGRAM_OWNER_ID='1',TELEGRAM_CHAT_ID='-2',TELEGRAM_THREAD_ID='3'):
            self.assertTrue(worker.authorized({'from':{'id':1},'chat':{'id':-2},'message_thread_id':3}))
            self.assertFalse(worker.authorized({'from':{'id':4},'chat':{'id':-2},'message_thread_id':3}))
    def test_imap_parent_lookup_no_dates(self):
        from unittest.mock import Mock
        client=Mock()
        client.select.return_value=('OK',[])
        raw=self.parent().as_bytes()
        def uid(op,*args):
            if op=='search':
                self.assertEqual(args[1:3],('HEADER','Message-ID'))
                return 'OK',[b'42']
            return 'OK',[(b'header',raw)]
        client.uid.side_effect=uid
        found=fetch_parent_messages(client,[self.reply().as_bytes()],['INBOX'])
        self.assertEqual(found,[raw])
