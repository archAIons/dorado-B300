from pathlib import Path
import modal

app = modal.App('archaions-b300-multimodel-build')
volume = modal.Volume.from_name('archaions-dorado-b300-build')
image = (
    modal.Image.from_registry('nvidia/cuda:13.1.1-devel-ubuntu22.04', add_python='3.11')
    .entrypoint([])
    .apt_install('build-essential', 'git', 'curl', 'ca-certificates', 'zlib1g-dev',
                 'autoconf', 'automake', 'libtool', 'pkg-config', 'samtools', 'libgomp1')
    .pip_install('cmake==3.31.6', 'ninja==1.11.1.3')
    .add_local_file(Path(__file__).resolve().parents[2]/'dorado/basecall/CudaCaller.cpp',
                    '/opt/CudaCaller.cpp', copy=True)
    .add_local_file(Path(__file__).resolve().parents[2]/'dorado/torch_utils/cuda_utils.cpp',
                    '/opt/cuda_utils.cpp', copy=True)
)

@app.function(image=image, cpu=8, memory=32768, timeout=900, volumes={'/build': volume})
def build():
    import hashlib
    import json
    import shlex
    import subprocess

    volume.reload()
    out = Path('/build/koi-b300-multimodel-v2')
    out.mkdir(exist_ok=True)
    (out/'bin').mkdir(exist_ok=True)
    baseline = Path('/build/official-build')
    def digest(path):
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()
    koi = Path('/build/koi-b300-full/libkoi.a')
    assert digest(koi) == '92dc1cc8ec0e3f9f773567175a844c2a6a9c3e572dc1ae71f9b41fa197dbe6fe'
    manifest = out/'build-result.json'
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous.get('ok'):
            if previous['cuda_caller_sha256'] != digest('/opt/CudaCaller.cpp') or previous['source_hashes']['cuda_utils.cpp'] != digest('/opt/cuda_utils.cpp'):
                raise RuntimeError('Immutable build path already contains another source revision; choose a new path')
            if previous['binary_sha256'] != digest(out/'bin/dorado'):
                raise RuntimeError('Existing immutable binary checksum mismatch')
            return previous
    commands = subprocess.check_output(['ninja','-t','commands','dorado'],cwd=baseline,text=True).splitlines()
    link = json.loads(Path('/build/koi-b300-full/link-args.json').read_text())
    import shutil
    source_hashes = {}
    for source, include in [('CudaCaller.cpp','basecall'),('cuda_utils.cpp','torch_utils')]:
        matches = [shlex.split(c) for c in commands if ' -c ' in c and c.endswith('/'+source)]
        if len(matches) != 1:
            raise RuntimeError(f'Expected one compile command for {source}; found {len(matches)}')
        args = matches[0]
        original_object = args[args.index('-o')+1]
        tuned_object = out/(source+'.o')
        args[args.index('-o')+1] = str(tuned_object)
        args[args.index('-c')+1] = '/opt/'+source
        args += ['-I/build/src/dorado/'+include]
        if '-MF' in args: args[args.index('-MF')+1] = str(tuned_object)+'.d'
        (out/(source+'-compile-args.json')).write_text(json.dumps(args,indent=2))
        with (out/(source+'-compile.log')).open('w') as log:
            proc = subprocess.run(args,cwd=baseline,stdout=log,stderr=log,timeout=600)
        if proc.returncode:
            print((out/(source+'-compile.log')).read_text()[-12000:],flush=True)
            volume.commit()
            raise RuntimeError('Compilation failed: '+source)
        owners=[]
        for a in dict.fromkeys(a for a in link if a.endswith('.a')):
            path = Path(a) if Path(a).is_absolute() else baseline/a
            members=subprocess.check_output(['ar','t',str(path)],text=True).splitlines()
            if Path(original_object).name in members: owners.append((a,path))
        if len(owners)!=1: raise RuntimeError(f'Ambiguous archive: {owners}')
        old_arg,old_path=owners[0]
        library=out/old_path.name
        shutil.copyfile(old_path,library)
        subprocess.run(['ar','rD',str(library),str(tuned_object)],check=True)
        link=[str(library) if a==old_arg else a for a in link]
        source_hashes[source]=digest('/opt/'+source)
    binary = out/'bin/dorado'
    torch_lib = Path('/build/official-torch/libtorch/lib')
    cublas = list(torch_lib.glob('libcublas-*.so.13'))
    assert len(cublas) == 1
    replaced = [a for a in link if a.endswith('/libcublas.so')]
    assert len(replaced) == 1, replaced
    link = [str(cublas[0]) if a == replaced[0] else a for a in link]
    link[link.index('-o')+1] = str(binary)
    (out/'link-args.json').write_text(json.dumps(link,indent=2))
    with (out/'link.log').open('w') as log:
        subprocess.run(link,cwd=baseline,stdout=log,stderr=log,check=True,timeout=180)
    subprocess.run([str(binary),'--version'],check=True)
    result = {'ok':True,'binary_sha256':digest(binary),'koi_sha256':digest(koi),
              'cuda_caller_sha256':digest('/opt/CudaCaller.cpp'),
              'source_hashes':source_hashes,
              'cublas_library':str(cublas[0]), 'cublas_sha256':digest(cublas[0]),
              'baseline_binary_sha256':digest('/build/koi-b300-full/bin/dorado')}
    (out/'build-result.json').write_text(json.dumps(result,indent=2))
    volume.commit()
    return result

@app.local_entrypoint()
def main():
    import json
    result = build.remote()
    output = Path(__file__).parent/'multimodel-core-build-v2'
    output.mkdir(exist_ok=True)
    (output/'build-result.json').write_text(json.dumps(result,indent=2))
    for name in ['CudaCaller.cpp-compile.log','cuda_utils.cpp-compile.log','link.log','link-args.json']:
        with (output/name).open('wb') as out:
            for chunk in volume.read_file('/koi-b300-multimodel-v2/'+name):
                out.write(chunk)
    print(json.dumps(result,indent=2))
