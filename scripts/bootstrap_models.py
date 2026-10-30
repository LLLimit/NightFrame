"""Fetch pinned upstream sources/weights. No private assets are distributed."""
from pathlib import Path, PurePosixPath
import argparse, json, hashlib, os, subprocess, sys, zipfile, struct, zlib
from urllib.request import Request,urlopen
from concurrent.futures import ThreadPoolExecutor
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

def unpack(archive,destination,strip=1,only=None):
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT):raise ValueError('Extraction outside project')
    with zipfile.ZipFile(archive) as package:
        if package.testzip():raise RuntimeError('ZIP CRC failed')
        for item in package.infolist():
            p=PurePosixPath(item.filename)
            if p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe archive member')
            parts=p.parts[strip:]
            if not parts or item.is_dir():continue
            if only is not None and PurePosixPath(*parts).as_posix() not in only:continue
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

def install_rife(rife):
    """Fetch only the five pinned entries of the large official Windows ZIP."""
    dest=ROOT/'research/RIFE'
    needed=[f for f in rife['files'] if not (dest/f['path']).is_file() or sha(dest/f['path'])!=f['sha256']]
    if not needed:return
    url=rife['url']
    def byte_range(start,end=None):
        header=f'bytes={start}-{end}' if end is not None else f'bytes={start}'
        req=Request(url,headers={'Range':header,'User-Agent':'NightFrame-model-setup/1.1.1','Accept-Encoding':'identity'})
        with urlopen(req,timeout=90) as response:
            if response.status!=206:raise RuntimeError('Server did not honor ZIP byte ranges; retry setup later.')
            content_range=response.headers.get('Content-Range','')
            import re
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',content_range)
            if not match:raise RuntimeError('Missing ZIP Content-Range')
            first,last,total=map(int,match.groups())
            if end is not None and (first,last)!=(start,end):raise RuntimeError('ZIP range mismatch')
            data=response.read(last-first+2)
            if len(data)!=last-first+1:raise RuntimeError('Truncated ZIP range')
            return data,first,total
    archive_bytes=rife['archive_bytes']
    tail,first,total=byte_range(archive_bytes-65557,archive_bytes-1)
    if total!=archive_bytes:raise RuntimeError('RIFE release size changed')
    index=tail.rfind(b'PK\x05\x06')
    if index<0:raise RuntimeError('ZIP central directory missing')
    fields=struct.unpack_from('<4s4H2LH',tail,index)
    size,offset=fields[5],fields[6]
    if size>2*1024*1024 or offset+size>total:raise RuntimeError('Unsupported ZIP directory')
    directory=tail[offset-first:offset-first+size] if offset>=first else byte_range(offset,offset+size-1)[0]
    entries={};pos=0
    while pos<len(directory):
        row=struct.unpack_from('<4s6H3L5H2L',directory,pos)
        if row[0]!=b'PK\x01\x02':raise RuntimeError('Invalid ZIP directory entry')
        flags,method,crc,compressed,plain=row[3],row[4],row[7],row[8],row[9]
        name_len,extra_len,comment_len=row[10:13]
        name=directory[pos+46:pos+46+name_len].decode('utf-8' if flags&0x800 else 'cp437')
        parts=PurePosixPath(name).parts[1:]
        relative=PurePosixPath(*parts).as_posix() if parts else ''
        entries[relative]=(flags,method,crc,compressed,plain,row[16])
        pos+=46+name_len+extra_len+comment_len
    def fetch(file):
        flags,method,crc,compressed,plain,offset=entries[file['path']]
        if flags&1 or method not in (0,8) or plain>128*1024*1024:raise RuntimeError('Unsupported RIFE ZIP entry')
        local=byte_range(offset,offset+29)[0]
        fields=struct.unpack('<4s5H3L2H',local)
        if fields[0]!=b'PK\x03\x04':raise RuntimeError('Invalid local ZIP header')
        start=offset+30+fields[9]+fields[10]
        data=byte_range(start,start+compressed-1)[0]
        if method==8:
            decompressor=zlib.decompressobj(-15);raw=decompressor.decompress(data,plain+1)
            if not decompressor.eof:raise RuntimeError('Invalid compressed RIFE entry')
        else:raw=data
        if len(raw)!=plain or zlib.crc32(raw)&0xffffffff!=crc or hashlib.sha256(raw).hexdigest()!=file['sha256']:
            raise RuntimeError('RIFE entry CRC/SHA256 mismatch: '+file['path'])
        target=(dest/file['path']).resolve()
        if not target.is_relative_to(dest.resolve()):raise ValueError('Unsafe RIFE destination')
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix(target.suffix+'.download');temporary.write_bytes(raw);os.replace(temporary,target)
        print('Verified RIFE component: '+file['path'],flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(fetch,needed))

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
    install_rife(rife)
    for file in rife['files']:
        path=dest/file['path']
        if not path.is_file() or sha(path)!=file['sha256']:raise RuntimeError('RIFE file validation failed: '+str(path))
    for directory in ['work/tmp','work/desktop/jobs','outputs/v03','outputs/NightFrame','profiles']:(ROOT/directory).mkdir(parents=True,exist_ok=True)
    print('Pinned sources and model checks passed.')

if __name__=='__main__':main()
