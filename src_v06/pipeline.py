"""App entry point for motion protection, including preparation and audio."""
from pathlib import Path
import os,sys,json,subprocess,importlib.util,time,uuid
ROOT=Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parent.parent))).resolve()
os.environ['NIGHTFRAME_HOME']=str(ROOT)
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def run(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if not source.is_file() or output==source or output.exists():raise ValueError('请选择有效的源片和新的导出文件名。')
    spec=importlib.util.spec_from_file_location('nightframe_v03',ROOT/'src_v03/pipeline.py')
    legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    job=ROOT/'work/v06/jobs'/uuid.uuid4().hex;job.mkdir(parents=True)
    prior=job/'prior.mp4'
    base=legacy.run(source,prior,mode='model',log=lambda s:print(s,flush=True))
    cache=Path(base['cache'])
    from motion_deflicker import run as restore
    print('MOTION_STAGE restore',flush=True)
    silent=job/'motion.mp4';report=restore(cache/'proxy.mp4',cache/'neural.mp4',silent)
    print('MOTION_STAGE export',flush=True)
    data=legacy.probe(source);audio=next((s for s in data['streams'] if s['codec_type']=='audio'),None)
    output.parent.mkdir(parents=True,exist_ok=True)
    args=['ffmpeg','-y','-hide_banner','-v','error','-i',str(silent)]
    if audio:args+=['-i',str(source),'-map','0:v:0','-map','1:a:0','-c:a','copy' if audio.get('codec_name')=='aac' else 'aac']
    else:args+=['-map','0:v:0']
    args+=['-c:v','copy','-t',str(report['frames']/30),'-map_metadata','-1','-movflags','+faststart',str(output)]
    subprocess.run(args,check=True,creationflags=FLAGS)
    final=legacy.video(output)
    if int(final['nb_frames'])!=report['frames']:raise RuntimeError('运动保护导出帧数不一致')
    subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',str(output),'-f','null','NUL'],check=True,creationflags=FLAGS)
    report.update(mode='motion',output=str(output),source=str(source),full_decode='passed',frame_interpolation=False)
    output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args();run(a.input,a.output)
