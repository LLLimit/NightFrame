from pathlib import Path
import subprocess,json,argparse,uuid,cv2,os
ROOT=Path(os.environ.get('NIGHTFRAME_HOME',str(Path(__file__).resolve().parents[2]))).resolve()
p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');p.add_argument('--frames',type=int);args=p.parse_args()
src=Path(args.input);dst=Path(args.output)
if dst.exists():raise FileExistsError(str(dst))
cap=cv2.VideoCapture(str(src));keys=round(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS);cap.release()
if abs(fps-10)>.01:raise ValueError('Expected 10 fps keyframes')
frames=args.frames or keys*3
work=ROOT/'work/v03'/('rife_'+uuid.uuid4().hex[:8]);a=work/'input';b=work/'output';a.mkdir(parents=True);b.mkdir()
subprocess.run(['ffmpeg','-y','-hide_banner','-v','error','-i',str(src),'-compression_level','1',str(a/'%08d.png')],check=True)
# This release uses input_count/output_count as its sampling scale (main.cpp).
# K*3 preserves the exact 1/3-frame grid; the final held frame is trimmed below.
with (work/'rife.log').open('w',encoding='utf-8') as log:
 subprocess.run([str(ROOT/'research/RIFE/rife-ncnn-vulkan.exe'),'-i',str(a),'-o',str(b),'-m',str(ROOT/'research/RIFE/rife-v4.6'),'-g','0','-j','1:1:2','-n',str(keys*3),'-f','%08d.png','-z'],stderr=log,check=True)
subprocess.run(['ffmpeg','-y','-hide_banner','-v','error','-framerate','30','-i',str(b/'%08d.png'),'-vf','tpad=stop_mode=clone:stop_duration=0.2','-frames:v',str(frames),'-an','-c:v','h264_nvenc','-preset','p3','-cq','14','-b:v','0','-pix_fmt','yuv420p','-movflags','+faststart',str(dst)],check=True)
print(json.dumps({'output':str(dst),'keys':keys,'rife_intermediate_frames':keys*3,'frames':frames,'fps':30,'timestamp_spacing':'1/30 seconds','work':str(work)}),flush=True)
