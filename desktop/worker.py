"""GPU worker for NightFrame. The desktop process never imports Torch/OpenCV."""
from pathlib import Path
import subprocess,os,sys,json,time,re,math,shutil,hashlib
from paths import ROOT,gpu_python
PYTHON=gpu_python();FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def emit(kind,**data):print('@NIGHTFRAME '+json.dumps({'kind':kind,**data},ensure_ascii=False),flush=True)
def probe(path):return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],creationflags=FLAGS))
def video(path):return next(s for s in probe(path)['streams'] if s['codec_type']=='video')
def run_command(args,parse=None):
 p=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=FLAGS)
 try:
  for line in p.stdout:
   line=line.strip()
   if line:emit('log',text=line)
   if parse:parse(line)
  if p.wait()!=0:raise RuntimeError('处理阶段失败，请展开日志查看原因。')
 finally:
  if p.poll() is None:p.terminate()

def detect_bars(path):
 import cv2,numpy as np
 cap=cv2.VideoCapture(str(path));W=round(cap.get(cv2.CAP_PROP_FRAME_WIDTH));H=round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT));N=round(cap.get(cv2.CAP_PROP_FRAME_COUNT));black=np.ones(H,dtype=bool)
 try:
  for n in sorted(set([0,max(0,N//4),max(0,N//2),max(0,3*N//4),max(0,N-1)])):
   cap.set(cv2.CAP_PROP_POS_FRAMES,n);ok,im=cap.read()
   if not ok:raise RuntimeError('无法读取视频帧')
   # Crop only spatially uniform black rows shared by all sampled times.
   black&=(im.max(2)<8).mean(1)>.995
 finally:cap.release()
 top=0;bottom=0
 while top<H and black[top]:top+=1
 while bottom<H-top and black[H-1-bottom]:bottom+=1
 if top<8:top=0
 if bottom<8:bottom=0
 top=top//2*2;bottom=bottom//2*2
 if H-top-bottom<H*.45:top=bottom=0
 return W,H,top,bottom

def quality(req,job,source):
 info=probe(source);v=next(s for s in info['streams'] if s['codec_type']=='video')
 if v.get('color_transfer') in ('smpte2084','arib-std-b67'):raise ValueError('当前版本处理 SDR 视频。请先将 HDR / HLG 转换为 SDR。')
 preset=req.get('model','sharp7b')
 if preset not in ('3b','sharp7b'):raise ValueError('未知的修复模型')
 width=int(req.get('width',1920))
 if width not in (1920,3840):raise ValueError('未知的输出尺寸')
 model='seedvr2_ema_7b_sharp-Q4_K_M.gguf' if preset=='sharp7b' else 'seedvr2_ema_3b_fp8_e4m3fn.safetensors'
 for f in [ROOT/'research/seedvr2-models'/model,ROOT/'research/seedvr2-models/ema_vae_fp16.safetensors']:
  if not f.is_file():raise ValueError('缺少本地模型：'+f.name)
 emit('progress',progress=2,phase='prepare',message='准备视频与音轨')
 # This is the selected video itself. No original/source donor is searched or loaded.
 master=job/'prepared.mp4';vf='fps=30:start_time=0,scale=w=1920:h=1080:force_original_aspect_ratio=decrease:force_divisible_by=2'
 run_command(['ffmpeg','-y','-hide_banner','-v','error','-i',source,'-vf',vf,'-map','0:v:0','-map','0:a:0?','-c:v','h264_nvenc','-preset','p3','-qp','0','-c:a','aac','-b:a','192k','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-color_range','tv',master])
 mv=video(master);expected=int(mv['nb_frames']);W,H,top,bottom=detect_bars(master);active_h=H-top-bottom
 active=master
 if top or bottom:
  active=job/'active.mp4';emit('log',text=f'保留固定黑边：上 {top}px / 下 {bottom}px')
  run_command(['ffmpeg','-y','-hide_banner','-v','error','-i',master,'-vf',f'crop={W}:{active_h}:0:{top}','-an','-c:v','h264_nvenc','-preset','p3','-qp','0','-colorspace','bt709','-color_range','tv',active])
 emit('progress',progress=8,phase='model',message='加载 '+('7B Sharp' if preset=='sharp7b' else 'SeedVR2 3B'))
 batch=13 if width==1920 else 5;overlap=4 if batch==13 else 1;restored=job/'neural.mp4';phase=0;chunk_index=0;chunk_count=1;batch_index=0;batch_count=1
 def parse(line):
  nonlocal phase,chunk_index,chunk_count,batch_index,batch_count
  m=re.search(r'(?:Processing |Streaming )?chunk\s+(\d+)\s*/\s*(\d+)',line,re.I)
  if m:chunk_index=int(m[1])-1;chunk_count=int(m[2])
  m=re.search(r'Phase\s+(\d+)',line)
  if m:phase=int(m[1])
  m=re.search(r'(?:Encoding|Upscaling|Decoding) batch\s+(\d+)\s*/\s*(\d+)',line,re.I)
  if m:
   batch_index=int(m[1]);batch_count=int(m[2]);bounds={1:(0,.22),2:(.22,.68),3:(.68,.94),4:(.94,1.)};lo,hi=bounds.get(phase,(0,.96));local=lo+(hi-lo)*(batch_index-1)/max(1,batch_count);progress=8+86*(chunk_index+local)/max(1,chunk_count)
   emit('progress',progress=min(94,progress),phase='model',message={1:'编码画面',2:'修复纹理',3:'还原画面',4:'整理输出'}.get(phase,'模型处理中')+f' · {batch_index}/{batch_count} 批')
  m=re.search(r'Block\s+(\d+) swap',line)
  if m and phase==2:
   local=.22+.46*((batch_index-1)+(int(m[1])+1)/(36 if preset=='sharp7b' else 32))/max(1,batch_count)
   emit('progress',progress=min(94,8+86*(chunk_index+local)/max(1,chunk_count)),phase='model',message='修复纹理 · 正在处理连续帧')
 short=round(active_h*width/W/2)*2
 command=[PYTHON,ROOT/'research/SeedVR2/inference_cli.py',active,'--output',restored,'--model_dir',ROOT/'research/seedvr2-models','--dit_model',model,'--resolution',short,'--max_resolution',width,'--batch_size',batch,'--uniform_batch_size','--temporal_overlap',overlap,'--chunk_size','65','--seed','42','--color_correction','lab','--dit_offload_device','cpu','--vae_offload_device','cpu','--blocks_to_swap',36 if preset=='sharp7b' else 32,'--swap_io_components','--vae_encode_tiled','--vae_encode_tile_size','512','--vae_encode_tile_overlap','128','--vae_decode_tiled','--vae_decode_tile_size','512','--vae_decode_tile_overlap','128','--attention_mode','sdpa','--video_backend','ffmpeg','--cache_dit','--cache_vae','--debug']
 run_command(command,parse);nv=video(restored)
 if int(nv.get('nb_frames',0))!=expected:raise RuntimeError('模型输出帧数与输入不一致，未交付不完整结果。')
 emit('progress',progress=95,phase='export',message='恢复画幅并保留原音轨')
 silent=job/'padded.mp4'
 if top or bottom:
  ratio=int(nv['width'])/W;out_h=round(H*ratio/2)*2;y=round(top*ratio/2)*2
  run_command(['ffmpeg','-y','-hide_banner','-v','error','-i',restored,'-vf',f'pad={nv["width"]}:{out_h}:0:{y}:black','-an','-c:v','h264_nvenc','-preset','p4','-cq','17','-b:v','0','-colorspace','bt709','-color_range','tv',silent])
 else:silent=restored
 # Full-video audio comes directly from the selected input. Previews were
 # already trimmed with their audio before entering this function.
 args=['ffmpeg','-y','-hide_banner','-v','error','-i',silent]
 if req.get('audio',True):args+=['-i',source,'-map','0:v:0','-map','1:a:0?','-c:a','copy' if any(s.get('codec_name')=='aac' and s.get('codec_type')=='audio' for s in info['streams']) else 'aac']
 else:args+=['-map','0:v:0']
 result=job/'complete.mp4';args+=['-c:v','copy','-t',str(expected/30),'-map_metadata','-1','-movflags','+faststart',result];run_command(args)
 return result,{'model':model,'frames':expected,'fps':30,'input_policy':'selected video only, no source-video detail donor','coded_black_borders':[top,bottom],'model_batch':batch,'temporal_overlap':overlap}

def main(req):
 started=time.time();source=Path(req['input']).resolve();output=Path(req['output']).resolve();job=Path(req['job_dir']).resolve()
 if not source.is_file():raise ValueError('输入视频不存在')
 if source==output or output.exists():raise ValueError('请选择新的输出文件名，已有文件不会覆盖')
 if not job.is_relative_to((ROOT/'work/desktop/jobs').resolve()):raise ValueError('工作目录无效')
 job.mkdir(parents=True,exist_ok=True);env={'TEMP':str(ROOT/'work/tmp'),'TMP':str(ROOT/'work/tmp'),'HF_HOME':str(ROOT/'work/hf-cache'),'TORCH_HOME':str(ROOT/'work/torch-cache'),'MPLCONFIGDIR':str(ROOT/'work/desktop/mpl'),'XDG_CACHE_HOME':str(ROOT/'work/desktop/cache'),'CUDA_CACHE_PATH':str(ROOT/'work/desktop/cuda-cache'),'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'};os.environ.update(env)
 original_input=source
 if req.get('preview'):
  emit('progress',progress=1,phase='prepare',message='截取 2 秒试处理片段');sample=job/'preview_input.mp4';start=max(0,float(req.get('preview_start',0)));data=probe(source);audio=next((s for s in data['streams'] if s['codec_type']=='audio'),None)
  args=['ffmpeg','-y','-hide_banner','-v','error','-ss',str(start),'-i',source,'-t','2','-map','0:v:0','-map','0:a:0?','-vf','fps=30:start_time=0,scale=w=1920:h=1080:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','h264_nvenc','-preset','p3','-qp','0','-c:a','aac','-b:a','192k',sample];run_command(args);source=sample
 if req['mode']=='deflicker':
  strength=req.get('deflicker_mode','strong')
  if strength not in ('strong','motion'):raise ValueError('未知的去闪烁模式')
  result=job/'deflickered.mp4';emit('progress',progress=3,phase='model',message='启动去闪烁引擎')
  stage=[0]
  def parse(line):
   if strength=='motion' and line.startswith('MOTION_STAGE'):
    emit('progress',progress=25 if 'restore' in line else 95,phase='model' if 'restore' in line else 'export',message='运动对齐与局部恢复' if 'restore' in line else '合入音轨并验证');return
   m=re.search(r'阶段\s*(\d)/4',line)
   if m:stage[0]=int(m[1]);emit('progress',progress=[3,7,17,55,77][stage[0]],phase='model',message=['','准备画面','识别并修复闪烁','稳定灯光','恢复流畅运动'][stage[0]])
   try:
    d=json.loads(line);n=d.get('completed',d.get('frame',0));total=d.get('total',0)
    if total and n:emit('progress',progress=min(94,25+68*n/total if strength=='motion' else [0,3,17,55,77][stage[0]]+16*n/total),phase='model',message=f'去闪烁处理中 · {n}/{total} 帧')
   except (ValueError,TypeError):pass
  command=[PYTHON,ROOT/'src_v06/pipeline.py',source,result] if strength=='motion' else [PYTHON,ROOT/'src_v03/pipeline.py',source,result,'--mode','strong']
  run_command(command,parse)
  details={'mode':strength,'fps':30,'frames':int(video(result)['nb_frames'])}
  if not req.get('audio',True):
   muted=job/'muted.mp4';run_command(['ffmpeg','-y','-hide_banner','-v','error','-i',result,'-map','0:v:0','-an','-c:v','copy',muted]);result=muted
 else:result,details=quality(req,job,source)
 emit('progress',progress=98,phase='export',message='完整解码检查')
 run_command(['ffmpeg','-hide_banner','-v','error','-hwaccel','cuda','-i',result,'-f','null','NUL'])
 output.parent.mkdir(parents=True,exist_ok=True);partial=output.with_name('.'+output.stem+'.'+job.name+'.partial.mp4')
 if partial.exists():raise FileExistsError('临时导出路径已存在')
 try:
  with result.open('rb') as src,partial.open('xb') as dst:shutil.copyfileobj(src,dst,8*1024*1024)
  os.rename(partial,output)
 except BaseException:
  if partial.exists():partial.unlink()
  raise
 report={'app':'NightFrame','version':'1.0.0','input':str(original_input),'output':str(output),'mode':req['mode'],'preview':bool(req.get('preview')),'elapsed_seconds':round(time.time()-started,2),'full_decode':'passed',**details}
 output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');emit('done',progress=100,phase='done',message='处理完成',output=str(output),report=report)

if __name__=='__main__':
 try:main(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
 except Exception as e:emit('error',message=str(e));sys.exit(1)
