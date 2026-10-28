"""Temporary YouGile read-only data collector. Never writes to YouGile."""
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = "https://yougile.com/api-v2"

def serial(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)

def normalize(x):
    return " ".join(str(x).casefold().split())

def rows_of(result):
    if isinstance(result, list):
        return result, {}
    if isinstance(result, dict):
        for field in ("content", "items", "data", "results"):
            if isinstance(result.get(field), list):
                return result[field], result.get("paging") or {}
    raise ValueError("Unexpected API response; list content missing")

def has_due(task):
    d = task.get("deadline")
    return bool(d) if not isinstance(d, dict) else any(d.get(k) for k in ("deadline", "date", "endDate", "dateEnd", "end"))

class API:
    def __init__(self, key):
        if not key:
            raise RuntimeError("YOUGILE_API_KEY is not set; add it privately to Railway Variables")
        self.key = key
        self.base = os.getenv("YOUGILE_API_BASE", API_ROOT).rstrip("/")
        self.last = 0.0
    def get(self, path, params=None):
        if not path.startswith("/") or ".." in path:
            raise ValueError("Invalid API path")
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            elapsed = time.monotonic() - self.last
            if elapsed < 1.4: time.sleep(1.4 - elapsed)
            self.last = time.monotonic()
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.key,
                "Accept": "application/json", "User-Agent": "Arsolit-YouGile-Trial/0.1"}, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=25) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                if err.code in (429, 500, 502, 503, 504) and attempt < 3:
                    header = err.headers.get("Retry-After")
                    time.sleep(int(header) if header and header.isdigit() else 2 ** (attempt + 1))
                    continue
                raise RuntimeError(f"YouGile error HTTP {err.code} at {path}") from None
        raise RuntimeError("API retry limit exceeded")
    def pages(self, path, filters=None):
        found = []
        offset = 0
        for _ in range(100):
            entries, paging = rows_of(self.get(path, {**(filters or {}), "limit": 100, "offset": offset}))
            found.extend(entries)
            if not entries or not paging.get("next", len(entries) >= 100):
                return found
            offset += len(entries)
        raise RuntimeError("Pagination exceeded 100 pages; incomplete export prohibited")

def collect(api, names):
    projects = api.pages("/projects")
    selected = [p for p in projects if normalize(p.get("title", "")) in names]
    if not selected:
        raise RuntimeError("No project matched the allowlist; check YOUGILE_PROJECTS and permissions")
    records = {}
    for p in selected:
        for board in api.pages("/boards", {"projectId": p["id"]}):
            for col in api.pages("/columns", {"boardId": board["id"]}):
                for task in api.pages("/task-list", {"columnId": col["id"]}):
                    if not task.get("id") or task.get("deleted") or task.get("archived"): continue
                    key=(p["id"], task["id"])
                    records[key]={"project":p["title"], "project_id":p["id"],
                        "board":board.get("title"), "column":col.get("title"), "task":task}
    return list(records.values())

def save(records, output):
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output / "observations.sqlite3")
    db.executescript("""CREATE TABLE IF NOT EXISTS observations(
       id INTEGER PRIMARY KEY, observed_at TEXT, task_id TEXT, project_id TEXT, snapshot TEXT);
       CREATE TABLE IF NOT EXISTS changes(
       observed_at TEXT, task_id TEXT, project_id TEXT, field TEXT, old_json TEXT, new_json TEXT);""")
    now=datetime.now(timezone.utc).isoformat()
    written=0
    field_changes=0
    for record in records:
        task=record["task"]
        if not has_due(task): continue
        task_id, project_id=task["id"],record["project_id"]
        previous=db.execute("SELECT snapshot FROM observations WHERE task_id=? AND project_id=? ORDER BY id DESC LIMIT 1", (task_id,project_id)).fetchone()
        if previous:
            old=json.loads(previous[0])["task"]
            for field in ("deadline","completed","assignedTo","columnId","title","archived"):
                if serial(old.get(field)) != serial(task.get(field)):
                    field_changes+=1
                    db.execute("INSERT INTO changes VALUES(?,?,?,?,?,?)",
                        (now,task_id,project_id,field,serial(old.get(field)),serial(task.get(field))))
        db.execute("INSERT INTO observations(observed_at,task_id,project_id,snapshot) VALUES(?,?,?,?)",
            (now,task_id,project_id,serial(record)))
        written+=1
    db.commit()
    db.close()
    suffix=now.replace(":","-")
    (output/f"snapshot-{suffix}.json").write_text(
        json.dumps({"observed_at":now, "records":records},ensure_ascii=False,indent=2),encoding="utf-8")
    with (output/"latest.csv").open("w",encoding="utf-8-sig",newline="") as fh:
        w=csv.writer(fh)
        w.writerow(["project","board","column","task_id","title","completed","deadline","assigned_to"])
        for record in records:
            t=record["task"]
            if has_due(t):
                w.writerow([record["project"],record["board"],record["column"],t["id"],
                    t.get("title"),t.get("completed"),serial(t.get("deadline")),
                    serial(t.get("assignedTo",t.get("assigned")))])
    return {"total_raw_tasks":len(records),"with_deadlines":written,
            "excluded_without_deadline":len(records)-written,"changed_fields_since_last_snapshot":field_changes,
            "output":str(output)}

def main():
    mode=sys.argv[1] if len(sys.argv)>1 else "probe"
    if mode not in ("probe","collect"): raise SystemExit("Usage: python yougile_trial.py [probe|collect]")
    allowlist={normalize(s) for s in os.getenv("YOUGILE_PROJECTS","Новь").split(",") if s.strip()}
    try:
        api=API(os.getenv("YOUGILE_API_KEY",""))
        if mode=="probe":
            names=[p.get("title","") for p in api.pages("/projects")]
            result={"mode":"probe","available_projects":names,"allowlisted":[n for n in names if normalize(n) in allowlist]}
        else:
            result=save(collect(api,allowlist),Path(os.getenv("YOUGILE_OUTPUT_DIR","/data/yougile-trial")))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (RuntimeError,ValueError,OSError,sqlite3.Error,urllib.error.URLError) as exc:
        print("YouGile trial error: "+str(exc),file=sys.stderr)
        raise SystemExit(1)

if __name__=="__main__": main()
