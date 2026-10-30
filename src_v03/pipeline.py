from pathlib import Path
import subprocess,hashlib,json,os,shutil,time,uuid
ROOT=Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parent.parent))).resolve();WORK=ROOT/'work/v03';SCRIPTS=ROOT/'src_v03/helpers'
PYTHON=Path(os.environ.get('NIGHTFRAME_PYTHON',str(ROOT/'work/research_env/Scripts/python.exe')))
MODEL=ROOT/'research/Flickerformer/Flickerformer.pth'
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()
def probe(path):
 return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],creationflags=FLAGS))
def video(path):return next(s for s in probe(path)['streams'] if s['codec_type']=='video')
def identity(source):
 data=probe(source);v=next(s for s in data['streams'] if s['codec_type']=='video')
 if v.get('color_transfer') in ('smpte2084','arib-std-b67'):raise ValueError('目前仅支持 SDR；HDR/HLG 需要先转换。')
 scale=min(1,1920/int(v['width']),1080/int(v['height']));w=max(2,round(int(v['width'])*scale/2)*2);h=max(2,round(int(v['height'])*scale/2)*2)
 info={'source_sha256':digest(source),'model_sha256':digest(MODEL),'width':w,'height':h,'fps':30,'model_commit':'56f2bef4a0455002fc75dd355bed3c0985fdfa4c'}
 tag=hashlib.sha256(json.dumps(info,sort_keys=True).encode()).hexdigest()[:16]
 return WORK/'jobs'/tag,info,data
def seed_cache(source,proxy,neural):
 job,info,_=identity(source);job.mkdir(parents=True,exist_ok=True)
 a,b=video(proxy),video(neural)
 if (a['width'],a['height'],a['nb_frames'])!=(b['width'],b['height'],b['nb_frames']):raise ValueError('Seed frame grid mismatch')
 if (a['width'],a['height'])!=(info['width'],info['height']):raise ValueError('Seed resolution mismatch')
 shutil.copy2(proxy,job/'proxy.mp4');shutil.copy2(neural,job/'neural.mp4');info['frames']=int(a['nb_frames']);(job/'cache.json').write_text(json.dumps(info,indent=2));return job
def run(source,output,mode='strong',log=print,cancel=None,state=None):
 source=Path(source).resolve();output=Path(output).resolve()
 if source==output or output.exists():raise FileExistsError('请使用一个新的输出文件名，原视频和已有结果不会覆盖。')
 if not PYTHON.is_file() or not MODEL.is_file():raise RuntimeError('缺少运行环境或去闪烁模型，请先运行 setup_windows.ps1。')
 output.parent.mkdir(parents=True,exist_ok=True);t0=time.time();job,info,data=identity(source);job.mkdir(parents=True,exist_ok=True)
 env=os.environ.copy();env.update({'TEMP':str(ROOT/'work/tmp'),'TMP':str(ROOT/'work/tmp'),'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'})
 def command(args):
  if cancel is not None and cancel.is_set():raise InterruptedError('处理已停止')
  proc=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env,creationflags=FLAGS)
  if state is not None:state['process']=proc
  try:
   for line in proc.stdout:
    if line.strip():log(line.rstrip())
   if proc.wait()!=0:raise RuntimeError('处理阶段失败，请查看上方日志。')
  finally:
   if state is not None:state['process']=None
  if cancel is not None and cancel.is_set():raise InterruptedError('处理已停止')
 log('阶段 1/4：准备 1080p 上限、30 fps 的处理画面。')
 cachepath=job/'cache.json';proxy=job/'proxy.mp4';neural=job/'neural.mp4'
 valid=False
 if cachepath.exists() and proxy.exists() and neural.exists():
  cached=json.loads(cachepath.read_text());valid=all(cached.get(k)==v for k,v in info.items())
 if valid:log('使用已校验的视频和模型缓存。')
 else:
  command(['ffmpeg','-y','-hide_banner','-v','error','-hwaccel','cuda','-hwaccel_output_format','cuda','-i',source,'-vf',f'fps=30:start_time=0,scale_cuda={info["width"]}:{info["height"]}','-an','-c:v','h264_nvenc','-preset','p3','-cq','12',proxy])
 frames=int(video(proxy)['nb_frames']);duration=frames/30
 log('阶段 2/4：Flickerformer 官方模型恢复。')
 if not valid:
  tag='job_'+job.name
  command([PYTHON,SCRIPTS/'neural_infer.py','--input',proxy,'--start','0','--duration',duration,'--tag',tag])
  shutil.copy2(ROOT/'outputs/v03'/f'{tag}_flickerformer.mp4',neural)
  if int(video(neural)['nb_frames'])!=frames:raise RuntimeError('模型输出帧数不一致')
  cached=dict(info,frames=frames);cachepath.write_text(json.dumps(cached,indent=2))
 if mode=='model':silent=neural
 else:
  radius=4 if mode=='strong' else 1;tag='job_'+job.name+'_'+mode
  log(f'阶段 3/4：{2*radius+1} 帧运动补偿曝光积分，优先消除闪烁。')
  command([PYTHON,SCRIPTS/'cycle_prototype.py','--input',neural,'--start','0','--end',duration,'--tag',tag,'--radius',radius,'--mode','mean'])
  keys=ROOT/'outputs/v03'/f'{tag}_cycle_mean_10fps.mp4';silent=job/(mode+'_'+uuid.uuid4().hex[:6]+'_30fps.mp4')
  log('阶段 4/4：RIFE v4.6 恢复平滑的 30 fps 运动。')
  command([PYTHON,SCRIPTS/'interpolate_rife.py',keys,silent,'--frames',frames])
 log('合入原音轨并验证导出。')
 audio=next((s for s in data['streams'] if s['codec_type']=='audio'),None)
 args=['ffmpeg','-y','-hide_banner','-v','error','-i',silent]
 if audio:args+=['-i',source,'-map','0:v:0','-map','1:a:0','-c:a','copy' if audio.get('codec_name')=='aac' else 'aac']
 else:args+=['-map','0:v:0']
 args+=['-c:v','copy','-t',str(duration),'-map_metadata','-1','-movflags','+faststart',output];command(args)
 result=video(output)
 if int(result.get('nb_frames',0))!=frames:raise RuntimeError('最终输出帧数不一致')
 command(['ffmpeg','-hide_banner','-v','error','-i',output,'-f','null','NUL'])
 report={'version':'0.3.0-research','source':str(source),'output':str(output),'mode':mode,**info,'frames':frames,'duration':duration,'elapsed_seconds':round(time.time()-t0,2),'full_decode':'passed','super_resolution':False,'cache':str(job),'papers':['https://arxiv.org/abs/2603.22794','https://arxiv.org/abs/2011.06294'],'limitations':['不保证所有闪烁均能消除。','强模式会降低细节和局部对比度；大运动、遮挡和切换内容可能有拖影。','部分输出帧由 RIFE 插值，不能视为原始采集帧。','尚未加入画质增强或超分辨率。']}
 output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');log('完成：'+str(output));return report
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');p.add_argument('--mode',choices=['strong','balanced','model'],default='strong');a=p.parse_args();run(a.input,a.output,a.mode,log=lambda s:print(s,flush=True))
