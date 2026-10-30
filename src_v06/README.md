# 运动保护去闪烁实验

该流程已作为 NightFrame 1.1.0-experimental 的“运动保护”档接入。实验交付是四段原片片段，共 249 帧、8.3 秒、30 fps；没有导出全片，也没有在实验结果后添加 SeedVR2。

## 方法

- 复用已按原片与模型 SHA256 校验的 1080p / CFR 30 fps 源片代理及 Flickerformer 输出。Flickerformer 的亮度修正作为频闪候选先验。
- 使用官方 SEA-RAFT Spring-M 权重估计相邻帧双向运动和不确定性。前后向一致性、边界和不确定性用于拒绝不可靠邻帧；逐步合成短时间轨迹，内存保留有限窗口。
- 在对齐轨迹上估计局部亮度变化。普通亮度起伏使用标量曝光校正，保留当前时刻的画面。
- 对小型灯光、电子屏候选，在周边稳定结构上拟合局部仿射运动。可靠时用单个邻帧的完整区域恢复内容，并对区域亮度做校正；不能可靠拟合时保守退回。
- 保留每个输入时刻；没有全片 RGB 时间平均，没有 10 fps 抽帧或 RIFE 插帧。

这是一套工程试验，不是对 FlickerFix、Selective LED Flicker 或 SEA-RAFT 论文整体方法的复现，也没有联合训练两个模型。

## 已知结果与限制

四段分别是源片 0–1.4 秒、2.4–5.6 秒、8.5–10.5 秒、25–26.7 秒。对照左侧 SOURCE 是源片代理，中间 STRONG 是用户现用强力结果，右侧 MOTION 是本实验。下半部分为同一区域放大；公交和背景区域用源片独立 LK 特征跟踪用于对比。

道路轮廓与运动细节的保留明显优于整幅 9 帧平均路线。公交屏幕和部分背景灯光的闪烁降低，但稳定度仍弱于强力模式，局部区域仍可能误判、变形或出现接缝。尚未满足同时彻底去闪烁与无虚影的目标，本阶段以可选择的实验档公开保留取舍。

`outputs/v06/*_evaluation.json` 保存跟踪区域亮度的帧间变化代理指标。它会受真实亮度变化、遮挡及跟踪误差影响；不是去闪烁成功率、主观画质评分或干净真值误差。开头道路另外记录归一化梯度相关性，只能反映该局部与同时间源帧的边缘一致程度。

## 环境与复现

仍使用 F 盘的 `work/research_env`。新增 `research/SEA-RAFT` 和 `research/sea-raft-models`；来源、固定版本与权重哈希见后者的 `source_manifest.json`。没有修改外部 Python 环境。

输入必须已转为 SDR、恒定 30 fps、至多 1920×1080，且 Flickerformer 先验与其尺寸、帧数相同：

```powershell
& F:\VideoDeflicker\work\research_env\Scripts\python.exe F:\VideoDeflicker\src_v06\motion_deflicker.py source_cfr.mp4 flickerformer_prior.mp4 new_output.mp4
```

该研究命令会覆盖同名实验输出；禁止把输出设为源片或先验文件。原片 `F:\MP\VID_20260930_192007_8K.mp4` 未修改。

## 参考来源

- SEA-RAFT：<https://github.com/princeton-vl/SEA-RAFT>，BSD-3-Clause；代码 commit `9137517ba24e628442aec097d3afe71d03503b75`。
- Flickerformer：<https://github.com/qulishen/Flickerformer>。
- Selective LED Flicker Detection and Mitigation Algorithm for Non-HDR Video Sequences：<https://doi.org/10.1109/ICCE-Berlin.2018.8576189>。
- FlickerFix：<https://www.kibme.org/resources/journal/20251222144147743.pdf>。其交通灯场景与本试验的公交文字、建筑照明不同。
