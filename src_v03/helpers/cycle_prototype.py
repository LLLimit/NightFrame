"""Experimental complete-cycle integration, independently from v0.2 masks.

This is a physics-inspired baseline, not an implementation of Flickerformer.
Three observed phases are motion-compensated before radiance integration.
"""
from pathlib import Path
import cv2,numpy as np,subprocess,json,time,argparse,os
ROOT=Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parents[2]))).resolve();cv2.setNumThreads(3)
p=argparse.ArgumentParser();p.add_argument('--start',type=float,default=4);p.add_argument('--end',type=float,default=11);p.add_argument('--tag',default='sample');p.add_argument('--input',default=str(ROOT/'work/v02/source_cfr.mp4'));p.add_argument('--radius',type=int,default=1);p.add_argument('--mode',choices=['all','mean','peak'],default='all');args=p.parse_args()
cap=cv2.VideoCapture(args.input)
first=round(args.start*30);last=round(args.end*30)
total=round(cap.get(cv2.CAP_PROP_FRAME_COUNT));last=min(last,total)
W,H=round(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT));fw=640;fh=round(H*fw/W)
cap.set(cv2.CAP_PROP_POS_FRAMES,max(0,first-args.radius));frames={};grays={};nextread=max(0,first-args.radius)
gx,gy=np.meshgrid(np.arange(W,dtype=np.float32),np.arange(H,dtype=np.float32))
def structure(im):
 g=cv2.cvtColor(cv2.resize(im,(fw,fh),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY).astype(np.float32)
 g=np.log1p(g);detail=g-cv2.GaussianBlur(g,(0,0),4)
 rms=np.sqrt(cv2.GaussianBlur(detail*detail,(0,0),4)+.02)
 return np.clip(127+32*detail/rms,0,255).astype(np.uint8)
def ensure(index):
 global nextread
 while nextread<=min(total-1,index):
  ok,im=cap.read()
  if not ok:raise RuntimeError('Video ended early')
  frames[nextread]=im;grays[nextread]=structure(im);nextread+=1
dis=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
def aligned(ref,j):
 if j==ref:return frames[j]
 flow=dis.calc(grays[ref],grays[j],None)
 flow=cv2.resize(flow,(W,H),interpolation=cv2.INTER_LINEAR);flow[:,:,0]*=W/fw;flow[:,:,1]*=H/fh
 return cv2.remap(frames[j],gx+flow[:,:,0],gy+flow[:,:,1],cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT101)
def encoder(name):
 path=ROOT/'outputs/v03'/f'{args.tag}_{name}_10fps.mp4'
 return path,subprocess.Popen(['ffmpeg','-y','-hide_banner','-v','error','-f','rawvideo','-pixel_format','bgr24','-video_size',f'{W}x{H}','-framerate','10','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p3','-cq','14','-b:v','0','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],stdin=subprocess.PIPE)
names=['cycle_mean','cycle_peak','phase0'] if args.mode=='all' else ['cycle_'+args.mode]
jobs={name:encoder(name) for name in names};gamma=((np.arange(256,dtype=np.float32)/255)**2.2)
t0=time.time();count=0
for n in range(first,last,3):
 window=min(total,2*args.radius+1);lo=max(0,min(n-args.radius,total-window));hi=lo+window-1
 ensure(hi);ids=list(range(lo,hi+1))
 ims=np.stack([aligned(n,j) for j in ids])
 linear=gamma[ims]
 mean=np.clip(linear.mean(0)**(1/2.2)*255,0,255).astype(np.uint8)
 # Peak RGB always comes from one observed color sample, not channelwise maxima.
 peak=None
 if args.mode!='mean':
  lum=.0722*ims[:,:,:,0]+.7152*ims[:,:,:,1]+.2126*ims[:,:,:,2]
  pick=lum.argmax(0);peak=np.take_along_axis(ims,pick[None,:,:,None],axis=0)[0]
 # Preserve the source coded black borders exactly.
 active=np.flatnonzero((frames[n].max(2)>24).mean(1)>.02)
 if len(active):
  for im in [v for v in [mean,peak] if v is not None]:im[:active[0]]=frames[n][:active[0]];im[active[-1]+1:]=frames[n][active[-1]+1:]
 images={'cycle_mean':mean,'cycle_peak':peak,'phase0':frames[n]}
 for name in jobs:jobs[name][1].stdin.write(memoryview(images[name]).cast('B'))
 count+=1
 if count%15==0:print(count,'keyframes',round(time.time()-t0,1),'seconds',flush=True)
 for old in [j for j in frames if j<n-args.radius]:del frames[old];del grays[old]
cap.release()
for path,enc in jobs.values():
 enc.stdin.close()
 if enc.wait()!=0:raise RuntimeError('encoding failed')
report={'method':'motion_compensated_temporal_radiance_integration','window_frames':2*args.radius+1,'keyframe_hop':3,'integration':args.mode,'start':args.start,'end':args.start+count/10,'keyframes':count,'keyframe_fps':10,'outputs':[str(v[0]) for v in jobs.values()],'elapsed_seconds':time.time()-t0,'limitations':['Three phases are an assumption to validate on the source, not a universal LED period.','Optical flow may smear motion or occlusions.','Mean integration can lower the brightness of emissive displays; peak reconstruction may amplify reflections.']}
(ROOT/'work/v03'/f'{args.tag}_cycle_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report),flush=True)
