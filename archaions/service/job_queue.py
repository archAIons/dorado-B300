import time
import uuid
from server import ROOT, db

LEASE = 180


def claim():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM jobs WHERE state='queued'
          OR (state='running' AND heartbeat<?) ORDER BY created LIMIT 1""",
            (time.time() - LEASE,),
        ).fetchone()
        if row is None:
            return None
        attempt = uuid.uuid4().hex
        conn.execute(
            "UPDATE jobs SET state='running',attempt=?,heartbeat=?,error=NULL WHERE id=?",
            (attempt, time.time(), row["id"]),
        )
        return dict(row) | {"attempt": attempt}


def heartbeat(job):
    with db() as conn:
        updated = conn.execute(
            "UPDATE jobs SET heartbeat=? WHERE id=? AND attempt=? AND state='running'",
            (time.time(), job["id"], job["attempt"]),
        ).rowcount
    if not updated:
        raise RuntimeError("Worker lease was replaced")
