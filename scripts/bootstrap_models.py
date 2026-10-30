"""Fetch pinned upstream sources/weights. No private assets are distributed."""
from pathlib import Path, PurePosixPath
import argparse, json, hashlib, os, subprocess, sys, zipfile
ROOT=Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parent.parent))).resolve()

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def download(url,target,expected=None):
    if target.is_file() and (expected is None or sha(target)==expected):return
    target.parent.mkdir(parents=True,exist_ok=True)
    part=target.with_suffix(target.suffix+'.download')
    subprocess.run(['curl.exe','--location','--fail','--retry','3','--output',str(part),url],check=True)
    if expected and sha(part)!=expected:raise RuntimeError('SHA256 mismatch: '+target.name)
    os.replace(part,target)

def unpack(archive,destination,strip=1):
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT):raise ValueError('Extraction outside project')
    with zipfile.ZipFile(archive) as package:
        if package.testzip():raise RuntimeError('ZIP CRC failed')
        for item in package.infolist():
            p=PurePosixPath(item.filename)
            if p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe archive member')
            parts=p.parts[strip:]
            if not parts or item.is_dir():continue
            if (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Archive symlink rejected')
            target=destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination):raise ValueError('Unsafe archive member')
            target.parent.mkdir(parents=True,exist_ok=True)
            with package.open(item) as src,target.open('wb') as dst:
                import shutil;shutil.copyfileobj(src,dst,8*1024*1024)

def checkout(repo,revision,dest):
    marker=dest/'.nightframe-source.json'
    if dest.exists():
        if marker.exists() and json.loads(marker.read_text('utf-8'))['revision']==revision:return
        if (dest/'.git').exists():
            current=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True).strip()
            if current==revision:return
        raise RuntimeError('Existing source directory was retained; check its revision: '+str(dest))
    archive=ROOT/'work/downloads'/(repo.split('/')[-1]+'-'+revision+'.zip')
    download('https://github.com/'+repo+'/archive/'+revision+'.zip',archive)
    unpack(archive,dest)
    marker.write_text(json.dumps({'repository':repo,'revision':revision}),encoding='utf-8')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--models',choices=['deflicker','all'],default='deflicker');parser.add_argument('--plan',action='store_true');a=parser.parse_args()
    plan=json.loads((ROOT/'models.lock.json').read_text('utf-8'))
    selected=[m for m in plan['models'] if m['feature']=='deflicker' or a.models=='all']
    sources=[s for s in plan['sources'] if s['feature']=='deflicker' or a.models=='all']
    if a.plan:
        print(json.dumps({'root':str(ROOT),'sources':sources,'models':selected,'rife':plan['rife']},ensure_ascii=False,indent=2));return
    for source in sources:checkout(source['repository'],source['revision'],ROOT/source['directory'])
    for model in selected:download(model['url'],ROOT/model['path'],model['sha256'])
    rife=plan['rife'];dest=ROOT/'research/RIFE'
    if not (dest/'rife-ncnn-vulkan.exe').is_file():
        archive=ROOT/'work/downloads/rife-windows.zip';download(rife['url'],archive,rife['sha256']);unpack(archive,dest)
    for file in rife['files']:
        path=dest/file['path']
        if not path.is_file() or sha(path)!=file['sha256']:raise RuntimeError('RIFE file validation failed: '+str(path))
    for directory in ['work/tmp','work/desktop/jobs','outputs/v03','outputs/NightFrame','profiles']:(ROOT/directory).mkdir(parents=True,exist_ok=True)
    print('Pinned sources and model checks passed.')

if __name__=='__main__':main()
