import modal
from pathlib import Path

app = modal.App("archaions-dorado-b300-port")
storage = modal.Volume.from_name("archaions-basecalling-test", create_if_missing=True)
build_volume = modal.Volume.from_name("archaions-dorado-b300-build")
BINARY_SHA256 = "fc0ed5e8db53c88a042b8ee409545d44076ad09b0c561ffb8f491947a6ecc06f"
KOI_SHA256 = "92dc1cc8ec0e3f9f773567175a844c2a6a9c3e572dc1ae71f9b41fa197dbe6fe"
MODEL = "dna_r10.4.1_e8.2_400bps_hac@v6.0.0"
MODELS = {
    "hac": MODEL,
    "fast": "dna_r10.4.1_e8.2_400bps_fast@v5.2.0",
    "sup": "dna_r10.4.1_e8.2_400bps_sup@v5.2.0",
}
MULTIMODEL_BINARY = "/build/koi-b300-multimodel-v2/bin/dorado"
MULTIMODEL_SHA256 = "7381e37d378ea78353ab115fdce5f4ad09400448a76f17a0e06c4ffd5c7327ef"
SUP_BINARY = "/build/koi-b300-sup-preserved-v1/bin/dorado"
SUP_SHA256 = "db8ba888425b57756fd1f1dbefb3bcc5c0aae2b5040384835cc64ba8a536b0aa"
SUP_KOI_SHA256 = "fd6bc7b565be5810a8347a074eb74d36db76f858ecb6e649eee52cc1f3a9041e"
image = (
    modal.Image.from_registry(
        "nvidia/cuda:13.1.1-runtime-ubuntu22.04", add_python="3.11"
    )
    .entrypoint([])
    .apt_install("samtools", "libgomp1", "libatomic1")
    .pip_install("pod5==0.3.48", "pydantic>=2,<3", "pysam==0.23.3")
    .env({"PATH": "/build/koi-b300-tuned-4096/bin:/usr/local/bin:/usr/bin:/bin"})
    .add_local_file(
        Path(__file__).parent / "service/run_options.py", "/root/run_options.py"
    )
    .add_local_file(
        Path(__file__).parent / "service/run_outputs.py", "/root/run_outputs.py"
    )
)


@app.function(
    image=image,
    gpu="B300",
    cpu=8,
    memory=32768,
    timeout=1800,
    min_containers=0,
    max_containers=1,
    scaledown_window=30,
    volumes={"/data": storage, "/build": build_volume},
)
def basecall_live(job_id: str, attempt: str, quality: str, options: dict = None):
    from run_options import RunOptions, validate_chemistry, basecaller_flags
    from run_outputs import filter_and_summarize

    settings = RunOptions.model_validate(options or {"qc": False})
    if quality != "fast" or options is not None:
        settings.validate_model(quality)
    import hashlib
    import pod5
    import gzip
    import json
    from pathlib import Path
    import re
    import subprocess
    import time

    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or not re.fullmatch(
        r"[0-9a-f]{32}", attempt
    ):
        raise ValueError("Invalid job identifier")
    if quality not in MODELS:
        raise ValueError("Invalid model")
    model = (
        f"rna004_{quality}@v6.0.0"
        if settings.kit.startswith("SQK-RNA")
        else MODELS[quality]
    )
    binary = (
        "/build/koi-b300-tuned-4096/bin/dorado"
        if quality == "hac"
        else MULTIMODEL_BINARY
    )
    expected_sha = BINARY_SHA256 if quality == "hac" else MULTIMODEL_SHA256
    if quality == "sup" or settings.modifications or settings.kit.startswith("SQK-RNA"):
        binary, expected_sha = SUP_BINARY, SUP_SHA256
    storage.reload()
    build_volume.reload()
    with Path(binary).open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha:
            raise RuntimeError("B300 executable differs from the validated build")
    source = Path("/data") / job_id / "input.pod5"
    with pod5.Reader(source) as reader:
        if not reader.num_reads:
            raise ValueError("POD5 contains no reads")
        for read in reader.reads():
            info = read.run_info
            validate_chemistry(info, settings)
    folder = Path("/data") / job_id / attempt
    folder.mkdir(parents=True, exist_ok=True)
    models = Path("/data/models")
    models.mkdir(exist_ok=True)
    if not (models / model / "config.toml").is_file():
        subprocess.run(
            [binary, "download", "--model", model, "--models-directory", str(models)],
            check=True,
            timeout=600,
        )
    started = time.monotonic()
    timings = {}
    args = [
        binary,
        "basecaller",
        str(models / model),
        str(source),
        "--device",
        "cuda:0",
        "--models-directory",
        str(models),
    ] + basecaller_flags(settings)
    if settings.kit.startswith("SQK-RNA"):
        args += ["--batchsize", "128"]
    try:
        with (
            (folder / "worker.log").open("wb") as log,
            (folder / "calls.bam").open("wb") as bam,
        ):
            subprocess.run(args, stdout=bam, stderr=log, check=True, timeout=1500)
        timings["basecall_seconds"] = round(time.monotonic() - started, 3)
        report = filter_and_summarize(folder, settings)
        if settings.demultiplex:
            import zipfile

            demux = folder / "demultiplexed"
            with (folder / "worker.log").open("ab") as log:
                subprocess.run(
                    [
                        binary,
                        "demux",
                        "--no-classify",
                        "--output-dir",
                        str(demux),
                        str(folder / "calls.bam"),
                    ],
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=300,
                )
            with zipfile.ZipFile(
                folder / "demultiplexed.zip", "w", compression=zipfile.ZIP_STORED
            ) as archive:
                for output in sorted(demux.rglob("*")):
                    if output.is_file():
                        archive.write(output, output.relative_to(demux))
        export_started = time.monotonic()
        subprocess.run(
            ["samtools", "quickcheck", "-u", str(folder / "calls.bam")],
            check=True,
            timeout=30,
        )
        with (
            (folder / "worker.log").open("ab") as log,
            gzip.open(folder / "calls.fastq.gz", "wb", compresslevel=1) as output,
        ):
            proc = subprocess.Popen(
                ["samtools", "fastq", "-T", "*", str(folder / "calls.bam")],
                stdout=subprocess.PIPE,
                stderr=log,
            )
            try:
                while block := proc.stdout.read(1024 * 1024):
                    output.write(block)
                if proc.wait(timeout=60):
                    raise RuntimeError("FASTQ export failed")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        timings["export_seconds"] = round(time.monotonic() - export_started, 3)
        header = subprocess.check_output(
            ["samtools", "view", "-H", str(folder / "calls.bam")], timeout=30
        ).decode()
        read_count = int(
            subprocess.check_output(
                ["samtools", "view", "-c", str(folder / "calls.bam")], timeout=60
            )
        )
        if report["total_reads"] <= 0:
            raise RuntimeError("Basecaller produced no reads")
        version = subprocess.check_output(
            [binary, "--version"], text=True, timeout=30
        ).strip()
        gpu = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            timeout=30,
        ).strip()
        provenance = {
            "dorado_version": version,
            "gpu": gpu,
            "quality": quality,
            "experimental": True,
            "settings": settings.model_dump(),
            "model": model,
            "koi_build": "embedded-ptx-sm103-v1",
            "koi_sha256": SUP_KOI_SHA256 if binary == SUP_BINARY else KOI_SHA256,
            "dorado_sha256": expected_sha,
            "command": args,
            "bam_header": header,
            "reads": read_count,
            "timings": timings,
            "fastq_compression_level": 1,
            "performance_profile": (
                "b300-extended-native-preserved-v1"
                if settings.modifications or settings.kit.startswith("SQK-RNA")
                else (
                    "b300-hac-v6-batch4096-v1"
                    if quality == "hac"
                    else (
                        "b300-sup-v5.2-native-preserved-v1"
                        if quality == "sup"
                        else "b300-" + quality + "-v5.2-unified-cublas-v2"
                    )
                )
            ),
            "compute_seconds": round(time.monotonic() - started, 2),
        }
        (folder / "provenance.json").write_text(json.dumps(provenance, indent=2))
        result = {
            "ok": True,
            "reads": read_count,
            "total_reads": report["total_reads"],
            "filtered_reads": report["filtered_reads"],
            "compute_seconds": provenance["compute_seconds"],
        }
    except Exception as exc:
        log = folder / "worker.log"
        tail = log.read_text(errors="replace")[-1800:] if log.exists() else ""
        result = {"ok": False, "error": str(exc) + "\n" + tail}
    storage.commit()
    return result
