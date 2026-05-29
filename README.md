# 内网文件分享工具

基于 Flask 的局域网文件分享工具，支持拖拽上传、生成分享链接、Office 文件预览、二维码访问。

## 功能

- 文件上传（拖拽/点击）
- 自动生成本机 IP + 端口分享链接
- 图片/视频/音频/PDF/Office/文本在线预览
- 批量下载打包 ZIP
- 二维码扫码访问
- 运行日志查看
- GUI 窗口模式（非命令行）

## 运行

```bash
# 开发模式
pip install -r requirements.txt
python app.py --gui

# 打包 EXE
py -m PyInstaller --onefile --noconsole --name "内网文件分享工具" --icon=app.ico --add-data "templates;templates" --add-data "static;static" --add-data "app.ico;." --hidden-import=qrcode --hidden-import=qrcode.image.pil --hidden-import=PIL --hidden-import=PIL.Image --hidden-import=tempfile --hidden-import=xlrd --hidden-import=docx --hidden-import=pptx --hidden-import=lxml --exclude-module numpy --exclude-module setuptools --exclude-module wheel --exclude-module pip app.py
```

## 依赖

Flask, Werkzeug, Pillow, qrcode, openpyxl, xlrd, python-docx, python-pptx, lxml
