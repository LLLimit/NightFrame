# 第三方来源

NightFrame 的 MIT 许可只覆盖本仓库原创 App、界面和工程适配代码。它不重新授权第三方模型、代码、数据、商标、运行库或演示画面中的内容。

| 组件 | 来源及处理方式 |
| --- | --- |
| Flickerformer | https://github.com/qulishen/Flickerformer ，安装脚本从固定提交获取上游代码与权重。上游仓库未提供明确的独立 LICENSE 文件，本仓库不将其声明为 MIT，也不在 Release 中捆绑其代码或权重；使用者应确认上游许可。 |
| SEA-RAFT | https://github.com/princeton-vl/SEA-RAFT ，BSD-3-Clause；安装时保留上游许可。 |
| RIFE ncnn Vulkan | https://github.com/nihui/rife-ncnn-vulkan ，从官方 Windows 发行 ZIP 获取，安装时保留 LICENSE。 |
| SeedVR2 / NumZ wrapper | https://github.com/ByteDance-Seed/SeedVR 与 https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler ，Apache-2.0；模型按对应模型页面条款使用。 |
| Python / pywebview / pythonnet / WebView2 及依赖 | Windows UI 运行库保留各自许可；Release 附带可收集的运行库许可文本。Microsoft WebView2 使用本机安装。 |
| FFmpeg、PyTorch 及 GPU 运行库 | 不随 UI ZIP 分发；按各自官方渠道与许可安装。 |

项目没有使用或分发 RE:Vision Effects DE:Flicker 的代码。README 中的名称只是作者个人使用体验的比较。

界面风格参考：https://luckymaomi.github.io/ui/styles/neumorphism/ 。
