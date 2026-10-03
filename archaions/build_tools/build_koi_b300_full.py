from pathlib import Path
import modal

app = modal.App('archaions-koi-b300-full-build')
build_volume = modal.Volume.from_name('archaions-dorado-b300-build', create_if_missing=True)
data_volume = modal.Volume.from_name('archaions-basecalling-test')
SOURCE_COMMIT = '8b8fc5d36a9c0baab262a743cb175b5878e38ca6'
image = (
    modal.Image.from_registry('nvidia/cuda:13.1.1-devel-ubuntu22.04',add_python='3.11')
    .entrypoint([])
    .apt_install('build-essential','git','curl','ca-certificates','zlib1g-dev','autoconf','automake',
                 'libtool','pkg-config','samtools','libgomp1')
    .pip_install('cmake==3.31.6','ninja==1.11.1.3')
    .add_local_file(Path(__file__).parent/'architectures.patch','/opt/architectures.patch',copy=True)
)


@app.function(image=image,cpu=8,memory=32768,timeout=1200,volumes={'/build':build_volume})
def build():
    import hashlib
    import json
    import re
    import shutil
    import shlex
    import subprocess
    import tempfile
    build_volume.reload()
    original=next(Path('/build/cmake-build/download').rglob('libkoi.a'))
    dest=Path('/build/koi-b300-full'); dest.mkdir(exist_ok=True)
    (dest/'bin').mkdir(exist_ok=True)
    logs=dest/'logs'; logs.mkdir(exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='koi-build-'))
    def digest(path):
        with Path(path).open('rb') as f:
            return hashlib.file_digest(f,'sha256').hexdigest()
    def run(args,name,cwd=None,timeout=180):
        print('Starting',name,flush=True)
        with (logs/(name+'.log')).open('w') as log:
            p=subprocess.run(args,cwd=cwd,stdout=log,stderr=log,timeout=timeout)
        if p.returncode:
            print((logs/(name+'.log')).read_text(errors='replace')[-10000:],flush=True)
            raise RuntimeError(name+' failed')
        print('Finished',name,flush=True)
    result={'original_koi_sha256':digest(original),'targets':['sm_90','sm_103'],
            'method':'Experimental reassembly of embedded PTX modules; no source-level Koi rebuild',
            'modified_objects':[], 'skipped_objects':[]}
    try:
        patched=work/'libkoi.a'
        replacements=[]
        shutil.copyfile(original,patched)
        for member in ['convolution.cu.o', 'ctc_simple.cu.o', 'decoder_lib.cu.o', 'lstm_step.cu.o', 'small_lstm.cu.o', 'transformer.cu.o', 'util_kernels.cu.o', 'utils_lstm.cu.o', 'linear_volta_stub.cu.o', 'factorised_linear.cu.o', 'linear.cu.o', 'cutlass_lstm.cu.o', 'factorised_lstm.cu.o', 'tensor_lstm.cu.o', 'cutlass_linear.cu.o', 'vcs_tx_conv.cu.o', 'vcs_tx_attn.cu.o']:
            folder=work/member; folder.mkdir()
            obj=folder/member
            with obj.open('wb') as f:
                subprocess.run(['ar','p',str(original),member],stdout=f,check=True)
            run(['cuobjdump','--extract-ptx','all',str(obj)],'extract-'+member,cwd=folder)
            sources=list(folder.glob('*.ptx'))
            if len(sources)!=1:
                raise RuntimeError('Expected exactly one PTX module per object')
            source=sources[0].read_text()
            if len(re.findall(r'^\.target sm_120\s*$',source,re.M))!=1:
                raise RuntimeError('Unexpected PTX target; refusing automatic retarget')
            (dest/(member+'.original.ptx')).write_text(source)
            images=[]
            rebuilt_arches=[]
            for arch in [103,90]:
                ptx=folder/(str(arch)+'.ptx'); cubin=folder/(str(arch)+'.cubin')
                ptx.write_text(re.sub(r'^\.target sm_120\s*$', '.target sm_'+str(arch),source,flags=re.M))
                try:
                    run(['ptxas','-arch=sm_'+str(arch),'-O3',str(ptx),'-o',str(cubin)],'ptxas-'+member+'-'+str(arch),timeout=300)
                except RuntimeError:
                    if arch==103:
                        result['skipped_objects'].append({'name':member,'reason':'PTX assembly for sm_103 failed'})
                        break
                    continue
                rebuilt_arches.append(arch)
                images+=['--image3=kind=elf,sm='+str(arch)+',file='+str(cubin)]
                shutil.copyfile(cubin,dest/(member+'.sm_'+str(arch)+'.cubin'))
            if 103 not in rebuilt_arches:
                continue
            run(['cuobjdump','--extract-elf','all',str(obj)],'extract-elf-'+member,cwd=folder)
            for existing in folder.glob('*.cubin'):
                match=re.search(r'\.sm_([0-9]+[a-z]?)\.cubin$',existing.name)
                if not match:
                    continue
                arch=match.group(1)
                if 90 in rebuilt_arches and arch in ['90','90a']:
                    continue
                images+=['--image3=kind=elf,sm='+arch+',file='+str(existing)]
            images+=['--image3=kind=ptx,sm=120,file='+str(sources[0])]
            fatbin=folder/'rebuilt.fatbin'
            run(['fatbinary','--64','--create='+str(fatbin),*images],'fatbinary-'+member)
            run(['readelf','-SW',str(obj)],'sections-'+member)
            run(['objcopy','--update-section','.nv_fatbin='+str(fatbin),str(obj)],'replace-'+member)
            replacements.append(str(obj))
            run(['cuobjdump','--list-elf',str(obj)],'audit-'+member)
            result['modified_objects'].append({'name':member,'ptx_sha256':hashlib.sha256(source.encode()).hexdigest(),
                'patched_object_sha256':digest(obj), 'rebuilt_arches':rebuilt_arches})
        run(['ar','rD',str(patched),*replacements],'archive-all',timeout=300)
        shutil.copyfile(patched,dest/'libkoi.a')
        patched=dest/'libkoi.a'
        result['patched_koi_sha256']=digest(patched)
        out=Path('/build/official-build')
        command=subprocess.check_output(['ninja','-t','commands','dorado'],cwd=out,text=True).splitlines()[-1]
        args=shlex.split(command)
        if args[:2]==[':','&&']: args=args[2:]
        if args[-2:]==['&&',':']: args=args[:-2]
        if any(a in ['&&',';','|'] for a in args):
            raise RuntimeError('Unexpected compound linker command')
        if args.count(str(original))!=1:
            raise RuntimeError('Expected exactly one original Koi linker input')
        args[args.index(str(original))]=str(patched)
        target=dest/'bin/dorado'
        args[args.index('-o')+1]=str(target)
        (dest/'link-args.json').write_text(json.dumps(args,indent=2))
        run(args,'link',cwd=out,timeout=300)
        run([str(target),'--version'],'version')
        result['binary_sha256']=digest(target)
        result['baseline_binary_sha256']=digest(out/'bin/dorado')
        result['ok']=True
    except Exception as exc:
        result.update(ok=False,error=str(exc))
    finally:
        (dest/'build-result.json').write_text(json.dumps(result,indent=2))
        build_volume.commit()
    return result

@app.local_entrypoint()
def main():
    import json
    result=build.remote()
    print(json.dumps(result,indent=2))
    target=Path(__file__).parent/'koi-full-rebuild'; target.mkdir(exist_ok=True)
    (target/'build-result.json').write_text(json.dumps(result,indent=2))
    for entry in build_volume.listdir('/koi-b300-full/logs'):
        name=Path(entry.path).name
        with (target/name).open('wb') as f:
            for chunk in build_volume.read_file('/koi-b300-full/logs/'+name): f.write(chunk)
