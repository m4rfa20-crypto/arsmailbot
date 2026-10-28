import sqlite3
import tempfile
import unittest
from pathlib import Path
from yougile_trial import API, collect, has_due, rows_of, save

class StubAPI:
    def pages(self, path, filters=None):
        if path == "/projects":
            return [{"id":"p1","title":"Новь"},{"id":"p2","title":"#Офисные дела"}]
        if path == "/boards":
            return [{"id":"b1","title":"Монтаж"}] if filters.get("projectId") == "p1" else []
        if path == "/columns":
            return [{"id":"c1","title":"К работам"}] if filters.get("boardId") == "b1" else []
        if path == "/task-list":
            return [
                {"id":"t1","title":"Установка столярки","deadline":{"deadline":"2026-11-20"},"completed":False},
                {"id":"t2","title":"Без срока","completed":False},
            ] if filters.get("columnId") == "c1" else []
        raise AssertionError(path)

class CollectorTests(unittest.TestCase):
    def test_page(self):
        self.assertEqual(rows_of({"content":[{"id":"x"}],"paging":{"next":False}})[0][0]["id"],"x")
    def test_deadline(self):
        self.assertTrue(has_due({"deadline":{"deadline":"2026-11-20"}}))
        self.assertFalse(has_due({"deadline":{"startDate":"2026-11-01"}}))
    def test_scoping_and_dedup(self):
        records=collect(StubAPI(),{"новь"})
        self.assertEqual(len(records),2)
        self.assertEqual({r["project"] for r in records},{"Новь"})
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            a=save(records,out)
            self.assertEqual(a["with_deadlines"],1)
            b=save(records,out)
            self.assertEqual(b["changed_fields_since_last_snapshot"],0)
            records[0]["task"]["deadline"]={"deadline":"2026-11-23"}
            c=save(records,out)
            self.assertEqual(c["changed_fields_since_last_snapshot"],1)
            self.assertTrue((out/"latest.csv").exists())
            with sqlite3.connect(out/"observations.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM changes").fetchone()[0],1)
    def test_api_key_required(self):
        with self.assertRaises(RuntimeError): API("")

if __name__=="__main__": unittest.main()
