from pathlib import Path
import modal

app = modal.App("archaions-b300-core4096-build")
volume = modal.Volume.from_name("archaions-dorado-b300-build")
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
        Path(__file__).resolve().parents[2] / "dorado/basecall/CudaCaller.cpp",
        "/opt/CudaCaller.cpp",
        copy=True,
    )
)


@app.function(image=image, cpu=8, memory=32768, timeout=900, volumes={"/build": volume})
def build():
    import hashlib
    import json
    import shlex
    import subprocess

    volume.reload()
    out = Path("/build/koi-b300-tuned-4096")
    out.mkdir(exist_ok=True)
    (out / "bin").mkdir(exist_ok=True)
    baseline = Path("/build/official-build")

    def digest(path):
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    koi = Path("/build/koi-b300-full/libkoi.a")
    assert (
        digest(koi)
        == "92dc1cc8ec0e3f9f773567175a844c2a6a9c3e572dc1ae71f9b41fa197dbe6fe"
    )
    manifest = out / "build-result.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous.get("ok"):
            if previous["cuda_caller_sha256"] != digest("/opt/CudaCaller.cpp"):
                raise RuntimeError(
                    "Immutable build path already contains another source revision; choose a new path"
                )
            if previous["binary_sha256"] != digest(out / "bin/dorado"):
                raise RuntimeError("Existing immutable binary checksum mismatch")
            return previous
    commands = subprocess.check_output(
        ["ninja", "-t", "commands", "dorado"], cwd=baseline, text=True
    ).splitlines()
    matches = [
        shlex.split(c)
        for c in commands
        if " -c " in c and c.endswith("/CudaCaller.cpp")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one CudaCaller compile command; found {len(matches)}"
        )
    args = matches[0]
    original_object = args[args.index("-o") + 1]
    tuned_object = out / "CudaCaller.cpp.o"
    args[args.index("-o") + 1] = str(tuned_object)
    args[args.index("-c") + 1] = "/opt/CudaCaller.cpp"
    args += ["-I/build/src/dorado/basecall"]
    if "-MF" in args:
        args[args.index("-MF") + 1] = str(out / "CudaCaller.cpp.o.d")
    (out / "compile-args.json").write_text(json.dumps(args, indent=2))
    with (out / "compile.log").open("w") as log:
        proc = subprocess.run(args, cwd=baseline, stdout=log, stderr=log, timeout=600)
    if proc.returncode:
        print((out / "compile.log").read_text()[-12000:], flush=True)
        volume.commit()
        raise RuntimeError("CudaCaller compilation failed")
    link = json.loads(Path("/build/koi-b300-full/link-args.json").read_text())
    archives = [a for a in link if a.endswith(".a")]
    owners = []
    for a in dict.fromkeys(archives):
        path = Path(a) if Path(a).is_absolute() else baseline / a
        members = subprocess.check_output(
            ["ar", "t", str(path)], text=True
        ).splitlines()
        if Path(original_object).name in members:
            owners.append((a, path))
    if len(owners) != 1:
        raise RuntimeError(f"Expected one CudaCaller archive; found {owners}")
    import shutil

    old_arg, old_path = owners[0]
    library = out / old_path.name
    shutil.copyfile(old_path, library)
    subprocess.run(["ar", "rD", str(library), str(tuned_object)], check=True)
    link = [str(library) if a == old_arg else a for a in link]
    binary = out / "bin/dorado"
    link[link.index("-o") + 1] = str(binary)
    (out / "link-args.json").write_text(json.dumps(link, indent=2))
    with (out / "link.log").open("w") as log:
        subprocess.run(
            link, cwd=baseline, stdout=log, stderr=log, check=True, timeout=180
        )
    subprocess.run([str(binary), "--version"], check=True)
    result = {
        "ok": True,
        "binary_sha256": digest(binary),
        "koi_sha256": digest(koi),
        "cuda_caller_sha256": digest("/opt/CudaCaller.cpp"),
        "changed_source": "dorado/basecall/CudaCaller.cpp",
        "baseline_binary_sha256": digest("/build/koi-b300-full/bin/dorado"),
    }
    (out / "build-result.json").write_text(json.dumps(result, indent=2))
    volume.commit()
    return result


@app.local_entrypoint()
def main():
    import json

    result = build.remote()
    output = Path(__file__).parent / "tuned4096-core-build"
    output.mkdir(exist_ok=True)
    (output / "build-result.json").write_text(json.dumps(result, indent=2))
    for name in ["compile.log", "link.log", "compile-args.json", "link-args.json"]:
        with (output / name).open("wb") as out:
            for chunk in volume.read_file("/koi-b300-tuned/" + name):
                out.write(chunk)
    print(json.dumps(result, indent=2))
