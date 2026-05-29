# 内网文件分享工具

windows本地文件分享工具，支持拖拽上传、生成分享链接、文件预览、手机可扫二维码访问。

## 核心功能

- 自动生成本机 IP + 端口分享链接
- GUI 窗口模式
- 文件上传与分享 — 拖拽/点击上传，自动获取本机 IP 生成分享链接，支持设置大小上限（默认 10GB）和文件过期自动清理
- 在线预览 — 图片（JPG/PNG/GIF/WebP）、视频（MP4/WebM 拖进度条）、音频（MP3/WAV/FLAC）、PDF（内嵌 PDF.js）、Office（XLSX/DOCX/PPTX）、文本/代码（30+ 格式）
- 批量操作 — 多选打包下载 ZIP、单个/全部删除
- 二维码扫码 — 一键生成，手机即用
- 运行日志 — 记录上传/下载/删除日志
- 单实例保护 — 防止重复启动

## 运行

- 下载EXE文件运行即可

```bash
# 开发模式
python app.py --gui

# 自编译 .EXE
py -m PyInstaller --onefile --noconsole --name "内网文件分享工具" --icon=app.ico --add-data "templates;templates" --add-data "static;static" --add-data "app.ico;." --hidden-import=qrcode --hidden-import=qrcode.image.pil --hidden-import=PIL --hidden-import=PIL.Image --hidden-import=tempfile --hidden-import=xlrd --hidden-import=docx --hidden-import=pptx --hidden-import=lxml --exclude-module numpy --exclude-module setuptools --exclude-module wheel --exclude-module pip app.py
```

## 依赖

Flask, Werkzeug, Pillow, qrcode, openpyxl, xlrd, python-docx, python-pptx, lxml
