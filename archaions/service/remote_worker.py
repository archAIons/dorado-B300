import json
import os
import time

import modal
from server import ROOT, db, gpu_options
from job_queue import claim, heartbeat


def process(job):
    folder = ROOT / job["id"] / job["attempt"]
    folder.mkdir(mode=0o700)
    volume = modal.Volume.from_name("archaions-basecalling-test")
    targets = {"H100": ("archaions-basecalling-test", "basecall"),
               "B300": ("archaions-dorado-b300-port", "basecall_live")}
    import threading
    stop = threading.Event()
    lost = threading.Event()

    def keep_alive():
        while not stop.wait(20):
            try:
                (ROOT / "dispatcher.heartbeat").touch()
                heartbeat(job)
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    call = None
    try:
        selected = next((item for item in gpu_options() if item["id"] == job["gpu"]), None)
        if not selected or not selected["available"]:
            raise RuntimeError("The selected GPU is not currently available for basecalling.")
        function = modal.Function.from_name(*targets[job["gpu"]])
        with volume.batch_upload(force=True) as batch:
            batch.put_file(ROOT / job["id"] / "input.pod5", f"/{job['id']}/input.pod5")
        heartbeat(job)
        call = function.spawn(job["id"], job["attempt"], job["model"], json.loads(job["options"]))
        with db() as conn:
            conn.execute("UPDATE jobs SET remote_call=? WHERE id=? AND attempt=?", (call.object_id, job["id"], job["attempt"]))
        result = call.get(timeout=2100)
        if lost.is_set():
            raise RuntimeError("Worker lease expired")
        if not result["ok"]:
            detail = result["error"]
            (folder / "worker-error.log").write_text(detail)
            if "Unknown chemistry" in detail or "No supported chemistry" in detail:
                detail = "Dorado could not select a model for this file's flow cell, sequencing kit and sample rate. Try a POD5 file from a supported kit; older or prototype kits may need an explicit model."
            elif "timed out" in detail:
                detail = "This file exceeded the 25-minute test limit. Try a smaller POD5 file."
            else:
                detail = "Basecalling failed on the GPU. The diagnostic log has been saved for investigation."
            raise RuntimeError(detail)
        result["requested_gpu"] = job["gpu"]
        names = ["calls.bam", "calls.fastq.gz", "provenance.json"]
        options = json.loads(job["options"])
        if options['qc']: names.append('qc.json')
        if options['demultiplex']: names.append('demultiplexed.zip')
        for name in names:
            with (folder / name).open("wb") as stream:
                for chunk in volume.read_file(f"/{job['id']}/{job['attempt']}/{name}"):
                    stream.write(chunk)
        with db() as conn:
            conn.execute("UPDATE jobs SET state='complete',error=NULL,metrics=? WHERE id=? AND attempt=?",
                         (json.dumps(result), job["id"], job["attempt"]))
    except Exception as exc:
        if call:
            try:
                call.cancel()
            except Exception:
                pass
        with db() as conn:
            conn.execute("UPDATE jobs SET state='failed',error=? WHERE id=? AND attempt=?",
                         (str(exc)[-2400:], job["id"], job["attempt"]))
    finally:
        stop.set()
        thread.join(timeout=2)
        try:
            with db() as conn:
                owner = conn.execute("SELECT attempt FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if owner and owner["attempt"] == job["attempt"]:
                volume.remove_file(f"/{job['id']}", recursive=True)
        except Exception:
            pass


if __name__ == "__main__":
    while True:
        try:
            (ROOT / "dispatcher.heartbeat").touch()
            job = claim()
            if job:
                process(job)
            else:
                time.sleep(2)
        except Exception as exc:
            print(f"Dispatcher error: {type(exc).__name__}", flush=True)
            time.sleep(10)
