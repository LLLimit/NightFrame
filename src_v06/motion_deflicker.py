"""Experimental motion-protected deflicker, not a reproduction of a paper.

Every input time is retained. SEA-RAFT gives bidirectional motion/uncertainty;
Flickerformer supplies an illumination prior. RGB replacement is restricted to
tracked flicker dropouts, never averaged over the complete moving scene.
"""
from pathlib import Path
import argparse, json, os, sys, time, subprocess, math
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parent.parent))).resolve()
for key, value in {'TEMP':'work/tmp','TMP':'work/tmp','HF_HOME':'work/hf-cache',
                   'TORCH_HOME':'work/torch-cache','CUDA_CACHE_PATH':'work/desktop/cuda-cache'}.items():
    os.environ[key] = str(ROOT/value)
import cv2
import numpy as np
import torch
from safetensors.torch import load_file

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

def luminance(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)/255.

def blur(image, sigma):
    return cv2.GaussianBlur(image, (0,0), sigma)

def smoothstep(low, high, x):
    t = np.clip((x-low)/(high-low), 0, 1)
    return t*t*(3-2*t)

class SeaFlow:
    def __init__(self, width=960):
        sys.path.insert(0, str(ROOT/'research/SEA-RAFT/core'))
        from raft import RAFT
        from extractor import ResNetFPN
        args = SimpleNamespace(**json.loads((ROOT/'research/SEA-RAFT/config/eval/spring-M.json').read_text()))
        # All model tensors, including the feature encoders, are loaded strictly
        # from the complete published checkpoint. Skip a redundant ImageNet download.
        with patch.object(ResNetFPN, '_init_weights', lambda self,args:None):
            self.model = RAFT(args)
        state=load_file(str(ROOT/'research/sea-raft-models/spring-M.safetensors'))
        # Hub safetensors deduplicates the same BN module registered both as
        # bn3 and downsample.1. Restore only those exact shared aliases.
        for name in self.model.state_dict():
            if name not in state and '.downsample.1.' in name:
                alias=name.replace('.downsample.1.','.bn3.')
                if alias in state:state[name]=state[alias]
        self.model.load_state_dict(state,strict=True)
        self.model.cuda().eval()
        self.width=width
        torch.backends.cuda.matmul.allow_tf32=True
        torch.backends.cudnn.allow_tf32=True
        torch.set_float32_matmul_precision('high')
        torch.set_num_threads(4)
        cv2.setNumThreads(3)
    @torch.inference_mode()
    def __call__(self, first, second):
        a=torch.from_numpy(cv2.cvtColor(first,cv2.COLOR_BGR2RGB)).permute(2,0,1)[None].float().cuda()
        b=torch.from_numpy(cv2.cvtColor(second,cv2.COLOR_BGR2RGB)).permute(2,0,1)[None].float().cuda()
        out=self.model(a,b,iters=4,test_mode=True)
        flow=out['final'][0].permute(1,2,0).cpu().numpy()
        info=out['info'][-1]
        weights=info[:,:2].softmax(1)
        logb=torch.stack([info[:,2].clamp(0,10),info[:,3].clamp(0,0)],1)
        log_error=(weights*logb).sum(1)[0].cpu().numpy()
        return flow,np.exp(-.45*log_error).astype(np.float32)

class FrameBank:
    def __init__(self, source, prior, width):
        self.cap=cv2.VideoCapture(str(source));self.prior=cv2.VideoCapture(str(prior))
        if not self.cap.isOpened() or not self.prior.isOpened():raise RuntimeError('Cannot open source/prior')
        self.count=round(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if self.count!=round(self.prior.get(cv2.CAP_PROP_FRAME_COUNT)):raise ValueError('Prior frame count mismatch')
        self.W=round(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH));self.H=round(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self.W>1920 or self.H>1080 or abs(self.cap.get(cv2.CAP_PROP_FPS)-30)>.01:
            raise ValueError('Experimental engine requires an SDR CFR 30 fps proxy, at most 1920x1080')
        if (round(self.prior.get(cv2.CAP_PROP_FRAME_WIDTH)),round(self.prior.get(cv2.CAP_PROP_FRAME_HEIGHT)))!=(self.W,self.H):
            raise ValueError('Prior/source dimensions do not match')
        self.top=0;self.bottom=0
        # Black borders must be common to multiple times, not inferred from one dark frame.
        rows=np.ones(self.H,dtype=bool)
        for i in sorted(set([0,self.count//4,self.count//2,3*self.count//4,self.count-1])):
            self.cap.set(cv2.CAP_PROP_POS_FRAMES,i);ok,im=self.cap.read()
            if not ok:raise RuntimeError('Cannot read source')
            rows &= (im.max(2)<8).mean(1)>.995
        while self.top<self.H and rows[self.top]:self.top+=1
        while self.bottom<self.H-self.top and rows[-1-self.bottom]:self.bottom+=1
        if self.top<8:self.top=0
        if self.bottom<8:self.bottom=0
        if self.H-self.top-self.bottom<self.H*.45:self.top=self.bottom=0
        self.top=self.top//2*2;self.bottom=self.bottom//2*2
        self.cap.set(cv2.CAP_PROP_POS_FRAMES,0)
        self.active_h=self.H-self.top-self.bottom
        self.size=(min(width,self.W),round(self.active_h*min(width,self.W)/self.W/2)*2)
        self.frames={};self.next=0
    def ensure(self,index):
        while self.next<=min(index,self.count-1):
            ok,im=self.cap.read();ok2,pr=self.prior.read()
            if not ok or not ok2:raise RuntimeError('Unexpected end of source/prior')
            active=im[self.top:self.H-self.bottom]
            guide=cv2.resize(active,self.size,interpolation=cv2.INTER_AREA)
            pred=cv2.resize(pr[self.top:self.H-self.bottom],self.size,interpolation=cv2.INTER_AREA)
            self.frames[self.next]={'full':im,'active':active,'small':guide,'prior':pred,'lum':luminance(guide)}
            self.next+=1
    def release_before(self,index):
        for key in list(self.frames):
            if key<index:del self.frames[key]
    def close(self):self.cap.release();self.prior.release()

class MotionRestorer:
    def __init__(self,bank,flow,radius=4):
        self.bank=bank;self.flow=flow;self.radius=radius;self.pairs={}
        w,h=bank.size
        self.grid=np.stack(np.meshgrid(np.arange(w,dtype=np.float32),np.arange(h,dtype=np.float32)),axis=-1)
        self.fullgrid=np.stack(np.meshgrid(np.arange(bank.W,dtype=np.float32),np.arange(bank.active_h,dtype=np.float32)),axis=-1)
    def remap(self,image,coordinates):
        return cv2.remap(image,coordinates[:,:,0],coordinates[:,:,1],cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    def pair(self,i,j):
        key=min(i,j)
        if key not in self.pairs:
            a=self.bank.frames[key]['small'];b=self.bank.frames[key+1]['small']
            f,uf=self.flow(a,b);back,ub=self.flow(b,a)
            for first,second,unc,src,dst in [(f,back,uf,key,key+1),(back,f,ub,key+1,key)]:
                points=self.grid+first
                error=np.linalg.norm(first+self.remap(second,points),axis=2)
                magnitude=np.linalg.norm(first,axis=2)
                agreement=np.exp(-np.square(error/(.65+.025*magnitude)))
                h,w=agreement.shape
                bounds=(points[:,:,0]>1)&(points[:,:,0]<w-2)&(points[:,:,1]>1)&(points[:,:,1]<h-2)
                confidence=(agreement*unc*bounds).astype(np.float32)
                self.pairs[(src,dst)]=(first,confidence)
            self.pairs[key]=True
        return self.pairs[(i,j)]
    def aligned(self,n,j):
        disp=np.zeros_like(self.grid);confidence=np.ones(self.grid.shape[:2],np.float32)
        step=1 if j>n else -1
        for k in range(n,j,step):
            field,cf=self.pair(k,k+step)
            points=self.grid+disp
            confidence=np.minimum(confidence,self.remap(cf,points))
            disp+=self.remap(field,points)
        return self.remap(self.bank.frames[j]['small'],self.grid+disp),disp,confidence
    def restore_planar_lights(self,n,ids,flows,cf,mask,restored):
        """Use one complete reference and a locally fitted surface for LED dropouts.

        Fit motion on the ring outside the light. It remains observable even
        when the screen itself has no illuminated pixels. Never extrapolate
        a surface from an uncertain, occluded, or inconsistent ring.
        """
        bank=self.bank;h,w=mask.shape
        seed=np.uint8(mask>.13)*255
        seed=cv2.morphologyEx(seed,cv2.MORPH_CLOSE,np.ones((7,9),np.uint8))
        count,labels,boxes,_=cv2.connectedComponentsWithStats(seed)
        replaced=0
        for label in range(1,count):
            x,y,bw,bh,area=map(int,boxes[label])
            if not (4<=bw<=140 and 3<=bh<=85 and 10<=area<=6500):continue
            if mask[y:y+bh,x:x+bw].max()<.35:continue
            # The rectangle's feather lies outside the luminous strokes.
            x0=max(0,x-3);y0=max(0,y-3);x1=min(w,x+bw+3);y1=min(h,y+bh+3)
            rx0=max(0,x0-7);ry0=max(0,y0-7);rx1=min(w,x1+7);ry1=min(h,y1+7)
            ring=np.ones((ry1-ry0,rx1-rx0),bool)
            ring[y0-ry0:y1-ry0,x0-rx0:x1-rx0]=False
            ring &= mask[ry0:ry1,rx0:rx1]<.1
            grid=self.grid[ry0:ry1,rx0:rx1]
            patchgrid=self.grid[y0:y1,x0:x1]
            candidates=[]
            identity=np.array([[1,0,0],[0,1,0]],np.float32)
            for k,j in enumerate(ids):
                if j==n:A=identity;trust=1.
                else:
                    good=ring&(cf[k,ry0:ry1,rx0:rx1]>.45)
                    if good.sum()<35:continue
                    points=grid[good][::2].astype(np.float32)
                    dest=(grid+flows[k][ry0:ry1,rx0:rx1])[good][::2].astype(np.float32)
                    A,inliers=cv2.estimateAffine2D(points,dest,method=cv2.RANSAC,ransacReprojThreshold=.65,maxIters=600,confidence=.98)
                    if A is None or inliers.mean()<.75:continue
                    sv=np.linalg.svd(A[:,:2],compute_uv=False)
                    if sv.min()<.85 or sv.max()>1.16:continue
                    projected=points@A[:,:2].T+A[:,2]
                    residual=np.median(np.linalg.norm(projected-dest,axis=1))
                    if residual>.35:continue
                    trust=float(inliers.mean())
                coords=patchgrid@A[:,:2].T+A[:,2]
                patch=self.remap(bank.frames[j]['small'],coords.astype(np.float32))
                value=float(luminance(patch).mean())
                candidates.append((value,k,A,trust))
            if len(candidates)<3:continue
            current=next(v for v in candidates if ids[v[1]]==n)
            peak=max(v[0] for v in candidates);floor=min(v[0] for v in candidates)
            target=float(np.percentile([v[0] for v in candidates],80))
            if peak<.06 or peak-floor<.025:continue
            # Correct one scalar illumination value for the complete light.
            # Use the current geometry whenever it actually contains the signal.
            if current[0]>=.80*target:chosen=current
            else:
                qualified=[v for v in candidates if v[0]>=.95*target]
                chosen=min(qualified,key=lambda v:abs(ids[v[1]]-n))
            _,k,A,trust=chosen;j=ids[k]
            scale_x=bank.W/w;scale_y=bank.active_h/h
            left=round(x0*scale_x);right=round(x1*scale_x);top=round(y0*scale_y);bottom=round(y1*scale_y)
            gx,gy=np.meshgrid(np.arange(left,right,dtype=np.float32)/scale_x,np.arange(top,bottom,dtype=np.float32)/scale_y)
            gridfull=np.stack([gx,gy],-1)
            coords=gridfull@A[:,:2].T+A[:,2]
            coords[:,:,0]*=scale_x;coords[:,:,1]*=scale_y
            donor=self.remap(bank.frames[j]['active'],coords.astype(np.float32)).astype(np.float32)
            donor*=np.clip((target+.002)/(chosen[0]+.002),.6,1.7)
            sh,sw=donor.shape[:2];xx=np.minimum(np.arange(sw),np.arange(sw)[::-1]);yy=np.minimum(np.arange(sh),np.arange(sh)[::-1])
            alpha=np.clip(np.minimum(yy[:,None],xx[None,:])/max(2,scale_x*2),0,1)[:,:,None]
            current_out=restored[top:bottom,left:right]
            restored[top:bottom,left:right]=current_out*(1-alpha)+donor*alpha
            replaced+=1
        return replaced
    def process(self,n):
        bank=self.bank;cur=bank.frames[n];lo=max(0,n-self.radius);hi=min(bank.count-1,n+self.radius)
        ids=list(range(lo,hi+1));views=[];flows=[];confidence=[]
        for j in ids:
            if j==n:im=cur['small'];field=np.zeros_like(self.grid);conf=np.ones(self.grid.shape[:2],np.float32)
            else:im,field,conf=self.aligned(n,j)
            views.append(im);flows.append(field);confidence.append(conf)
        ims=np.stack(views);cf=np.stack(confidence);center=ids.index(n)
        lum=np.stack([blur(luminance(im),1.1) for im in ims])
        # Rejected samples cannot affect the illumination target or supply content.
        valid=cf>.25
        # Vectorized order statistics avoid per-pixel Python nan-percentile loops.
        ordered=np.sort(np.where(valid,lum,np.inf),axis=0)
        counts=valid.sum(0)
        lower=np.rint(.15*(counts-1)).astype(np.intp)
        upper=np.rint(.80*(counts-1)).astype(np.intp)
        low=np.take_along_axis(ordered,lower[None],axis=0)[0]
        target=np.take_along_axis(ordered,upper[None],axis=0)[0]
        span=target-low
        relative=span/(target+.025)
        # Repeated changes are stronger evidence than one entry/exit or a cut.
        diff=np.diff(lum,axis=0);dv=valid[:-1]&valid[1:]
        up=((diff>.012)&dv).sum(0);down=((diff<-.012)&dv).sum(0)
        recurring=(np.minimum(up,down)>0).astype(np.float32)
        recurring=blur(recurring,1.4)
        stable_support=(valid.sum(0)>=min(4,len(ids))).astype(np.float32)
        prior_luma=blur(luminance(cur['prior']),1.1)
        prior_delta=np.abs(prior_luma-lum[center])
        strength=smoothstep(.025,.10,span)*smoothstep(.13,.45,relative)
        # A temporal prior maintains the mask even when the lamp is dark now.
        light_support=smoothstep(.055,.20,target)
        model_evidence=smoothstep(.015,.07,prior_delta)
        evidence=np.maximum(np.clip(recurring*1.6,0,1),.7*model_evidence)
        mask=strength*stable_support*evidence*light_support
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
        mask=np.clip(blur(mask,1.2)*1.15,0,1)
        # Source geometry stays at time n. Smooth scalar exposure cannot create
        # a second geometric contour; brightness dropout is handled separately.
        base=blur(lum[center],2.2)
        desired=blur(target,2.2)
        gain=np.clip((desired+.012)/(base+.012),.45,5.)
        gain=1+(gain-1)*mask
        gain_full=cv2.resize(gain,(bank.W,bank.active_h),interpolation=cv2.INTER_LINEAR)
        source=cur['active'].astype(np.float32)
        exposure=np.clip(source*gain_full[:,:,None],0,255)
        # Choose one observed RGB sample per location, with a preference for a
        # nearby time, rather than averaging moving RGB contours across frames.
        score=np.where(valid,lum,-10.)
        distance=np.array([abs(j-n) for j in ids],np.float32)[:,None,None]
        score=score-.003*distance
        pick=score.argmax(0)
        best=np.take_along_axis(lum,pick[None],axis=0)[0]
        best_conf=np.take_along_axis(cf,pick[None],axis=0)[0]
        dropout=smoothstep(.025,.12,best-lum[center])*smoothstep(.12,.55,(best-lum[center])/(best+.02))
        replace=mask*dropout*smoothstep(.3,.7,best_conf)
        replace_full=cv2.resize(replace,(bank.W,bank.active_h),interpolation=cv2.INTER_LINEAR)
        pick_full=cv2.resize(pick.astype(np.uint8),(bank.W,bank.active_h),interpolation=cv2.INTER_NEAREST)
        restored=exposure.copy()
        for k,j in enumerate(ids):
            selected=(pick_full==k)&(replace_full>.01)
            if j==n or not selected.any():continue
            field=cv2.resize(flows[k],(bank.W,bank.active_h),interpolation=cv2.INTER_LINEAR)
            field[:,:,0]*=bank.W/bank.size[0];field[:,:,1]*=bank.active_h/bank.size[1]
            donor=self.remap(bank.frames[j]['active'],self.fullgrid+field).astype(np.float32)
            alpha=replace_full[selected,None]
            restored[selected]=exposure[selected]*(1-alpha)+donor[selected]*alpha
        planar=self.restore_planar_lights(n,ids,flows,cf,mask,restored)
        full=cur['full'].copy();full[bank.top:bank.H-bank.bottom]=np.clip(restored,0,255).astype(np.uint8)
        exp=cur['full'].copy();exp[bank.top:bank.H-bank.bottom]=exposure.astype(np.uint8)
        # Releasing old motion fields bounds memory for a complete video.
        for key in list(self.pairs):
            if isinstance(key,int) and key<n-self.radius-1:del self.pairs[key]
            elif isinstance(key,tuple) and min(key)<n-self.radius-1:del self.pairs[key]
        stats={'mask_mean':float(mask.mean()),'mask_fraction':float((mask>.25).mean()),
               'replacement_fraction':float((replace>.25).mean()),'valid_neighbors':float(valid.sum(0).mean()),
               'prior_change':float(prior_delta.mean()),'flow_confidence':float(cf.mean()),'planar_patches':planar}
        return full,exp,cv2.resize(mask,(bank.W,bank.active_h)),stats

def encoder(path,W,H):
    path.parent.mkdir(parents=True,exist_ok=True)
    return subprocess.Popen(['ffmpeg','-hide_banner','-v','error','-y','-f','rawvideo','-pix_fmt','bgr24',
        '-s',f'{W}x{H}','-r','30','-i','pipe:0','-an','-c:v','h264_nvenc','-preset','p4','-cq','16',
        '-b:v','0','-vf','scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p',
        '-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-color_range','tv','-movflags','+faststart',str(path)],stdin=subprocess.PIPE,creationflags=FLAGS)

def run(source,prior,output,radius=4,flow_width=960,diagnostics=False):
    if Path(output).resolve() in {Path(source).resolve(),Path(prior).resolve()}:raise ValueError('Output cannot replace source/prior')
    start=time.time();bank=FrameBank(source,prior,flow_width)
    model=SeaFlow(flow_width);restorer=MotionRestorer(bank,model,radius)
    outputs={'motion':Path(output)}
    if diagnostics:outputs['exposure']=Path(output).with_stem(Path(output).stem+'_exposure')
    enc={key:encoder(path,bank.W,bank.H) for key,path in outputs.items()};stats=[]
    try:
        for n in range(bank.count):
            bank.ensure(n+radius)
            result,exposure,mask,stat=restorer.process(n)
            enc['motion'].stdin.write(result.tobytes())
            if diagnostics:enc['exposure'].stdin.write(exposure.tobytes())
            stats.append(stat)
            if diagnostics and n in {0,bank.count//3,bank.count//2,bank.count-1}:
                cv2.imwrite(str(Path(output).with_name(Path(output).stem+f'_mask_{n:04d}.png')),np.uint8(mask*255))
            if n%10==0 or n==bank.count-1:print(json.dumps({'frame':n+1,'total':bank.count,'elapsed':round(time.time()-start,1),**stat}),flush=True)
            bank.release_before(n-radius)
        for writer in enc.values():
            writer.stdin.close()
            if writer.wait()!=0:raise RuntimeError('Encoding failed')
    finally:
        bank.close()
        for writer in enc.values():
            if writer.poll() is None:writer.kill()
    report={'version':'v0.6-experimental','method':'SEA-RAFT motion/confidence + Flickerformer illumination prior + local dropout restoration',
        'source':str(source),'prior':str(prior),'frames':bank.count,'fps':30,'radius':radius,'flow_width':flow_width,
        'interpolation':False,'global_rgb_averaging':False,'outputs':{k:str(p) for k,p in outputs.items()},
        'stats':stats,'elapsed':time.time()-start,'black_borders':[bank.top,bank.bottom]}
    Path(output).with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('prior');p.add_argument('output');p.add_argument('--radius',type=int,default=4);p.add_argument('--flow-width',type=int,default=960);p.add_argument('--diagnostics',action='store_true')
    a=p.parse_args();run(a.input,a.prior,a.output,a.radius,a.flow_width,a.diagnostics)
