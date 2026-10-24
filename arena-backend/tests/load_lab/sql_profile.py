"""Opt-in SQL CPU profiler for this private load harness; never log parameters."""
import json
from pathlib import Path
import sqlite3
import threading
import time
from collections import defaultdict

records=defaultdict(lambda:[0,0])
lock=threading.Lock()
base_connect=sqlite3.connect
class Cursor(sqlite3.Cursor):
    statement=''
    def measured(self,method,*args):
        started=time.thread_time_ns()
        try:return method(*args)
        finally:
            with lock:
                row=records[self.statement];row[0]+=time.thread_time_ns()-started;row[1]+=1
    def execute(self,sql,parameters=()):
        self.statement=' '.join(sql.split())
        return self.measured(super().execute,sql,parameters)
    def executemany(self,sql,parameters):
        self.statement=' '.join(sql.split())
        return self.measured(super().executemany,sql,parameters)
    def fetchone(self):return self.measured(super().fetchone)
    def fetchall(self):return self.measured(super().fetchall)
class Connection(sqlite3.Connection):
    def execute(self,sql,parameters=()):return self.cursor(Cursor).execute(sql,parameters)
    def executemany(self,sql,parameters):return self.cursor(Cursor).executemany(sql,parameters)
def connect(*args,**kwargs):return base_connect(*args,**dict(kwargs,factory=Connection))
def save(path):
    with lock:rows=[dict(sql=key,cpu_seconds=round(value[0]/1e9,6),calls=value[1]) for key,value in records.items()]
    Path(path).write_text(json.dumps(sorted(rows,key=lambda x:-x['cpu_seconds']),indent=2)+'\n')
