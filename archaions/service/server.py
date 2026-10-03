import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
import time
import uuid
import json
from contextvars import ContextVar
import httpx
from fastapi.responses import JSONResponse

account = ContextVar("account", default="")

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from run_options import (
    RunOptions,
    KITS,
    DNA_MODS,
    RNA_MODS,
    BARCODE_KITS,
    validate_chemistry,
)

ROOT = Path(os.environ.get("BASECALL_DATA", "./data")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ["BASECALL_TOKEN"]
if len(TOKEN) < 24:
    raise RuntimeError("BASECALL_TOKEN must contain at least 24 characters")
MAX_CHUNK = 8 * 1024 * 1024
QUOTA = int(os.environ.get("BASECALL_QUOTA_BYTES", str(100 * 1024**3)))
MAX_FILE = int(os.environ.get("BASECALL_MAX_FILE_BYTES", str(2 * 1024**3)))


def db():
    conn = sqlite3.connect(ROOT / "jobs.sqlite", timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


with db() as conn:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY, run TEXT NOT NULL, filename TEXT NOT NULL,
      sha256 TEXT NOT NULL, size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
      model TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'uploading',
      error TEXT, created REAL NOT NULL, attempt TEXT, heartbeat REAL,
      UNIQUE(run, sha256, model))"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column in ("metrics", "remote_call"):
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")
    if "gpu" not in columns:
        conn.execute(
            """CREATE TABLE jobs_gpu (
          id TEXT PRIMARY KEY, run TEXT NOT NULL, filename TEXT NOT NULL,
          sha256 TEXT NOT NULL, size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
          model TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'uploading',
          error TEXT, created REAL NOT NULL, attempt TEXT, heartbeat REAL,
          metrics TEXT, remote_call TEXT, gpu TEXT NOT NULL DEFAULT 'H100',
          UNIQUE(run, sha256, model, gpu))"""
        )
        conn.execute(
            """INSERT INTO jobs_gpu
          SELECT id,run,filename,sha256,size,offset,model,state,error,created,attempt,heartbeat,
                 metrics,remote_call,'H100' FROM jobs"""
        )
        conn.execute("DROP TABLE jobs")
        conn.execute("ALTER TABLE jobs_gpu RENAME TO jobs")

with db() as conn:
    conn.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "options" not in columns:
        conn.execute(
            """CREATE TABLE jobs_options (
          id TEXT PRIMARY KEY, run TEXT NOT NULL, filename TEXT NOT NULL,
          sha256 TEXT NOT NULL, size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
          model TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'uploading',
          error TEXT, created REAL NOT NULL, attempt TEXT, heartbeat REAL,
          metrics TEXT, remote_call TEXT, gpu TEXT NOT NULL DEFAULT 'B300',
          options TEXT NOT NULL, UNIQUE(run,sha256,model,gpu,options))"""
        )
        defaults = json.dumps(RunOptions(qc=False).model_dump(), sort_keys=True)
        conn.execute(
            "INSERT INTO jobs_options SELECT id,run,filename,sha256,size,offset,model,state,error,created,attempt,heartbeat,metrics,remote_call,gpu,? FROM jobs",
            (defaults,),
        )
        conn.execute("DROP TABLE jobs")
        conn.execute("ALTER TABLE jobs_options RENAME TO jobs")

with db() as conn:
    conn.execute("PRAGMA journal_mode=WAL")


def gpu_options():
    ready = False
    models = ["hac"]
    try:
        status = json.loads((ROOT / "gpu-status.json").read_text()).get("B300", {})
        ready = status.get("available") is True
        models = [m for m in ("hac", "sup") if m in status.get("models", ["hac"])]
        ready = ready and bool(models)
    except (OSError, ValueError, TypeError, AttributeError):
        ready = False
        pass
    return [
        {
            "id": "B300",
            "name": "NVIDIA B300",
            "available": ready,
            "models": models,
            "detail": (
                "Experimental · "
                + " / ".join(m.upper() for m in models)
                + " · Kit 14 DNA / RNA004 · experimental"
                if ready
                else "Awaiting compatible Dorado GPU libraries. Please try again later."
            ),
        },
    ]


app = FastAPI(title="Archaions B300 Basecalling")


async def resolve_account(request):
    token = request.cookies.get("archaions_session", "")
    if not token:
        raise HTTPException(401, "Sign in to use basecalling.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                os.environ.get(
                    "BASECALL_ACCOUNT_URL", "http://127.0.0.1:8000/api/account/me"
                ),
                cookies={"archaions_session": token},
            )
        if response.status_code == 401:
            raise HTTPException(401, "Sign in to use basecalling.")
        response.raise_for_status()
        email = response.json()["email"]
        return hashlib.sha256(email.encode()).hexdigest() + ":"
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Account service is temporarily unavailable.")


@app.middleware("http")
async def account_access(request, call_next):
    context = None
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") not in set(
                os.environ.get(
                    "BASECALL_ALLOWED_ORIGINS",
                    "https://archaions.com,https://www.archaions.com",
                ).split(",")
            ):
                raise HTTPException(403, "Origin is not allowed")
        context = account.set(await resolve_account(request))
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    finally:
        if context is not None:
            account.reset(context)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    marker = ROOT / "dispatcher.heartbeat"
    online = marker.exists() and time.time() - marker.stat().st_mtime < 90
    return {
        "ok": True,
        "worker_online": online,
        "gpu": "NVIDIA B300",
        "max_file_bytes": MAX_FILE,
        "chunk_bytes": MAX_CHUNK,
        "quota_bytes": QUOTA,
        "gpus": gpu_options(),
        "kits": KITS,
        "dna_modifications": DNA_MODS,
        "rna_modifications": RNA_MODS,
        "barcode_kits": sorted(BARCODE_KITS),
    }


class Upload(BaseModel):
    run: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=50 * 1024**3)
    model: str = Field(pattern=r"^(hac|sup)$", default="hac")
    gpu: str = Field(pattern=r"^B300$", default="B300")
    options: RunOptions = Field(default_factory=RunOptions)


def get_job(conn, job_id):
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or not account.get() or not row["run"].startswith(account.get()):
        raise HTTPException(404, "Unknown job")
    return row


def public(row):
    result = {
        k: row[k]
        for k in (
            "id",
            "run",
            "filename",
            "size",
            "offset",
            "model",
            "gpu",
            "state",
            "error",
        )
    }
    result["run"] = result["run"][len(account.get()) :]
    result["options"] = json.loads(row["options"])
    result["metrics"] = json.loads(row["metrics"]) if row["metrics"] else None
    return result


@app.post("/uploads")
def initiate(body: Upload):
    try:
        body.options.validate_model(body.model)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    options = json.dumps(body.options.model_dump(), sort_keys=True)
    body.run = account.get() + body.run
    selected = next(item for item in gpu_options() if item["id"] == body.gpu)
    if not selected["available"]:
        raise HTTPException(409, selected["name"] + ": " + selected["detail"])
    if body.model not in selected.get("models", ["fast", "hac", "sup"]):
        supported = " / ".join(m.upper() for m in selected.get("models", []))
        raise HTTPException(
            409,
            f"B300 currently supports validated models: {supported}. Choose HAC or SUP.",
        )
    if not body.filename.lower().endswith(".pod5"):
        raise HTTPException(400, "Expected a POD5 file")
    if body.size > MAX_FILE:
        raise HTTPException(413, "This page accepts files up to 2 GiB each")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM jobs WHERE run=? AND sha256=? AND model=? AND gpu=? AND options=?",
            (body.run, body.sha256, body.model, body.gpu, options),
        ).fetchone()
        if existing:
            if existing["size"] != body.size:
                raise HTTPException(409, "Size differs for this checksum")
            return public(existing)
        reserved = conn.execute("SELECT COALESCE(SUM(size),0) FROM jobs").fetchone()[0]
        if reserved + body.size > QUOTA:
            raise HTTPException(413, "Raw-data quota exceeded")
        job_id = uuid.uuid4().hex
        folder = ROOT / job_id
        folder.mkdir(mode=0o700)
        conn.execute(
            "INSERT INTO jobs(id,run,filename,sha256,size,model,gpu,created,options) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                body.run,
                body.filename,
                body.sha256,
                body.size,
                body.model,
                body.gpu,
                time.time(),
                options,
            ),
        )
        return public(get_job(conn, job_id))


@app.get("/jobs")
def jobs(run: str = ""):
    with db() as conn:
        if not run:
            return [
                public(r)
                for r in conn.execute(
                    "SELECT * FROM jobs WHERE substr(run,1,65)=? ORDER BY created DESC LIMIT 200",
                    (account.get(),),
                )
            ]
        return [
            public(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE run=? ORDER BY created",
                (account.get() + run,),
            )
        ]


@app.get("/jobs/{job_id}")
def status(job_id: str):
    with db() as conn:
        return public(get_job(conn, job_id))


@app.put("/uploads/{job_id}")
async def chunk(job_id: str, offset: int, request: Request):
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > MAX_CHUNK:
            raise HTTPException(413, "Chunk exceeds 8 MiB")
        data.extend(part)
    if not data:
        raise HTTPException(400, "Empty chunk")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_job(conn, job_id)
        if row["state"] != "uploading" or offset != row["offset"]:
            raise HTTPException(409, "Refresh job status before resuming")
        if offset + len(data) > row["size"]:
            raise HTTPException(400, "Chunk exceeds declared size")
        path = ROOT / job_id / "input.part"
        with path.open("r+b" if path.exists() else "w+b") as stream:
            stream.seek(offset)
            stream.write(data)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        conn.execute(
            "UPDATE jobs SET offset=? WHERE id=?", (offset + len(data), job_id)
        )
        return public(get_job(conn, job_id))


class UnsupportedB300Chemistry(ValueError):
    pass


def validate_pod5(path, gpu="B300", options=None):
    import pod5

    options = options or RunOptions()
    with pod5.Reader(path) as reader:
        if reader.num_reads == 0:
            raise ValueError("POD5 contains no reads")
        seen = set()
        for read in reader.reads():
            info = read.run_info
            key = (info.flow_cell_product_code, info.sequencing_kit, info.sample_rate)
            if key not in seen:
                try:
                    validate_chemistry(info, options)
                except ValueError as exc:
                    raise UnsupportedB300Chemistry(str(exc)) from exc
                seen.add(key)


@app.post("/uploads/{job_id}/complete")
def complete(job_id: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_job(conn, job_id)
        if row["state"] != "uploading":
            return public(row)
        if row["offset"] != row["size"]:
            raise HTTPException(409, "Upload is incomplete")
        folder = ROOT / job_id
        path = folder / "input.part"
        if not path.exists():
            path = folder / "input.pod5"
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != row["sha256"]:
            conn.execute(
                "UPDATE jobs SET offset=0,error='Checksum mismatch; resend file' WHERE id=?",
                (job_id,),
            )
            return public(get_job(conn, job_id))
        try:
            validate_pod5(
                path, row["gpu"], RunOptions.model_validate_json(row["options"])
            )
        except Exception as exc:
            detail = (
                str(exc)
                if isinstance(exc, UnsupportedB300Chemistry)
                else f"POD5 validation failed: {type(exc).__name__}"
            )
            conn.execute(
                "UPDATE jobs SET state='failed',error=? WHERE id=?", (detail, job_id)
            )
            return public(get_job(conn, job_id))
        path.replace(folder / "input.pod5")
        conn.execute("UPDATE jobs SET state='queued',error=NULL WHERE id=?", (job_id,))
        return public(get_job(conn, job_id))


@app.get("/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    if kind not in (
        "bam",
        "fastq.gz",
        "provenance.json",
        "qc.json",
        "demultiplexed.zip",
    ):
        raise HTTPException(404)
    with db() as conn:
        row = get_job(conn, job_id)
        if row["state"] != "complete":
            raise HTTPException(409, "Results are not ready")
        filename = "calls." + kind if kind in ("bam", "fastq.gz") else kind
        path = ROOT / job_id / row["attempt"] / filename
        if not path.is_file():
            raise HTTPException(404, "This output was not requested for this run")
        return FileResponse(path, filename=f"{job_id}-{filename}")
