"""Local inference adapter for the official Flickerformer checkpoint.

Architecture and weights remain in the unmodified upstream checkout. The
training framework initializer is skipped; its actual Registry module is used.
"""
from pathlib import Path
import sys,types,importlib.util,os,argparse,time,json
ROOT=Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parents[2]))).resolve();os.environ['TEMP']=str(ROOT/'work/tmp');os.environ['TMP']=os.environ['TEMP']
import torch,cv2,numpy as np
import torch.nn.functional as F
REPO=ROOT/'research/Flickerformer'
def load_model(device='cuda'):
 for name,path in [('basicsr',REPO/'basicsr'),('basicsr.utils',REPO/'basicsr/utils')]:
  module=types.ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
 name='basicsr.utils.registry';spec=importlib.util.spec_from_file_location(name,REPO/'basicsr/utils/registry.py');reg=importlib.util.module_from_spec(spec);sys.modules[name]=reg;spec.loader.exec_module(reg)
 spec=importlib.util.spec_from_file_location('flickerformer_official',REPO/'basicsr/archs/Flickerformer_arch.py');arch=importlib.util.module_from_spec(spec);spec.loader.exec_module(arch)
 model=arch.Flickerformer()
 checkpoint=torch.load(REPO/'Flickerformer.pth',map_location='cpu',weights_only=True)
 params=checkpoint.get('params_ema',checkpoint.get('params',checkpoint))
 model.load_state_dict(params,strict=True);return model.to(device).eval()
def infer(model,ims,device,side=512):
 x=torch.from_numpy(np.stack([cv2.cvtColor(im,cv2.COLOR_BGR2RGB) for im in ims])).permute(0,3,1,2).float().div(255).to(device)
 small=F.interpolate(x,size=(side,side),mode='bilinear',align_corners=False)
 packed=small.reshape(1,9,side,side)
 with torch.inference_mode():pred=model(packed)
 delta=F.interpolate(pred-small[1:2],size=ims[1].shape[:2],mode='bilinear',align_corners=False)
 out=(x[1:2]+delta).clamp(0,1)[0].permute(1,2,0).mul(255).byte().cpu().numpy()
 return cv2.cvtColor(out,cv2.COLOR_RGB2BGR)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--start',type=float,default=4);p.add_argument('--duration',type=float,default=7);p.add_argument('--step',type=int,default=1);p.add_argument('--tag',default='sample');p.add_argument('--device',default='cuda');p.add_argument('--input',default=str(ROOT/'work/v02/source_cfr.mp4'));args=p.parse_args()
 torch.set_num_threads(4);cv2.setNumThreads(2)
 if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA is unavailable')
 model=load_model(args.device);print('Loaded official checkpoint strictly',torch.__version__,args.device,flush=True)
 cap=cv2.VideoCapture(args.input);first=round(args.start*30);last=min(round(cap.get(cv2.CAP_PROP_FRAME_COUNT)),first+round(args.duration*30));cap.set(cv2.CAP_PROP_POS_FRAMES,max(0,first-1));frames={};nextread=max(0,first-1)
 def ensure(index):
  global nextread
  while nextread<=min(last-1,index):
   ok,im=cap.read()
   if not ok:break
   frames[nextread]=im;nextread+=1
 ensure(first+1)
 height,width=frames[first].shape[:2]
 import subprocess
 output=ROOT/'outputs/v03'/f'{args.tag}_flickerformer.mp4';fps=30/args.step
 enc=subprocess.Popen(['ffmpeg','-y','-hide_banner','-v','error','-f','rawvideo','-pixel_format','bgr24','-video_size',f'{width}x{height}','-framerate',str(fps),'-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p3','-cq','14','-b:v','0','-pix_fmt','yuv420p','-movflags','+faststart',str(output)],stdin=subprocess.PIPE)
 t0=time.time();count=0
 for n in range(first,last,args.step):
  ensure(n+1)
  ims=[frames[max(min(frames),n-1)],frames[n],frames[min(max(frames),n+1)]]
  result=infer(model,ims,args.device)
  active=np.flatnonzero((frames[n].max(2)>24).mean(1)>.02)
  if len(active):result[:active[0]]=frames[n][:active[0]];result[active[-1]+1:]=frames[n][active[-1]+1:]
  enc.stdin.write(memoryview(result).cast('B'));count+=1
  if count%15==0:print(count,'frames',round(time.time()-t0,1),'seconds',flush=True)
  for old in [j for j in frames if j<n-1]:del frames[old]
 cap.release()
 enc.stdin.close()
 if enc.wait()!=0:raise RuntimeError('video encoding failed')
 print(json.dumps({'output':str(output),'frames':count,'fps':fps,'elapsed_seconds':time.time()-t0}),flush=True)
