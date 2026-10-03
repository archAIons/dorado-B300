from pathlib import Path
import modal

app = modal.App("archaions-dorado-b300-port")
build_volume = modal.Volume.from_name(
    "archaions-dorado-b300-build", create_if_missing=True
)
data_volume = modal.Volume.from_name("archaions-basecalling-test")
SOURCE_COMMIT = "8b8fc5d36a9c0baab262a743cb175b5878e38ca6"
image = (
    modal.Image.from_registry("nvidia/cuda:13.1.1-devel-ubuntu22.04", add_python="3.11")
    .entrypoint([])
    .apt_install(
        "build-essential",
        "git",
        "curl",
        "ca-certificates",
        "zlib1g-dev",
        "autoconf",
        "automake",
        "libtool",
        "pkg-config",
        "samtools",
        "libgomp1",
    )
    .pip_install("cmake==3.31.6", "ninja==1.11.1.3")
    .add_local_file(
        Path(__file__).parent / "architectures.patch",
        "/opt/architectures.patch",
        copy=True,
    )
)


@app.function(
    image=image,
    cpu=16,
    memory=65536,
    timeout=7200,
    volumes={"/build": build_volume},
    max_containers=1,
)
def build():
    import json
    import re
    import subprocess
    import time

    build_volume.reload()
    root = Path("/build")
    src = root / "src"
    out = root / "cmake-build"
    logs = root / "logs"
    logs.mkdir(exist_ok=True)

    def run(args, name, timeout=3600, cwd=None):
        print("Starting", name, flush=True)
        start = time.monotonic()
        with (logs / (name + ".log")).open("w") as stream:
            process = subprocess.run(
                args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout
            )
        print(
            "Finished",
            name,
            "exit",
            process.returncode,
            "seconds",
            round(time.monotonic() - start, 1),
            flush=True,
        )
        if process.returncode:
            print(
                (logs / (name + ".log")).read_text(errors="replace")[-12000:],
                flush=True,
            )
            raise RuntimeError(name + " failed")

    result = {"commit": SOURCE_COMMIT, "cuda_architectures": ["90", "103"]}
    try:
        if not (src / ".git").exists():
            run(
                [
                    "git",
                    "clone",
                    "--branch",
                    "release-v2.1",
                    "--depth",
                    "1",
                    "https://github.com/nanoporetech/dorado.git",
                    str(src),
                ],
                "clone",
            )
        current = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=src, text=True
        ).strip()
        if current != SOURCE_COMMIT:
            raise RuntimeError("Source commit differs from the pinned revision")
        patch = Path("/opt/architectures.patch").read_bytes()
        if subprocess.run(
            ["git", "apply", "--reverse", "--check", "/opt/architectures.patch"],
            cwd=src,
            capture_output=True,
        ).returncode:
            run(["git", "apply", "/opt/architectures.patch"], "patch", cwd=src)
        run(
            [
                "cmake",
                "-S",
                str(src),
                "-B",
                str(out),
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCUDAToolkit_ROOT=/usr/local/cuda",
                "-DCMAKE_CUDA_ARCHITECTURES=90;103",
                "-DDORADO_DISABLE_TESTS=ON",
                "-DDORADO_DISABLE_CCACHE=ON",
            ],
            "configure",
        )
        for archive in (out / "download").rglob("libkoi.a"):
            listing = subprocess.run(
                ["cuobjdump", "--list-elf", str(archive)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            (logs / "koi-cubins.txt").write_text(listing.stdout + listing.stderr)
            result["koi_native_architectures"] = sorted(
                set(re.findall(r"sm_[0-9]+[a-z]?", listing.stdout))
            )
            print(
                "Koi native architectures:",
                result["koi_native_architectures"],
                flush=True,
            )
        run(
            ["cmake", "--build", str(out), "--target", "dorado", "-j", "12"],
            "compile",
            timeout=3600,
        )
        run([str(out / "bin/dorado"), "--version"], "version", timeout=60)
        result["ok"] = True
    except Exception as exc:
        result.update(ok=False, error=str(exc))
    finally:
        (root / "build-result.json").write_text(json.dumps(result, indent=2))
        build_volume.commit()
    return result


@app.local_entrypoint()
def main():
    import json

    result = build.remote()
    target = Path(__file__).parent
    (target / "build-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    for name in ["configure.log", "compile.log", "version.log", "koi-cubins.txt"]:
        try:
            with (target / name).open("wb") as stream:
                for chunk in build_volume.read_file("/logs/" + name):
                    stream.write(chunk)
        except FileNotFoundError:
            pass
