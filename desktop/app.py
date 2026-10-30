"""NightFrame Windows desktop shell. Model execution is isolated in worker.py."""
from pathlib import Path
import os,sys,json,subprocess,threading,time,uuid,base64,copy,logging,argparse
from paths import ROOT,gpu_python
WORK=ROOT/'work/desktop';PROFILES=ROOT/'profiles';OUTPUTS=ROOT/'outputs/NightFrame';FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
for d in [WORK,PROFILES,OUTPUTS,ROOT/'work/tmp']:d.mkdir(parents=True,exist_ok=True)
os.environ.update(TEMP=str(ROOT/'work/tmp'),TMP=str(ROOT/'work/tmp'),WEBVIEW2_USER_DATA_FOLDER=str(WORK/'webview-profile'))
logging.basicConfig(filename=WORK/'app.log',level=logging.INFO,encoding='utf-8',format='%(asctime)s %(levelname)s %(message)s')

def read_json(path,default):
 try:return json.loads(path.read_text(encoding='utf-8'))
 except (OSError,ValueError):return default
def write_json(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path)
def probe(path):return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],creationflags=FLAGS))

class Controller:
 def __init__(self,testing=False,settings_path=None):
  self.testing=testing
  self.settings_path=Path(settings_path) if settings_path else PROFILES/'nightframe_settings.json'
  self.lock=threading.RLock();self.proc=None;self.canceled=False;self.window=None;self.source_revision=0;self.source=None;self.log_lines=[];self.started=0;self.maximized=False
  self.settings=read_json(self.settings_path,{});self.history=read_json(PROFILES/'nightframe_history.json',[])
  self.settings.setdefault('output_dir',str(OUTPUTS))
  self.state={'status':'idle','progress':0,'phase':'ready','message':'准备就绪','output':None,'job_id':None}
  self.system=self.check_system()
  candidate=self.settings.get('input')
  if candidate and Path(candidate).is_file():threading.Thread(target=lambda:self._initial_source(candidate),daemon=True).start()
 def _initial_source(self,path):
  try:self.set_source(path)
  except Exception:logging.exception('Initial source unavailable')
 def check_system(self):
  gpu='未检测到 NVIDIA GPU'
  try:gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],creationflags=FLAGS,text=True,encoding='utf-8',timeout=8).splitlines()[0].strip()
  except Exception:pass
  models={'3b':(ROOT/'research/seedvr2-models/seedvr2_ema_3b_fp8_e4m3fn.safetensors').is_file(),'sharp7b':(ROOT/'research/seedvr2-models/seedvr2_ema_7b_sharp-Q4_K_M.gguf').is_file(),'vae':(ROOT/'research/seedvr2-models/ema_vae_fp16.safetensors').is_file(),'deflicker':(ROOT/'research/Flickerformer/Flickerformer.pth').is_file() and (ROOT/'research/RIFE/rife-ncnn-vulkan.exe').is_file()}
  models['motion']=models['deflicker'] and (ROOT/'research/SEA-RAFT/core/raft.py').is_file() and (ROOT/'research/sea-raft-models/spring-M.safetensors').is_file()
  return {'gpu':gpu,'models':models,'runtime':gpu_python().is_file(),'home':str(ROOT),'version':'1.1.0-experimental'}
 def _save_settings(self):
  if not self.testing:write_json(self.settings_path,self.settings)
 def snapshot(self,source_revision=0):
  with self.lock:
   data=copy.deepcopy(self.state);data.update(system=self.system,source_revision=self.source_revision,settings=self.settings,history=self.history[-12:][::-1],logs=self.log_lines[-120:],elapsed=round(time.time()-self.started,1) if self.started and self.state['status']=='running' else self.state.get('elapsed',0))
   if int(source_revision)!=self.source_revision:data['source']=copy.deepcopy(self.source)
   return data
 def set_source(self,path):
  with self.lock:
   if self.state['status']=='running':raise ValueError('请先停止当前任务，再更换视频。')
  p=Path(path).expanduser().resolve()
  if not p.is_file() or p.suffix.lower() not in ('.mp4','.mov','.mkv','.avi','.webm','.m4v'):raise ValueError('请选择有效的视频文件。')
  data=probe(p);v=next((s for s in data['streams'] if s['codec_type']=='video'),None)
  if not v:raise ValueError('文件中没有可读取的视频轨道。')
  duration=float(data.get('format',{}).get('duration',v.get('duration',0)));seek=min(5,max(0,duration*.15));poster=None
  try:
   im=subprocess.check_output(['ffmpeg','-hide_banner','-v','error','-ss',str(seek),'-i',str(p),'-frames:v','1','-vf','scale=1280:-2','-f','image2pipe','-c:v','mjpeg','pipe:1'],creationflags=FLAGS,timeout=30)
   poster='data:image/jpeg;base64,'+base64.b64encode(im).decode('ascii')
  except Exception:logging.exception('Poster generation failed')
  fps=v.get('avg_frame_rate','0/1');num,den=fps.split('/');rate=float(num)/max(1,float(den))
  source={'path':str(p),'name':p.name,'width':int(v['width']),'height':int(v['height']),'duration':duration,'fps':round(rate,3),'size':p.stat().st_size,'has_audio':any(s['codec_type']=='audio' for s in data['streams']),'poster':poster}
  with self.lock:
   self.source=source;self.source_revision+=1;self.settings['input']=str(p)
   self._save_settings()
  return self.snapshot(0)
 def choose_video(self):
  import webview
  result=self.window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('视频文件 (*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v)',))
  return self.set_source(result[0]) if result else self.snapshot(self.source_revision)
 def set_output_dir(self,path):
  value=str(path or '').strip().strip('"')
  if not value:raise ValueError('请选择或输入默认导出文件夹。')
  folder=Path(value).expanduser()
  if not folder.is_absolute():raise ValueError('请输入完整的文件夹路径，例如 F:\\导出视频。')
  folder=folder.resolve()
  try:
   folder.mkdir(parents=True,exist_ok=True)
   check=folder/('.nightframe-write-'+uuid.uuid4().hex+'.tmp')
   with check.open('xb'):pass
   check.unlink()
  except OSError as e:raise ValueError('无法写入此文件夹，请选择有写入权限的导出位置。') from e
  with self.lock:
   previous=self.settings['output_dir'];self.settings['output_dir']=str(folder)
   try:self._save_settings()
   except OSError:
    self.settings['output_dir']=previous
    raise
  return self.snapshot(self.source_revision)
 def choose_output(self,pick_only=False,directory=None):
  import webview
  folder=Path(directory or self.settings.get('output_dir',str(OUTPUTS)))
  if not folder.is_dir():folder=OUTPUTS
  result=self.window.create_file_dialog(webview.FileDialog.FOLDER,directory=str(folder))
  if pick_only:return {'path':str(Path(result[0]).resolve()) if result else None}
  if result:return self.set_output_dir(result[0])
  return self.snapshot(self.source_revision)
 def start_job(self,options):
  with self.lock:
   if self.state['status']=='running':raise ValueError('已有任务正在处理。')
   if not self.source:raise ValueError('请先选择视频。')
   if not self.system['runtime']:raise ValueError('找不到本机 GPU 运行环境。')
   mode=options.get('mode','deflicker');model=options.get('model','sharp7b');width=int(options.get('width',1920));preview=bool(options.get('preview',False));start=float(options.get('preview_start',0))
   if mode not in ('quality','deflicker') or model not in ('3b','sharp7b') or width not in (1920,3840):raise ValueError('处理参数无效。')
   if start<0 or start>=self.source['duration']:raise ValueError('试处理起点需要在视频时长范围内。')
   if mode=='quality' and not all(self.system['models'].get(k) for k in (model,'vae')):raise ValueError('本地修复模型不完整。')
   if mode=='deflicker' and not self.system['models']['deflicker']:raise ValueError('去闪烁模型不完整。')
   strength=options.get('deflicker_mode','strong')
   if strength not in ('strong','motion'):raise ValueError('请选择强力或运动保护模式。')
   if mode=='deflicker' and strength=='motion' and not self.system['models']['motion']:raise ValueError('运动保护模型未安装，请运行设置环境脚本。')
   job_id=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6];job=WORK/'jobs'/job_id;job.mkdir(parents=True)
   output_dir=Path(self.settings.get('output_dir',str(OUTPUTS))).resolve();suffix=('强力去闪烁' if strength=='strong' else '运动保护去闪烁') if mode=='deflicker' else ('SeedVR2' if model=='3b' else '7B_Sharp');name=Path(self.source['name']).stem+'_'+suffix+('_试片' if preview else '')+'_'+job_id+'.mp4';output=output_dir/name
   request={'input':self.source['path'],'output':str(output),'job_dir':str(job),'mode':mode,'model':model,'width':width,'preview':preview,'preview_start':start,'audio':bool(options.get('audio',True)),'deflicker_mode':options.get('deflicker_mode','strong')}
   write_json(job/'request.json',request);self.canceled=False;self.started=time.time();self.log_lines=[];self.state={'status':'running','progress':0,'phase':'prepare','message':'正在启动任务','job_id':job_id,'output':None,'mode':mode,'model':model,'preview':preview,'deflicker_mode':strength};self.settings.update(last_mode=mode,last_model=model,last_width=width,last_deflicker_mode=strength)
   self._save_settings()
   threading.Thread(target=self._work,args=(request,job),daemon=True).start()
  return self.snapshot(self.source_revision)
 def _work(self,req,job):
  try:
   env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')
   p=subprocess.Popen([str(gpu_python()),str(ROOT/'desktop/worker.py'),str(job/'request.json')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env,creationflags=FLAGS)
   with self.lock:self.proc=p
   if self.canceled:self._kill(p)
   completed=False
   with (job/'process.log').open('w',encoding='utf-8') as log:
    for line in p.stdout:
     log.write(line);log.flush();line=line.strip()
     with self.lock:
      if line.startswith('@NIGHTFRAME '):
       try:event=json.loads(line[12:])
       except ValueError:continue
       kind=event.get('kind')
       if kind=='log':self.log_lines.append(event.get('text',''));self.log_lines=self.log_lines[-400:]
       elif kind=='progress':self.state.update(progress=max(self.state['progress'],float(event['progress'])),phase=event.get('phase','model'),message=event.get('message','处理中'))
       elif kind=='done':
        completed=True;self.state.update(status='done',progress=100,phase='done',message='处理完成',output=event['output'],elapsed=round(time.time()-self.started,1));entry={'id':self.state['job_id'],'input':req['input'],'output':event['output'],'mode':req['mode'],'model':req['model'],'preview':req['preview'],'time':time.strftime('%m-%d %H:%M'),'elapsed':self.state['elapsed']};self.history.append(entry);self.history=self.history[-30:]
        entry['deflicker_mode']=req.get('deflicker_mode','strong')
        if not self.testing:write_json(PROFILES/'nightframe_history.json',self.history)
       elif kind=='error':self.state.update(status='error',phase='error',message=event.get('message','处理失败'))
      elif line:self.log_lines.append(line);self.log_lines=self.log_lines[-400:]
   code=p.wait()
   with self.lock:
    if self.canceled:self.state.update(status='canceled',phase='ready',message='处理已停止',elapsed=round(time.time()-self.started,1))
    elif not completed and self.state['status']!='error':self.state.update(status='error',phase='error',message=f'处理进程提前结束（{code}），请查看日志。')
  except Exception as e:
   logging.exception('Worker failed')
   with self.lock:self.state.update(status='error',phase='error',message=str(e))
  finally:
   with self.lock:self.proc=None
 @staticmethod
 def _kill(p):
  if p and p.poll() is None:subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=FLAGS)
 def cancel_job(self):
  with self.lock:
   if self.state['status']!='running':return self.snapshot(self.source_revision)
   self.canceled=True;self.state['message']='正在停止处理';p=self.proc
  self._kill(p);return self.snapshot(self.source_revision)
 def open_output(self):
  path=Path(self.settings.get('output_dir',str(OUTPUTS)));path.mkdir(parents=True,exist_ok=True);os.startfile(str(path));return True
 def play_source(self):
  if self.source:os.startfile(self.source['path'])
  return True
 def open_result(self,job_id=None):
  path=self.state.get('output') if job_id is None else next((x['output'] for x in self.history if x['id']==job_id),None)
  if not path or not Path(path).is_file():raise ValueError('结果文件不存在。')
  os.startfile(path);return True
 def use_result(self):
  if not self.state.get('output'):raise ValueError('还没有完成的结果。')
  return self.set_source(self.state['output'])
 def window_action(self,action):
  if action=='minimize':self.window.minimize()
  elif action=='maximize':
   self.window.restore() if self.maximized else self.window.maximize();self.maximized=not self.maximized
  elif action=='close':self.window.destroy()
  return True
 def shutdown(self):self.cancel_job()

class Bridge:
 def __init__(self,controller):self._controller=controller
 def call(self,action,data=None):
  data=data or {}
  try:
   c=self._controller
   if action=='state':result=c.snapshot(data.get('source_revision',0))
   elif action=='source':result=c.set_source(data['path'])
   elif action=='choose_video':result=c.choose_video()
   elif action=='choose_output':result=c.choose_output(bool(data.get('pick_only')),data.get('directory'))
   elif action=='save_settings':result=c.set_output_dir(data.get('output_dir'))
   elif action=='start':result=c.start_job(data)
   elif action=='cancel':result=c.cancel_job()
   elif action=='open_output':result=c.open_output()
   elif action=='play_source':result=c.play_source()
   elif action=='open_result':result=c.open_result(data.get('id'))
   elif action=='use_result':result=c.use_result()
   elif action=='window':result=c.window_action(data['action'])
   else:raise ValueError('未知操作')
   return {'ok':True,'data':result}
  except Exception as e:return {'ok':False,'error':str(e)}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--self-test');parser.add_argument('--test-job');parser.add_argument('--ui-only',action='store_true');a=parser.parse_args();import webview
 profile=WORK/('webview-test-profile' if a.self_test or a.test_job else 'webview-profile');os.environ['WEBVIEW2_USER_DATA_FOLDER']=str(profile)
 c=Controller(testing=bool(a.self_test or a.test_job));bridge=Bridge(c);asset=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'web/index.html'
 webview.settings['OPEN_DEVTOOLS_IN_DEBUG']=False
 win=webview.create_window('NightFrame灯光闪烁修复 · 实验存档',str(asset),js_api=bridge,width=1370,height=890,min_size=(1080,740),frameless=True,easy_drag=False,shadow=True,background_color='#e0e5ec',hidden=bool(a.self_test or a.test_job));c.window=win;win.events.closed+=c.shutdown
 loaded=threading.Event();win.events.loaded+=loaded.set
 def test():
  if not loaded.wait(30):raise RuntimeError('UI load timeout')
  time.sleep(1.5)
  if a.self_test:
   try:
    deadline=time.time()+35
    while not a.ui_only and time.time()<deadline and not win.evaluate_js("document.querySelector('#inputPath').value"):time.sleep(.25)
    report=win.evaluate_js("JSON.stringify({title:document.title,bridge:!!window.pywebview?.api,nav:document.querySelectorAll('[data-mode]').length,models:document.querySelectorAll('[data-model]').length,width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,progressBottom:document.querySelector('.progress-card').getBoundingClientRect().bottom,engine:document.querySelector('#engineLabel').textContent,source:document.querySelector('#inputPath').value,errors:window.appErrors||[]})")
    result=json.loads(report);assert result['bridge'] and result['nav']==2 and result['models']==2 and result['scrollWidth']<=result['width'];assert not result['errors'];assert result['progressBottom']<=result['height']
    if not a.ui_only:assert result['engine']=='本地引擎已就绪' and result['source']
    result.update(project_root=str(ROOT),gpu_runtime_required=not a.ui_only)
    win.evaluate_js("document.querySelector('[data-mode=deflicker]').click()")
    assert win.evaluate_js("!document.querySelector('#deflickerSettings').hidden && document.querySelector('#qualitySettings').hidden")
    assert win.evaluate_js("document.querySelectorAll('[data-strength]').length===2 && !!document.querySelector('[data-strength=strong]') && !!document.querySelector('[data-strength=motion]')")
    win.evaluate_js("document.querySelector('[data-strength=motion]').click()")
    assert win.evaluate_js("window.NightFrame.getState().strength==='motion'")
    win.evaluate_js("document.querySelector('[data-mode=quality]').click();document.querySelector('[data-model=\"3b\"]').click()")
    assert win.evaluate_js("window.NightFrame.getState().model==='3b'")
    win.evaluate_js("document.querySelector('[data-model=sharp7b]').click();document.querySelector('#historyButton').click()")
    assert win.evaluate_js("!document.querySelector('#historyOverlay').hidden")
    win.evaluate_js("document.querySelector('#closeHistory').click();document.querySelector('#settingsButton').click()")
    original=c.settings['output_dir']
    assert win.evaluate_js("!document.querySelector('#settingsOverlay').hidden && document.activeElement.id==='defaultOutputPath'")
    assert win.evaluate_js("document.querySelector('#defaultOutputPath').value")==original
    win.evaluate_js("document.querySelector('#defaultOutputPath').value='cancelled edit';document.querySelector('#cancelSettings').click()")
    assert c.settings['output_dir']==original
    target=str(WORK/'设置导出 测试')
    win.evaluate_js("document.querySelector('#settingsButton').click();document.querySelector('#defaultOutputPath').value="+json.dumps(target)+";document.querySelector('#saveSettings').click()")
    deadline=time.time()+10
    while time.time()<deadline and not win.evaluate_js("document.querySelector('#settingsOverlay').hidden"):time.sleep(.1)
    assert c.settings['output_dir']==target
    assert win.evaluate_js("document.querySelector('#outputPath').textContent")==target
    win.evaluate_js("document.querySelector('#settingsButton').click();document.querySelector('#defaultOutputPath').value="+json.dumps(original)+";document.querySelector('#saveSettings').click()")
    deadline=time.time()+10
    while time.time()<deadline and not win.evaluate_js("document.querySelector('#settingsOverlay').hidden"):time.sleep(.1)
    assert c.settings['output_dir']==original
    result.update(status='passed',navigation='passed',model_selection='passed',history='passed',deflicker_modes=['strong','motion'],settings_dialog='passed',settings_cancel='passed',settings_save_and_workbench_sync='passed');write_json(Path(a.self_test),result)
   except Exception as e:
    import traceback
    write_json(Path(a.self_test),{'status':'failed','error':str(e),'traceback':traceback.format_exc(),'ui':locals().get('result')})
   finally:win.destroy()
  elif a.test_job:
   try:
    req=read_json(Path(a.test_job),{});c.set_source(req['input']);result=c.start_job(req)
    while c.snapshot()['status']=='running':time.sleep(.5)
    write_json(WORK/'native_job_test.json',c.snapshot(c.source_revision))
   finally:win.destroy()
 try:webview.start(test if a.self_test or a.test_job else None,gui='edgechromium',private_mode=bool(a.self_test or a.test_job),storage_path=str(profile),debug=False)
 finally:c.shutdown()

if __name__=='__main__':
 try:main()
 except Exception as e:
  logging.exception('Desktop startup failed')
  if '--self-test' in sys.argv:
   try:write_json(Path(sys.argv[sys.argv.index('--self-test')+1]),{'status':'failed','error':str(e)})
   except Exception:pass
  else:
   import ctypes;ctypes.windll.user32.MessageBoxW(None,'启动失败：'+str(e)+'\n\n日志：'+str(WORK/'app.log'),'NightFrame',0x10)
  sys.exit(1)
