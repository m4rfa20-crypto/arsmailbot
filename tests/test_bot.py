import io, tempfile, unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from openpyxl import Workbook
import bot

def fixture(text='Согласовано.', value=1200, sender='approver@example.com', when='Fri, 23 Oct 2026 12:00:00 +0300'):
    msg = EmailMessage()
    msg['From'], msg['Subject'], msg['Date'] = sender, 'НОВЬ Смета 337', when
    msg.set_content(text)
    wb = Workbook()
    wb.active['A1'] = value
    out = io.BytesIO()
    wb.save(out)
    msg.add_attachment(out.getvalue(), maintype='application', subtype='octet-stream', filename='337 смета.xlsx')
    return msg.as_bytes()

class Tests(unittest.TestCase):
    def scan(self, messages):
        with tempfile.TemporaryDirectory() as d:
            cfg = {'sender': 'approver@example.com', 'amount_cells': {'смета:337': {'sheet':'Sheet', 'cell':'A1'}}}
            return bot.scan(messages, cfg, date(2026,10,1), date(2026,10,23), Path(d))
    def test_positive(self):
        self.assertEqual(self.scan([fixture()])['documents'][0]['amount'], '1200.00')
    def test_negative(self):
        for t in ['Не согласовано','Согласовано, если исправите','Согласовано?','Согласовано частично']:
            self.assertFalse(bot.approved(t))
    def test_quote(self):
        self.assertFalse(self.scan([fixture('Посмотрю\n> Согласовано')])['documents'])
    def test_dedup(self):
        raw = fixture()
        self.assertEqual(len(self.scan([raw,raw])['documents']), 1)
    def test_versions(self):
        r = self.scan([fixture(), fixture(value=1500)])
        self.assertTrue(all(x['conflict'] for x in r['documents']))
        self.assertIn('Смета: 0 шт.', bot.render(r))
    def test_sender(self):
        self.assertFalse(self.scan([fixture(sender='other@example.com')])['documents'])
    def test_moscow_boundary(self):
        self.assertFalse(self.scan([fixture(when='Fri, 23 Oct 2026 23:00:00 +0000')])['documents'])
    def test_formula(self):
        self.assertIsNone(self.scan([fixture(value='=100+200')])['documents'][0]['amount'])
    def test_html_quote(self):
        msg = EmailMessage()
        msg.set_content('<p>Проверю</p><blockquote>Согласовано</blockquote>',subtype='html')
        self.assertFalse(bot.approved(bot.fresh_text(msg)))
    def test_act(self):
        self.assertEqual(bot.identify('253 АКТ на перенос.xlsx'), ('акт','253'))
if __name__ == '__main__': unittest.main()
