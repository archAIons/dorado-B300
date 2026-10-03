from pathlib import Path
import modal
app=modal.App('archaions-sup-preserved-build')
volume=modal.Volume.from_name('archaions-dorado-b300-build')
image=(modal.Image.from_registry('nvidia/cuda:13.1.1-devel-ubuntu22.04',add_python='3.11').entrypoint([]).apt_install('build-essential','zlib1g-dev','libgomp1'))
@app.function(image=image,cpu=8,memory=32768,timeout=900,volumes={'/build':volume})
def build():
    import hashlib,json,re,shutil,subprocess,tempfile
    volume.reload()
    dest=Path('/build/koi-b300-sup-preserved-v1');(dest/'bin').mkdir(parents=True,exist_ok=True)
    def sha(p):
        with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
    original=next(Path('/build/cmake-build/download').rglob('libkoi.a'))
    assert sha(original)=='17cf3083c5db538f1e275b0a41ac19560b5986c143634e8a25540b9735f5805f'
    parent=json.loads(Path('/build/koi-b300-full/build-result.json').read_text())
    work=Path(tempfile.mkdtemp(prefix='sup-preserve-'))
    archive=work/'libkoi.a';shutil.copyfile(original,archive)
    evidence=[]
    for item in parent['modified_objects']:
        member=item['name'];folder=work/member;folder.mkdir();obj=folder/member
        with obj.open('wb') as f:subprocess.run(['ar','p',str(original),member],stdout=f,check=True)
        subprocess.run(['cuobjdump','--extract-elf','all',str(obj)],cwd=folder,stdout=subprocess.DEVNULL,check=True)
        subprocess.run(['cuobjdump','--extract-ptx','all',str(obj)],cwd=folder,stdout=subprocess.DEVNULL,check=True)
        preserved={};images=[]
        for cubin in sorted(folder.glob('*.cubin')):
            match=re.search(r'\.sm_([0-9]+[a-z]?)\.cubin$',cubin.name)
            assert match,cubin
            arch=match[1];assert arch!='103'
            preserved[arch]=sha(cubin)
            images.append('--image3=kind=elf,sm='+arch+',file='+str(cubin))
        ptxs=list(folder.glob('*.ptx'));assert len(ptxs)==1
        sm103=Path('/build/koi-b300-full')/(member+'.sm_103.cubin');assert sm103.exists()
        images+=['--image3=kind=elf,sm=103,file='+str(sm103),'--image3=kind=ptx,sm=120,file='+str(ptxs[0])]
        fatbin=folder/'rebuilt.fatbin'
        subprocess.run(['fatbinary','--64','--create='+str(fatbin),*images],check=True)
        subprocess.run(['objcopy','--update-section','.nv_fatbin='+str(fatbin),str(obj)],check=True)
        audit=folder/'audit';audit.mkdir()
        subprocess.run(['cuobjdump','--extract-elf','all',str(obj)],cwd=audit,stdout=subprocess.DEVNULL,check=True)
        actual={re.search(r'\.sm_([0-9]+[a-z]?)\.cubin$',p.name)[1]:sha(p) for p in audit.glob('*.cubin')}
        assert all(actual[a]==h for a,h in preserved.items())
        assert actual['103']==sha(sm103)
        subprocess.run(['ar','rD',str(archive),str(obj)],check=True)
        evidence.append({'member':member,'native_sha256':preserved,'sm103_sha256':sha(sm103)})
    shutil.copyfile(archive,dest/'libkoi.a')
    args=json.loads(Path('/build/koi-b300-multimodel-v2/link-args.json').read_text())
    args[args.index('/build/koi-b300-full/libkoi.a')]=str(dest/'libkoi.a')
    args[args.index('-o')+1]=str(dest/'bin/dorado')
    subprocess.run(args,cwd='/build/official-build',check=True,timeout=180)
    result={'ok':True,'binary_sha256':sha(dest/'bin/dorado'),'koi_sha256':sha(dest/'libkoi.a'),'original_koi_sha256':sha(original),'preserved_images':evidence}
    (dest/'build-result.json').write_text(json.dumps(result,indent=2));volume.commit();return result
@app.local_entrypoint()
def main():
    import json
    result=build.remote()
    Path(__file__).with_name('sup-preserved-build.json').write_text(json.dumps(result,indent=2))
    print({k:v for k,v in result.items() if k!='preserved_images'})
