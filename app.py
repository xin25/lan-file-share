#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网文件分享工具 - Flask 主应用

功能：
- 文件上传（拖拽/点击选择）
- 生成分享链接（含局域网IP和端口）
- 文件下载页面
- 已分享文件列表

作者：Xin
"""

import os
import sys
import uuid
import json
import io
import zipfile
import socket
import mimetypes
import unicodedata
import re
import threading
import time
from datetime import datetime
from flask import (
    Flask, request, render_template, make_response,
    send_from_directory, jsonify, abort
)
from werkzeug.utils import secure_filename


# ============================================================
# 控制台颜色
# ============================================================

class Color:
    """ANSI颜色码，兼容Windows终端"""
    RESET   = '\033[0m'
    GREEN   = '\033[32m'
    BLUE    = '\033[34m'
    YELLOW  = '\033[33m'
    RED     = '\033[31m'
    CYAN    = '\033[36m'
    DIM     = '\033[2m'


def log_upload(filename: str, size: str, link: str):
    """上传日志 - 绿色"""
    print(f"  {Color.GREEN}[上传]{Color.RESET} {filename} ({size}) → {link}")
    _store_log("上传", f"{filename} ({size})")


def log_download(filename: str, ip: str):
    """下载日志 - 蓝色"""
    print(f"  {Color.BLUE}[下载]{Color.RESET} {filename} ← {ip}")
    _store_log("下载", f"{filename} ← {ip}")


def log_delete(filename: str):
    """删除日志 - 红色"""
    print(f"  {Color.RED}[删除]{Color.RESET} {filename}")
    _store_log("删除", filename)


def log_info(msg: str):
    """信息日志 - 青色"""
    print(f"  {Color.CYAN}[信息]{Color.RESET} {msg}")
    _store_log("信息", msg)


# ============================================================
# GUI 日志存储（供"查看日志"窗口读取，线程安全）
# ============================================================

_gui_logs: list = []
_gui_logs_lock = threading.Lock()

def _store_log(action: str, message: str):
    """向日志列表追加记录，最多保留 300 条"""
    now = datetime.now().strftime('%H:%M:%S')
    with _gui_logs_lock:
        _gui_logs.append((now, action, message))
        if len(_gui_logs) > 300:
            _gui_logs.pop(0)


# ============================================================
# 单实例检测（防止重复启动）- Windows Mutex 方案
# ============================================================

_mutex_handle = None

def check_single_instance():
    """使用 Windows Mutex 检测是否已有实例在运行，无需生成文件"""
    global _mutex_handle
    if sys.platform == 'win32':
        import ctypes
        mutex_name = 'Global\\FileShareLanTool_v1'
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
        ERROR_ALREADY_EXISTS = 183
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            print("程序已在运行中，当前实例退出。")
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            sys.exit(0)


# ============================================================
# 配置项
# ============================================================

class Config:
    """应用配置"""
    # 服务端口
    PORT: int = int(os.environ.get('FILE_SHARE_PORT', 5000))
    # 上传文件大小限制（字节），默认 10GB
    MAX_CONTENT_LENGTH: int = int(os.environ.get('FILE_SHARE_MAX_SIZE', 10 * 1024 * 1024 * 1024))
    # 上传文件存储目录
    UPLOAD_DIR: str = 'uploads'
    # 模板目录
    TEMPLATE_DIR: str = 'templates'
    # 数据文件（持久化文件列表）
    DATA_FILE: str = 'file_data.json'
    # 最大文件数量限制（0表示无限制）
    MAX_FILES: int = int(os.environ.get('FILE_SHARE_MAX_FILES', 0))
    # 文件过期天数（0表示永不过期）
    FILE_EXPIRY_DAYS: int = int(os.environ.get('FILE_SHARE_EXPIRY_DAYS', 0))


# ============================================================
# 工具函数
# ============================================================

def get_local_ip() -> str:
    """获取本机局域网 IP 地址"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'


def find_available_port(start_port: int) -> int:
    """从 start_port 开始查找可用端口，找不到则报错退出"""
    for port in range(start_port, start_port + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.bind(('0.0.0.0', port))
                return port
        except OSError:
            continue
    print(f"[错误] 端口 {start_port}-{start_port+99} 均被占用，无法启动服务")
    sys.exit(1)


def format_file_size(size_bytes: int) -> str:
    """将字节大小转换为人类可读格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# 预览支持的文件类型扩展名集合
_PREVIEW_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'ico', 'tiff'}
_PREVIEW_VIDEO_EXTS = {'mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'}
_PREVIEW_AUDIO_EXTS = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'wma', 'm4a'}
_PREVIEW_PDF_EXTS   = {'pdf'}
_PREVIEW_OFFICE_EXTS = {'xlsx', 'xls', 'docx', 'pptx', 'ppt'}  # 移除了 'doc'
_PREVIEW_TEXT_EXTS  = {
    'txt', 'json', 'xml', 'md', 'csv', 'log', 'html', 'htm', 'css',
    'js', 'ts', 'py', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs',
    'sh', 'bat', 'cmd', 'ps1', 'yaml', 'yml', 'toml', 'ini', 'cfg',
    'conf', 'sql', 'rb', 'php', 'swift', 'kt', 'lua', 'r', 'vue',
    'jsx', 'tsx', 'scss', 'less', 'env', 'gitignore', 'dockerfile',
    'makefile', 'cmake', 'gradle', 'properties'
}


def get_preview_type(filename: str) -> str:
    """根据文件扩展名返回预览类型：image/video/audio/pdf/text/other"""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext in _PREVIEW_IMAGE_EXTS:
        return 'image'
    if ext in _PREVIEW_VIDEO_EXTS:
        return 'video'
    if ext in _PREVIEW_AUDIO_EXTS:
        return 'audio'
    if ext in _PREVIEW_PDF_EXTS:
        return 'pdf'
    if ext in _PREVIEW_TEXT_EXTS:
        return 'text'
    if ext in _PREVIEW_OFFICE_EXTS:
        return 'office'
    return 'other'


def get_resource_path(relative_path: str) -> str:
    """获取资源文件路径（只读资源，如模板），兼容 PyInstaller 打包后的路径"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_app_dir() -> str:
    """获取应用所在目录（可写数据目录），EXE模式下为EXE所在目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def load_file_data() -> dict:
    """从 JSON 文件加载已分享文件数据"""
    data_path = os.path.join(get_app_dir(), Config.UPLOAD_DIR, Config.DATA_FILE)
    if os.path.exists(data_path):
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_file_data(data: dict) -> None:
    """将已分享文件数据保存到 JSON 文件（原子写入，防止中途崩溃导致数据丢失）"""
    data_path = os.path.join(get_app_dir(), Config.UPLOAD_DIR, Config.DATA_FILE)
    try:
        # 先写入临时文件，再原子替换，避免写入中途崩溃导致 JSON 损坏
        tmp_path = data_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, data_path)
    except (IOError, RuntimeError) as e:
        print(f"[警告] 保存文件数据失败: {e}")


def make_safe_filename(filename: str, file_id: str) -> str:
    """
    生成安全的保存文件名，同时保留原始文件名的可辨识度。
    对于中文文件名，secure_filename 会吞掉所有非 ASCII 字符，
    因此改为用 UUID 子目录隔离 + 保留原始文件名。
    """
    # 尝试 secure_filename，如果结果有效则使用
    safe = secure_filename(filename)
    if safe and safe != '.' and not safe.startswith('.'):
        # secure_filename 能处理，用原有逻辑
        return f"{file_id}_{safe}"
    else:
        # 中文/特殊字符文件名，secure_filename 吃掉了，
        # 改用子目录隔离方式保留原始名
        return None  # 返回 None 表示需要用子目录方式保存


def sanitize_save_name(filename: str, file_id: str) -> str:
    """
    对中文/特殊文件名做安全处理，防止路径穿越。
    1. 取 basename 去掉目录部分
    2. 循环去除 .. 防止穿越绕过（如 ....// → ../）
    3. 去除路径分隔符
    4. 空名字用 file_id 兜底
    """
    name = os.path.basename(filename)
    # 循环替换直到不再包含 ..
    while '..' in name:
        name = name.replace('..', '')
    # 去除路径分隔符（防止残留的 / 或 \）
    name = name.replace('/', '').replace('\\', '')
    if not name or name == '.':
        name = file_id
    return name


# ============================================================
# Flask 应用初始化
# ============================================================

# 确定模板和上传目录路径
template_path = get_resource_path(Config.TEMPLATE_DIR)
upload_path = os.path.join(get_app_dir(), Config.UPLOAD_DIR)

# 确保上传目录存在
os.makedirs(upload_path, exist_ok=True)

# 创建 Flask 应用
app = Flask(__name__, template_folder=template_path)
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH

# 文件数据存储（内存 + JSON 持久化）
# 结构: { file_id: { filename, size, size_str, upload_time, download_count, original_name, save_dir } }
file_store: dict = load_file_data()
file_store_lock = threading.Lock()  # 线程锁，保护并发访问

# 清理过期文件节流：每 5 分钟最多执行一次
_last_cleanup_time: float = 0
_CLEANUP_INTERVAL: float = 300  # 秒


def cleanup_old_files():
    """清理过期文件和超量文件（节流：每 5 分钟最多执行一次，需在file_store_lock锁内调用）"""
    global _last_cleanup_time
    now_ts = time.monotonic()
    if now_ts - _last_cleanup_time < _CLEANUP_INTERVAL:
        return  # 5 分钟内已执行过，跳过
    _last_cleanup_time = now_ts

    now = datetime.now()
    files_to_delete = []

    # 1. 清理过期文件
    if Config.FILE_EXPIRY_DAYS > 0:
        for file_id, info in list(file_store.items()):
            upload_time_str = info.get('upload_time', '')
            if not upload_time_str:
                continue
            try:
                upload_time = datetime.strptime(upload_time_str, '%Y-%m-%d %H:%M:%S')
                if (now - upload_time).days >= Config.FILE_EXPIRY_DAYS:
                    files_to_delete.append(file_id)
            except ValueError:
                continue

    # 2. 清理超量文件（按上传时间排序，删除最旧的）
    if Config.MAX_FILES > 0 and len(file_store) > Config.MAX_FILES:
        sorted_files = sorted(
            file_store.items(),
            key=lambda x: x[1].get('upload_time', ''),
            reverse=False
        )
        excess_count = len(file_store) - Config.MAX_FILES
        for i in range(excess_count):
            if i < len(sorted_files):
                files_to_delete.append(sorted_files[i][0])

    # 删除文件（记录和磁盘）
    for file_id in files_to_delete:
        if file_id not in file_store:
            continue
        file_info = file_store[file_id]
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')  # 子目录方式
        file_path = os.path.join(upload_path, save_dir, save_name) if save_dir else os.path.join(upload_path, save_name)

        # 从记录中移除
        del file_store[file_id]

        # 删除磁盘文件
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                print(f"[警告] 清理文件失败: {file_path} - {e}")

        # 如果是子目录方式，删除空的子目录
        if save_dir:
            sub_dir = os.path.join(upload_path, save_dir)
            if os.path.isdir(sub_dir):
                try:
                    os.rmdir(sub_dir)  # 只删除空目录
                except OSError:
                    pass

        log_delete(f"清理过期文件: {file_info.get('original_name', '未知文件')}")

    # 返回是否有文件被删除（由调用者在锁外持久化）
    return len(files_to_delete) > 0


def verify_file_consistency():
    """
    启动时校验文件一致性：清理 file_store 中磁盘文件已不存在的幽灵记录。
    需要在 file_store_lock 锁内调用。
    """
    ghost_ids = []
    for file_id, info in list(file_store.items()):
        save_name = info.get('save_name', '')
        save_dir = info.get('save_dir', '')
        file_path = os.path.join(upload_path, save_dir, save_name) if save_dir else os.path.join(upload_path, save_name)

        if not os.path.exists(file_path):
            ghost_ids.append(file_id)

    for file_id in ghost_ids:
        name = file_store[file_id].get('original_name', '未知文件')
        del file_store[file_id]
        log_info(f"清理幽灵记录: {name} (文件已不存在)")

    # 返回是否有幽灵记录（由调用者在锁外持久化）
    return len(ghost_ids) > 0


# 启动时校验文件一致性
with file_store_lock:
    need_save = verify_file_consistency()
if need_save:
    save_file_data(file_store)

# 本机 IP（启动时获取，同时提供实时获取方法）
local_ip: str = get_local_ip()


def get_current_ip() -> str:
    """获取当前局域网 IP（实时获取，处理网络切换场景）"""
    global local_ip
    current = get_local_ip()
    if current != local_ip:
        local_ip = current
        log_info(f"IP 地址已更新: {local_ip}")
    return local_ip

# 查找可用端口
Config.PORT = find_available_port(Config.PORT)


# ============================================================
# 路由定义
# ============================================================

@app.route('/')
def index():
    """首页 - 上传页面 + 文件列表"""
    # 先获取 IP（锁外执行，避免 socket 连接持锁阻塞）
    current_ip = get_current_ip()
    # 修复：读操作也需要加锁，防止并发时字典变更
    with file_store_lock:
        files_info = []
        for file_id, info in file_store.items():
            files_info.append({
                'file_id': file_id,
                'original_name': info.get('original_name', '未知文件'),
                'size_str': info.get('size_str', '未知'),
                'upload_time': info.get('upload_time', ''),
                'download_count': info.get('download_count', 0),
                'share_link': f"http://{current_ip}:{Config.PORT}/download/{file_id}",
                'preview_type': get_preview_type(info.get('original_name', ''))
            })
    # 按上传时间倒序排列
    files_info.sort(key=lambda x: x['upload_time'], reverse=True)

    return render_template(
        'index.html',
        files=files_info,
        local_ip=current_ip,
        port=Config.PORT
    )


@app.route('/upload', methods=['POST'])
def upload_file():
    """处理文件上传"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '未选择文件'}), 400

    original_name = file.filename
    # 文件夹上传时浏览器发送的filename带路径（如"文件夹/test.txt"），提取纯文件名
    original_name = os.path.basename(original_name).replace('\\', '/')
    if '/' in original_name:
        original_name = original_name.rsplit('/', 1)[-1]
    if not original_name:
        original_name = 'unnamed_file'

    # 生成唯一文件 ID（使用完整UUID避免碰撞）
    file_id = str(uuid.uuid4())

    # 生成安全的保存文件名
    save_name = make_safe_filename(original_name, file_id)
    save_dir = ''

    if save_name is None:
        # 中文/特殊字符文件名，用子目录隔离 + 保留原始文件名（过滤路径穿越）
        save_dir = file_id
        sub_dir_path = os.path.join(upload_path, save_dir)
        os.makedirs(sub_dir_path, exist_ok=True)
        save_name = sanitize_save_name(original_name, file_id)
        save_path = os.path.join(sub_dir_path, save_name)
    else:
        save_path = os.path.join(upload_path, save_name)

    try:
        file.save(save_path)
    except Exception as e:
        # 保存失败时清理已创建的子目录
        if save_dir and os.path.isdir(os.path.join(upload_path, save_dir)):
            try:
                os.rmdir(os.path.join(upload_path, save_dir))
            except OSError:
                pass
        return jsonify({'success': False, 'message': f'文件保存失败: {str(e)}'}), 500

    # 获取文件大小
    file_size = os.path.getsize(save_path)

    # 存储文件信息（加锁确保线程安全）
    upload_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with file_store_lock:
        # 清理过期文件和超量文件（节流：5分钟内不重复执行）
        cleanup_old_files()

        file_store[file_id] = {
            'original_name': original_name,
            'save_name': save_name,
            'save_dir': save_dir,
            'size': file_size,
            'size_str': format_file_size(file_size),
            'upload_time': upload_time,
            'download_count': 0
        }
        store_snapshot = dict(file_store)

    # 持久化存储（锁外执行快照，避免磁盘 IO 持锁；也不怕并发修改 dict）
    save_file_data(store_snapshot)

    # 生成分享链接
    share_link = f"http://{get_current_ip()}:{Config.PORT}/download/{file_id}"

    log_upload(original_name, format_file_size(file_size), share_link)

    return jsonify({
        'success': True,
        'message': '上传成功',
        'file_id': file_id,
        'original_name': original_name,
        'size_str': format_file_size(file_size),
        'share_link': share_link
    })


@app.route('/download/<file_id>')
def download_page(file_id: str):
    """文件下载页面 - 显示文件信息和下载按钮"""
    # 先获取 IP（锁外执行，避免 socket 连接持锁阻塞）
    current_ip = get_current_ip()
    # 修复：读操作加锁
    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            abort(404)
        # 拷贝一份快照用于渲染
        info_snapshot = dict(file_info)

    share_link = f"http://{current_ip}:{Config.PORT}/download/{file_id}"

    # 判断文件预览类型
    original_name = info_snapshot.get('original_name', '')
    file_type = get_preview_type(original_name)

    return render_template(
        'index.html',
        download_mode=True,
        file_id=file_id,
        file_info=info_snapshot,
        file_type=file_type,
        share_link=share_link,
        local_ip=current_ip,
        port=Config.PORT,
        files=[]
    )


@app.route('/file/<file_id>')
def serve_file(file_id: str):
    """实际文件下载接口"""
    # 修复：读取和计数更新放在同一个锁内，避免竞态
    store_snapshot = None
    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            abort(404)
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')
        original_name = file_info.get('original_name', 'download')

        # 只对完整GET请求计数
        if request.method == 'GET' and 'Range' not in request.headers:
            file_info['download_count'] = file_info.get('download_count', 0) + 1
            store_snapshot = dict(file_store)

    # 持久化存储（锁外执行快照）
    if store_snapshot:
        save_file_data(store_snapshot)

    # 获取文件路径
    file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path

    if not os.path.exists(os.path.join(file_path_dir, save_name)):
        abort(404)

    # 下载日志（锁外执行，避免长IO持锁）
    if request.method == 'GET' and 'Range' not in request.headers:
        log_download(original_name, request.remote_addr)

    # 获取 MIME 类型
    mime_type, _ = mimetypes.guess_type(original_name)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    # 使用 send_from_directory 安全地发送文件
    # 兼容旧版 Werkzeug（< 2.3 用 attachment_filename，>= 2.3 用 download_name）
    send_kwargs = dict(as_attachment=True, mimetype=mime_type)
    try:
        # 优先使用新参数名
        return send_from_directory(
            file_path_dir,
            save_name,
            download_name=original_name,
            **send_kwargs
        )
    except TypeError:
        # 旧版 Werkzeug 回退
        return send_from_directory(
            file_path_dir,
            save_name,
            attachment_filename=original_name,
            **send_kwargs
        )


@app.route('/preview/file/<file_id>')
def preview_file(file_id: str):
    """预览文件 - 内联返回（不触发下载计数）
    使用 send_from_directory 以支持 Range 请求（视频/音频可拖进度条、流式播放）"""
    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            abort(404)
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')
        original_name = file_info.get('original_name', '')

    file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
    if not os.path.exists(os.path.join(file_path_dir, save_name)):
        abort(404)

    mime_type, _ = mimetypes.guess_type(original_name)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    # as_attachment=False → Content-Disposition: inline，浏览器内联渲染
    # send_from_directory 原生支持 Range 请求，视频可拖进度条
    resp = send_from_directory(
        file_path_dir,
        save_name,
        as_attachment=False,
        mimetype=mime_type
    )
    # 禁止缓存，避免浏览器缓存旧响应导致预览异常
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/preview/pdfviewer/<file_id>')
def preview_pdf_viewer(file_id: str):
    """PDF.js 查看器 - 将PDF数据以base64内嵌到HTML，避免浏览器/代理干扰fetch请求"""
    import base64

    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            abort(404)
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')

    file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
    file_path = os.path.join(file_path_dir, save_name)

    if not os.path.exists(file_path):
        abort(404)

    # 将PDF文件读取并base64编码，直接内嵌到HTML中
    # PDF.js从内存加载数据，不再需要发fetch请求到 /preview/file/
    # 这样彻底避免代理/浏览器缓存/扩展等中间层对PDF请求的干扰
    try:
        with open(file_path, 'rb') as f:
            pdf_bytes = f.read()
        pdf_b64 = base64.b64encode(pdf_bytes).decode('ascii')
    except Exception as e:
        html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PDF Preview</title>
<style>html,body{{margin:0;padding:0;height:100%;background:#525659;}}
.error{{color:#fff;text-align:center;padding:40px;font-family:sans-serif;}}</style>
</head><body><div class="error">读取PDF失败: {str(e)}</div></body></html>'''
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PDF Preview</title>
<style>
  html, body {{ margin:0; padding:0; height:100%; background:#525659; }}
  #viewerContainer {{ position:absolute; top:0; left:0; right:0; bottom:0; overflow:auto; }}
  canvas {{ display:block; margin:8px auto; box-shadow:0 2px 8px rgba(0,0,0,0.3); max-width:100%; }}
  .error {{ color:#fff; text-align:center; padding:40px; font-family:sans-serif; }}
  .loading {{ color:#ccc; text-align:center; padding:40px; font-family:sans-serif; }}
</style>
</head>
<body>
<div id="viewerContainer"><div class="loading">PDF 加载中...</div></div>
<script src="/static/js/pdf.min.js"></script>
<script>
pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/js/pdf.worker.min.js';
var container = document.getElementById('viewerContainer');

// 从内嵌的base64数据加载PDF，不发送网络请求
var pdfBase64 = '{pdf_b64}';
var pdfBinary = atob(pdfBase64);
var pdfArray = new Uint8Array(pdfBinary.length);
for (var i = 0; i < pdfBinary.length; i++) {{
    pdfArray[i] = pdfBinary.charCodeAt(i);
}}

pdfjsLib.getDocument({{data: pdfArray}}).promise.then(function(pdf) {{
    container.innerHTML = '';
    var containerWidth = container.clientWidth - 40;
    for (var i = 1; i <= pdf.numPages; i++) {{
        (function(pageNum) {{
            pdf.getPage(pageNum).then(function(page) {{
                var unscaledViewport = page.getViewport({{scale: 1}});
                var scale = Math.min(containerWidth / unscaledViewport.width, 2.0);
                var viewport = page.getViewport({{scale: scale}});
                var canvas = document.createElement('canvas');
                canvas.width = viewport.width;
                canvas.height = viewport.height;
                container.appendChild(canvas);
                page.render({{canvasContext: canvas.getContext('2d'), viewport: viewport}});
            }});
        }})(i);
    }}
}}).catch(function(err) {{
    container.innerHTML = '<div class="error">PDF 加载失败: ' + err.message + '</div>';
}});
</script>
</body>
</html>'''
    response = make_response(html)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response


@app.route('/static/js/<path:filename>')
def serve_static_js(filename: str):
    """提供静态JS文件"""
    return send_from_directory('static/js', filename)


@app.route('/preview/text/<file_id>')
def preview_text(file_id: str):
    """文本文件预览 API - 返回前200行内容"""
    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            return jsonify({'error': '文件不存在'}), 404
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')

    file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
    file_path = os.path.join(file_path_dir, save_name)

    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在'}), 404

    # 限制读取大小：最大 100KB
    MAX_BYTES = 100 * 1024
    MAX_LINES = 200
    truncated = False
    lines = []
    total_lines = 0

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                total_lines += 1
                if not truncated and len(lines) < MAX_LINES:
                    if sum(len(l) for l in lines) + len(line) > MAX_BYTES:
                        truncated = True
                    else:
                        lines.append(line)
                # 如果已截断，只计数不存储
            # 检查是否超过行数限制
            if len(lines) == MAX_LINES and total_lines > MAX_LINES:
                truncated = True
    except Exception as e:
        return jsonify({'error': f'读取失败: {str(e)}'}), 500

    return jsonify({
        'content': ''.join(lines),
        'truncated': truncated,
        'shown_lines': len(lines),
        'total_lines': total_lines
    })


@app.route('/preview/office/<file_id>')
def preview_office(file_id: str):
    """Office 文件预览 - 读取 Excel 内容转为 HTML 表格"""
    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            return jsonify({'error': '文件不存在'}), 404
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')
        original_name = file_info.get('original_name', '')

    file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
    file_path = os.path.join(file_path_dir, save_name)

    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在'}), 404

    ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''

    try:
        if ext in ('xlsx', 'xls'):
            html_parts = []
            sheets_count = 0
            
            if ext == 'xlsx':
                # 使用 openpyxl 读取 .xlsx 文件
                import openpyxl
                wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
                sheets_count = len(wb.sheetnames)
                for sheet_name in wb.sheetnames:
                    ws = wb[sheet_name]
                    html_parts.append(f'<h3 style="margin:16px 0 8px; font-size:14px;">📄 {sheet_name}</h3>')
                    html_parts.append('<div style="overflow-x:auto;">')
                    html_parts.append('<table style="width:100%; border-collapse:collapse; font-size:13px;">')
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        tag = 'th' if i == 0 else 'td'
                        bg = 'background:var(--primary,#7c3aed); color:#fff;' if i == 0 else ''
                        cells = ''.join(
                            f'<{tag} style="border:1px solid var(--border,#e5e7eb); padding:6px 10px; text-align:left; {bg}">{_office_cell(v)}</{tag}>'
                            for v in row
                        )
                        html_parts.append(f'<tr>{cells}</tr>')
                    html_parts.append('</table></div>')
                wb.close()
            else:
                # 使用 xlrd 读取旧版 .xls 文件
                import xlrd
                wb = xlrd.open_workbook(file_path)
                sheets_count = wb.nsheets
                for sheet in wb.sheets():
                    html_parts.append(f'<h3 style="margin:16px 0 8px; font-size:14px;">📄 {sheet.name}</h3>')
                    html_parts.append('<div style="overflow-x:auto;">')
                    html_parts.append('<table style="width:100%; border-collapse:collapse; font-size:13px;">')
                    for i in range(sheet.nrows):
                        tag = 'th' if i == 0 else 'td'
                        bg = 'background:var(--primary,#7c3aed); color:#fff;' if i == 0 else ''
                        cells = ''.join(
                            f'<{tag} style="border:1px solid var(--border,#e5e7eb); padding:6px 10px; text-align:left; {bg}">{_office_cell(sheet.cell_value(i, j))}</{tag}>'
                            for j in range(sheet.ncols)
                        )
                        html_parts.append(f'<tr>{cells}</tr>')
                    html_parts.append('</table></div>')
            
            return jsonify({'type': 'table', 'content': ''.join(html_parts), 'sheets': sheets_count})
        elif ext == 'docx':
            # 使用 python-docx 读取 .docx 文件
            try:
                from docx import Document as DocxDocument
                doc = DocxDocument(file_path)
                paragraphs = [f'<p style="margin:4px 0;">{_office_cell(p.text)}</p>' for p in doc.paragraphs if p.text.strip()]
                return jsonify({'type': 'text', 'content': ''.join(paragraphs)})
            except ImportError:
                return jsonify({'error': 'python-docx 未安装，无法预览 .docx 文件'})
        elif ext in ('pptx', 'ppt'):
            try:
                from pptx import Presentation
                prs = Presentation(file_path)
                slides_html = []
                for i, slide in enumerate(prs.slides):
                    texts = []
                    for shape in slide.shapes:
                        if hasattr(shape, 'text') and shape.text.strip():
                            texts.append(shape.text)
                    slides_html.append(f'<div style="margin:12px 0; padding:12px; background:var(--bg-secondary,#f5f5f5); border-radius:8px;"><strong>第 {i+1} 页</strong><br>' + '<br>'.join(_office_cell(t) for t in texts) + '</div>')
                return jsonify({'type': 'text', 'content': ''.join(slides_html)})
            except ImportError:
                return jsonify({'error': 'python-pptx 未安装，无法预览 PPT 文件'})
        else:
            return jsonify({'error': f'不支持预览 .{ext} 格式'})
    except Exception as e:
        return jsonify({'error': f'预览失败: {str(e)}'}), 500


def _office_cell(value):
    """安全转换单元格值为 HTML 文本"""
    if value is None:
        return ''
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


@app.route('/delete/<file_id>', methods=['POST'])
def delete_file(file_id: str):
    """删除已分享的文件"""
    with file_store_lock:
        file_info = file_store.get(file_id)
        if not file_info:
            return jsonify({'success': False, 'message': '文件不存在'}), 404

        original_name = file_info.get('original_name', '未知文件')
        save_name = file_info.get('save_name', '')
        save_dir = file_info.get('save_dir', '')
        del file_store[file_id]
        store_snapshot = dict(file_store)

    # 持久化存储（锁外执行快照）
    save_file_data(store_snapshot)

    # 再删除磁盘上的文件（记录已移除，文件删除失败可忽略）
    file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
    file_path = os.path.join(file_path_dir, save_name)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError as e:
            print(f"[警告] 删除文件失败: {file_path} - {e}")

    # 如果是子目录方式，清理空目录
    if save_dir:
        sub_dir = os.path.join(upload_path, save_dir)
        if os.path.isdir(sub_dir):
            try:
                os.rmdir(sub_dir)
            except OSError:
                pass

    log_delete(original_name)

    return jsonify({'success': True, 'message': f'已删除: {original_name}'})


@app.route('/delete_all', methods=['POST'])
def delete_all_files():
    """删除所有已分享的文件"""
    with file_store_lock:
        count = len(file_store)
        if count == 0:
            return jsonify({'success': False, 'message': '没有文件可删除'}), 404

        files_to_delete = list(file_store.items())
        file_store.clear()
        store_snapshot = dict(file_store)

    # 持久化存储（锁外执行快照）
    save_file_data(store_snapshot)

    # 再删除磁盘上的文件
    for file_id, info in files_to_delete:
        save_name = info.get('save_name', '')
        save_dir = info.get('save_dir', '')
        file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
        file_path = os.path.join(file_path_dir, save_name)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                print(f"[警告] 删除文件失败: {file_path} - {e}")

        # 清理子目录
        if save_dir:
            sub_dir = os.path.join(upload_path, save_dir)
            if os.path.isdir(sub_dir):
                try:
                    os.rmdir(sub_dir)
                except OSError:
                    pass

    log_delete(f'全部文件 ({count}个)')

    return jsonify({'success': True, 'message': f'已删除全部 {count} 个文件'})


@app.route('/batch_download', methods=['POST'])
def batch_download():
    """批量下载选中的文件，打包为 ZIP 返回"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'message': '请求数据无效'}), 400

    file_ids = data.get('file_ids', [])
    if not file_ids or not isinstance(file_ids, list):
        return jsonify({'success': False, 'message': '未选择文件'}), 400

    # 限制一次性最多打包 50 个文件，防止内存爆炸
    if len(file_ids) > 50:
        return jsonify({'success': False, 'message': '一次最多打包 50 个文件'}), 400

    # 去重
    file_ids = list(dict.fromkeys(file_ids))

    with file_store_lock:
        files_to_zip = []
        for file_id in file_ids:
            info = file_store.get(file_id)
            if info:
                files_to_zip.append(info)

    if not files_to_zip:
        return jsonify({'success': False, 'message': '未找到所选文件'}), 404

    # 在内存中创建 ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for info in files_to_zip:
            save_name = info.get('save_name', '')
            save_dir = info.get('save_dir', '')
            file_path_dir = os.path.join(upload_path, save_dir) if save_dir else upload_path
            file_path = os.path.join(file_path_dir, save_name)
            original_name = info.get('original_name', save_name)

            if os.path.exists(file_path):
                # 写入 ZIP，使用原始文件名
                zf.write(file_path, original_name)
            else:
                log_info(f"批量打包时文件不存在: {original_name}")

    zip_buffer.seek(0)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_name = f'files_{timestamp}.zip'

    response = make_response(zip_buffer.getvalue())
    response.headers['Content-Type'] = 'application/zip'
    response.headers['Content-Disposition'] = f'attachment; filename="{zip_name}"'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/files')
def api_files():
    """JSON 接口 - 列出所有已分享文件"""
    # 先获取 IP（锁外执行，避免 socket 连接持锁阻塞）
    current_ip = get_current_ip()
    # 修复：读操作加锁
    with file_store_lock:
        files_list = []
        for file_id, info in file_store.items():
            files_list.append({
                'file_id': file_id,
                'original_name': info.get('original_name', '未知文件'),
                'size': info.get('size', 0),
                'size_str': info.get('size_str', '未知'),
                'upload_time': info.get('upload_time', ''),
                'download_count': info.get('download_count', 0),
                'share_link': f"http://{current_ip}:{Config.PORT}/download/{file_id}"
            })
    return jsonify({'files': files_list, 'total': len(files_list)})


# ============================================================
# 错误处理
# ============================================================

@app.errorhandler(404)
def page_not_found(e):
    """404 错误页面"""
    return render_template(
        'index.html',
        error_mode=True,
        error_code=404,
        error_message='页面未找到',
        local_ip=get_current_ip(),
        port=Config.PORT,
        files=[]
    ), 404


@app.errorhandler(413)
def file_too_large(e):
    """文件过大错误"""
    return jsonify({
        'success': False,
        'message': f'文件过大，最大允许 {format_file_size(Config.MAX_CONTENT_LENGTH)}'
    }), 413


# ============================================================
# 启动入口
# ============================================================

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def display_width(s: str) -> int:
    """计算字符串在终端的显示宽度（中文/全角字符占2列，忽略ANSI转义序列）"""
    clean = _ANSI_RE.sub('', s)
    w = 0
    for ch in clean:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            w += 2
        else:
            w += 1
    return w


def pad_line(s: str, width: int) -> str:
    """将字符串填充到指定显示宽度"""
    pad = width - display_width(s)
    return s + ' ' * max(0, pad)


def enable_ansi():
    """启用Windows控制台ANSI转义序列支持"""
    if sys.platform == 'win32':
        try:
            import ctypes
            h = ctypes.windll.kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode))
            ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            pass


def print_qr_color(url: str):
    """用qrcode库自带的ASCII输出显示二维码"""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        print()
        print(f"  {Color.CYAN}使用手机扫描二维码:{Color.RESET}")
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        pass


_qr_window_open = False

def show_qr_window(url: str, parent=None):
    """弹出小窗口显示二维码图片（使用 Toplevel 避免与主窗口冲突）"""
    global _qr_window_open
    if _qr_window_open:
        return

    try:
        import qrcode
        import tempfile
        import tkinter as tk
    except ImportError:
        return

    _qr_window_open = True

    def _show():
        global _qr_window_open
        qr_win = None
        tmp_path = None
        try:
            qr = qrcode.QRCode(border=2, box_size=10)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            tmp = tempfile.NamedTemporaryFile(suffix='.gif', delete=False)
            img.save(tmp.name, format='GIF')
            tmp.close()
            tmp_path = tmp.name

            # 使用 Toplevel 而非 Tk()——避免多个 Tk 实例冲突
            if parent and parent.winfo_exists():
                qr_win = tk.Toplevel(parent)
            else:
                qr_win = tk.Toplevel()
            qr_win.title("扫码访问")
            qr_win.resizable(False, False)
            qr_win.configure(bg="#1a1a2e")

            tk_img = tk.PhotoImage(file=tmp.name)
            label = tk.Label(qr_win, image=tk_img, bg="#ffffff")
            label.image = tk_img
            label.pack(padx=12, pady=12)

            # 底部提示
            tk.Label(qr_win, text=url, font=("Consolas", 9),
                     fg="#90a4ae", bg="#1a1a2e").pack(pady=(0, 8))

            def on_close():
                global _qr_window_open
                _qr_window_open = False
                try:
                    qr_win.destroy()
                except Exception:
                    pass

            qr_win.protocol("WM_DELETE_WINDOW", on_close)

            # 居中
            qr_win.update_idletasks()
            pw = qr_win.winfo_width()
            ph = qr_win.winfo_height()
            if pw > 0 and ph > 0:
                sx = (qr_win.winfo_screenwidth() - pw) // 2
                sy = (qr_win.winfo_screenheight() - ph) // 2
                qr_win.geometry(f"+{sx}+{sy}")

            qr_win.transient(parent)
            qr_win.grab_set()
            qr_win.mainloop()
        except Exception:
            pass
        finally:
            _qr_window_open = False
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    threading.Thread(target=_show, daemon=True).start()


def start_qr_key_listener(url: str):
    """后台监听按键，按Q弹出二维码窗口（无需回车）"""
    def _listen():
        try:
            import msvcrt
            import time
            while True:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b'q', b'Q'):
                        show_qr_window(url)
                time.sleep(0.1)
        except (ImportError, EOFError, OSError):
            pass

    threading.Thread(target=_listen, daemon=True).start()


def print_startup_info():
    """打印启动信息 - 科技感风格"""
    current_ip = get_current_ip()
    url = f"http://{current_ip}:{Config.PORT}"
    limit = format_file_size(Config.MAX_CONTENT_LENGTH)

    W = 50
    line = "─" * W

    box = [
        f"┌{line}┐",
        f"│{pad_line('', W)}│",
        f"│{pad_line('    ███╗   ██╗███████╗████████╗', W)}│",
        f"│{pad_line('    ████╗  ██║██╔════╝╚══██╔══╝', W)}│",
        f"│{pad_line('    ██╔██╗ ██║█████╗     ██║', W)}│",
        f"│{pad_line('    ██║╚██╗██║██╔══╝     ██║', W)}│",
        f"│{pad_line('    ██║ ╚████║███████╗   ██║', W)}│",
        f"│{pad_line('    ╚═╝  ╚═══╝╚══════╝   ╚═╝', W)}│",
        f"│{pad_line('', W)}│",
        f"│{pad_line('LAN File Share Tool v1.0', W)}│",
        f"│{pad_line('', W)}│",
        f"├{line}┤",
        f"│{pad_line(f'  > 本机 IP    {current_ip}', W)}│",
        f"│{pad_line(f'  > 服务端口   {Config.PORT}', W)}│",
        f"│{pad_line(f'  > 访问链接   {url}', W)}│",
        f"│{pad_line(f'  > 上传限制   {limit}', W)}│",
        f"├{line}┤",
        f"│{pad_line('  将链接分享给内网用户即可访问', W)}│",
        f"│{pad_line('', W)}│",
        f"│{pad_line(f'  {Color.GREEN}✓ 访问链接已复制到剪贴板，直接 Ctrl+V 粘贴即可{Color.RESET}', W)}│",
        f"│{pad_line('', W)}│",
        f"│{pad_line(f'  {Color.CYAN}手机扫描二维码访问（按 Q 键显示二维码）{Color.RESET}', W)}│",
        f"└{line}┘",
    ]

    print()
    for box_line in box:
        print(f"  {box_line}")

    # 启动按键监听（按Q弹出二维码）
    start_qr_key_listener(url)

    # 自动复制链接到剪贴板
    try:
        import subprocess
        subprocess.run(['clip.exe'], input=url.encode('utf-8'), check=True, timeout=3)
    except Exception:
        pass

    print()


def disable_console_quick_edit():
    """禁用Windows控制台快速编辑模式，防止选中文字时阻塞程序"""
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            STD_INPUT_HANDLE = -10
            handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            new_mode = mode.value & ~0x0040 & ~0x0010
            kernel32.SetConsoleMode(handle, new_mode)
        except Exception:
            pass


# ============================================================
# GUI 模式 - tkinter 窗口（替代控制台）
# ============================================================

def run_flask():
    """在后台线程启动 Flask 服务器（无日志输出）"""
    import logging as _logging
    _logging.getLogger('werkzeug').setLevel(_logging.ERROR)
    app.run(
        host='0.0.0.0',
        port=Config.PORT,
        debug=False,
        use_reloader=False,
        threaded=True
    )


def run_gui():
    """GUI 主窗口"""
    import tkinter as tk
    import webbrowser
    import subprocess as _subprocess

    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(0.6)

    current_ip = get_current_ip()
    port = Config.PORT
    url = f"http://{current_ip}:{port}"
    limit_str = format_file_size(Config.MAX_CONTENT_LENGTH)

    BG     = "#f5f5f7"; CARD = "#ffffff"
    ACCENT = "#007aff"; ACCENT_H = "#0066d6"
    GREEN  = "#34c759"; TEXT = "#1d1d1f"
    DIM    = "#86868b"; RED = "#ff3b30"
    BORDER = "#d2d2d7"

    root = tk.Tk()
    root.title("内网文件分享工具")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(420, 500)
    try:
        ico = get_resource_path('app.ico')
        if os.path.exists(ico): root.iconbitmap(ico)
    except: pass

    def L(p, t, s, fg=TEXT, bg=CARD, bold=False):
        w = "bold" if bold else "normal"
        if isinstance(s, tuple):
            return tk.Label(p, text=t, font=(s[0], s[1], w), fg=fg, bg=bg)
        return tk.Label(p, text=t, font=("Microsoft YaHei UI", s, w), fg=fg, bg=bg)

    def btn(p, t, cmd):
        b = tk.Button(p, text=t, command=cmd, font=("Microsoft YaHei UI", 10),
            fg=ACCENT, bg="#f2f2f7", activebackground="#e5e5ea", activeforeground=ACCENT_H,
            relief=tk.FLAT, cursor="hand2", padx=12, pady=10, border=0)
        b.bind("<Enter>", lambda e: b.configure(bg="#e5e5ea"))
        b.bind("<Leave>", lambda e: b.configure(bg="#f2f2f7"))
        b.bind("<ButtonPress>", lambda e: b.configure(relief=tk.SUNKEN))
        b.bind("<ButtonRelease>", lambda e: b.configure(relief=tk.FLAT))
        return b

    # 标题
    header = tk.Frame(root, bg=BG)
    header.pack(fill=tk.X, padx=22, pady=(20, 6))
    L(header, "内网文件分享", 17, TEXT, BG, True).pack(side=tk.LEFT)
    L(header, "v1.3", ("Segoe UI", 10), DIM, BG).pack(side=tk.RIGHT, pady=(5, 0))

    main = tk.Frame(root, bg=BG)
    main.pack(fill=tk.BOTH, expand=True, padx=22)

    # 卡片
    card_outer = tk.Frame(main, bg=CARD, relief=tk.GROOVE, bd=2)
    card_outer.pack(fill=tk.X, pady=(0, 10))
    cin = tk.Frame(card_outer, bg=CARD)
    cin.pack(fill=tk.X, padx=18, pady=14)

    # 状态
    srow = tk.Frame(cin, bg=CARD); srow.pack(fill=tk.X, pady=(0, 12))
    dot = tk.Canvas(srow, width=14, height=14, bg=CARD, highlightthickness=0)
    dot.pack(side=tk.LEFT); dot.create_oval(1, 1, 13, 13, fill=GREEN, outline="")
    L(srow, "  服务运行中", 12, GREEN, CARD, True).pack(side=tk.LEFT)
    L(srow, f"单文件最大 {limit_str}", 9, DIM, CARD).pack(side=tk.RIGHT, pady=(1, 0))

    tk.Frame(cin, bg=BORDER, height=1).pack(fill=tk.X, pady=(0, 12))

    # IP + 端口
    irow = tk.Frame(cin, bg=CARD); irow.pack(fill=tk.X, pady=(0, 10))
    col_l = tk.Frame(irow, bg=CARD); col_l.pack(side=tk.LEFT)
    L(col_l, "本机 IP 地址", ("Microsoft YaHei UI", 8), DIM, CARD).pack(anchor=tk.W)
    L(col_l, current_ip, ("Segoe UI", 14), TEXT, CARD, True).pack(anchor=tk.W, pady=(2, 0))
    col_r = tk.Frame(irow, bg=CARD); col_r.pack(side=tk.RIGHT)
    L(col_r, "服务端口", ("Microsoft YaHei UI", 8), DIM, CARD).pack(anchor=tk.E)
    L(col_r, str(port), ("Segoe UI", 14), TEXT, CARD, True).pack(anchor=tk.E, pady=(2, 0))

    tk.Frame(cin, bg=BORDER, height=1).pack(fill=tk.X, pady=(0, 12))

    # 链接
    L(cin, "分享链接", ("Microsoft YaHei UI", 8), DIM, CARD).pack(anchor=tk.W, pady=(0, 6))
    url_row = tk.Frame(cin, bg="#f2f2f7"); url_row.pack(fill=tk.X)
    url_inner = tk.Frame(url_row, bg="#f2f2f7"); url_inner.pack(fill=tk.X, padx=12, pady=10)
    L(url_inner, url, ("Segoe UI", 11), ACCENT, "#f2f2f7", True).pack(side=tk.LEFT)
    copy_sm = tk.Button(url_inner, text="复制", font=("Microsoft YaHei UI", 9),
        fg=ACCENT, bg="#f2f2f7", activebackground="#f2f2f7",
        activeforeground=ACCENT_H, relief=tk.FLAT, cursor="hand2", border=0)
    copy_sm.pack(side=tk.RIGHT)
    def do_copy():
        root.clipboard_clear(); root.clipboard_append(url)
        copy_sm.config(text="已复制", fg=GREEN)
        root.after(1500, lambda: copy_sm.config(text="复制", fg=ACCENT))
    copy_sm.config(command=do_copy)

    fcnt = L(cin, "", 9, DIM, CARD); fcnt.pack(anchor=tk.W, pady=(10, 0))

    # 日志弹窗
    _log_win = None  # 日志窗口单例
    def show_log_window(parent):
        nonlocal _log_win
        # 已有窗口则提到前面
        if _log_win and _log_win.winfo_exists():
            _log_win.lift()
            _log_win.focus_force()
            return

        w = tk.Toplevel(parent); w.title("运行日志"); w.geometry("500x380")
        w.configure(bg=CARD); w.transient(parent)
        _log_win = w

        def on_close():
            nonlocal _log_win
            _log_win = None
            w.destroy()
        w.protocol("WM_DELETE_WINDOW", on_close)

        head = tk.Frame(w, bg=ACCENT, height=34); head.pack(fill=tk.X); head.pack_propagate(False)
        L(head, "  运行日志", ("Microsoft YaHei UI", 10), "#fff", ACCENT, True).pack(side=tk.LEFT, pady=7)
        L(head, f"共 {len(_gui_logs)} 条  ", ("Microsoft YaHei UI", 8), "#a8c8fa", ACCENT).pack(side=tk.RIGHT, pady=8)
        tf = tk.Frame(w, bg=CARD); tf.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(tf); sb.pack(side=tk.RIGHT, fill=tk.Y)
        tx = tk.Text(tf, font=("Segoe UI", 10), bg="#f5f5f7", fg=TEXT,
                     wrap=tk.WORD, state=tk.DISABLED, padx=12, pady=10,
                     yscrollcommand=sb.set, relief=tk.FLAT, border=0)
        tx.pack(fill=tk.BOTH, expand=True); sb.config(command=tx.yview)
        tx.config(state=tk.NORMAL)
        with _gui_logs_lock: snap = list(_gui_logs)
        if not snap:
            tx.insert(tk.END, "暂无日志", "empty"); tx.tag_config("empty", foreground=DIM)
        else:
            tx.tag_config("up", foreground=GREEN)
            tx.tag_config("dl", foreground=ACCENT)
            tx.tag_config("del", foreground=RED)
            tx.tag_config("inf", foreground=DIM)
            for t, action, msg in reversed(snap):
                tag = {"上传":"up","下载":"dl","删除":"del"}.get(action,"inf")
                tx.insert(tk.END, f"{t}  [{action}]  {msg}\n", tag)
        tx.config(state=tk.DISABLED)
        bbar = tk.Frame(w, bg=CARD); bbar.pack(fill=tk.X, padx=14, pady=8)
        tk.Button(bbar, text="清空", font=("Microsoft YaHei UI", 8), fg=DIM, bg=CARD,
                  relief=tk.FLAT, cursor="hand2", border=0,
                  command=lambda:[_gui_logs.clear(), on_close(), show_log_window(parent)])\
            .pack(side=tk.LEFT)
        tk.Button(bbar, text="关闭", font=("Microsoft YaHei UI", 8), fg="#fff", bg=ACCENT,
                  activebackground=ACCENT_H, activeforeground="#fff", relief=tk.FLAT,
                  cursor="hand2", padx=14, pady=5, border=0, command=on_close).pack(side=tk.RIGHT)
        w.update_idletasks(); pw = w.winfo_width()
        if pw > 0: w.geometry(f"+{(w.winfo_screenwidth()-pw)//2}+{(w.winfo_screenheight()-w.winfo_height())//2}")

    # 2×2 按钮
    bf = tk.Frame(main, bg=BG); bf.pack(fill=tk.X, pady=(2, 0))
    bf.columnconfigure(0, weight=1, uniform="g"); bf.columnconfigure(1, weight=1, uniform="g")
    btn(bf, "在浏览器打开", lambda: webbrowser.open(url))\
        .grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 8))
    btn(bf, "显示二维码", lambda: show_qr_window(url, root))\
        .grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 8))
    btn(bf, "查看日志", lambda: show_log_window(root))\
        .grid(row=1, column=0, sticky="ew", padx=(0, 4))
    btn(bf, "打开文件目录",
        lambda: _subprocess.Popen(['explorer', os.path.join(get_app_dir(), Config.UPLOAD_DIR)]))\
        .grid(row=1, column=1, sticky="ew", padx=(4, 0))

    # 底部
    foot = tk.Frame(root, bg=BG)
    foot.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 14))
    L(foot, "关闭窗口即停止服务", ("Microsoft YaHei UI", 8), DIM, BG).pack()

    root.update_idletasks()
    rh = root.winfo_reqheight()
    root.geometry(f"430x{max(rh+8,540)}+{(root.winfo_screenwidth()-430)//2}+{(root.winfo_screenheight()-max(rh+8,540))//2}")

    def refresh():
        with file_store_lock: n = len(file_store)
        fcnt.config(text=f"已分享 {n} 个文件")
        root.after(3000, refresh)
    root.after(1000, refresh)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    sys.exit(0)


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    import argparse as _argparse

    _parser = _argparse.ArgumentParser()
    _parser.add_argument('--console', action='store_true', help='强制使用控制台模式')
    _parser.add_argument('--gui', action='store_true', help='强制使用GUI模式')
    _args = _parser.parse_args()

    enable_ansi()
    check_single_instance()

    is_frozen = getattr(sys, 'frozen', False)

    if is_frozen and not _args.console:
        # EXE 双击启动 → GUI 模式
        run_gui()
    elif _args.gui:
        # 源码运行 + --gui → GUI 模式
        run_gui()
    else:
        # 默认控制台模式
        import logging

        disable_console_quick_edit()

        class NoDevWarningFilter(logging.Filter):
            def filter(self, record):
                return 'This is a development server' not in record.getMessage()
        logging.getLogger('werkzeug').addFilter(NoDevWarningFilter())

        print_startup_info()
        app.run(
            host='0.0.0.0',
            port=Config.PORT,
            debug=False,
            use_reloader=False,
            threaded=True
        )
