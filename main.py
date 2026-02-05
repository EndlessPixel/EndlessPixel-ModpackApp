#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Minecraft整合包下载器
用于下载EndlessPixel整合包，支持多线程下载和镜像源选择
"""

import sys
import os
import re
import json
import threading
import queue
import yaml
import requests
from urllib.parse import urljoin, urlparse
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QProgressBar, QFileDialog, QComboBox, QMenu, QAction,
    QMessageBox, QSplitter, QStyleFactory, QStyle, QDialog,
    QDialogButtonBox, QSpinBox, QCheckBox, QLineEdit, QTextEdit,
    QGroupBox, QGridLayout, QSizePolicy, QTabWidget, QTextBrowser
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QUrl, QEvent, QSettings,
    QPoint, QSize, QTimer
)
from PyQt5.QtGui import (
    QIcon, QFont, QColor, QPalette, QTextCursor,
    QDesktopServices, QPixmap
)
import markdown
from datetime import datetime

# ========== 修复：提前设置高DPI属性 ==========
# 必须在创建QApplication之前设置
if hasattr(Qt, 'AA_EnableHighDpiScaling'):
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

# 应用程序版本号
APP_VERSION = "1.1"
# GitHub更新检查URL
UPDATE_CHECK_URL = "https://api.github.com/repos/EndlessPixel/EndlessPixel-ModpackApp/releases/latest"
# 版本日志API URL
RELEASE_NOTES_URL = "https://api.github.com/repos/EndlessPixel/EndlessPixel-Modpack/releases"

# 配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yml")

# 设置中文字体支持
QApplication.setFont(QFont("Microsoft YaHei", 9))

# ===================== 配置管理类 =====================
class ConfigManager:
    """配置管理器，负责加载、保存和验证配置"""
    def __init__(self):
        self.config = self.load_config()
    
    def load_config(self):
        """加载配置文件，缺失则生成默认配置"""
        default_config = {
            "common": {
                "download_dir": os.path.join(os.path.expanduser("~"), "Downloads", "EndlessPixel"),
                "max_threads": 4,
                "mirror": "GitHub",
                "check_update_on_startup": True,
                "window_geometry": [900, 600],
                "window_position": [100, 100]
            },
            "mirrors": {
                "GitHub": "",
                "Cloudflare": "https://gh-proxy.org/",
                "Fastly": "https://cdn.gh-proxy.org/",
                "Edgeone": "https://edgeone.gh-proxy.org/",
                "香港": "https://hk.gh-proxy.org/"
            }
        }
        
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                
                # 合并配置，确保默认配置项存在
                self._merge_config(config, default_config)
                return config
            else:
                # 生成默认配置文件
                self.save_config(default_config)
                return default_config
        except Exception as e:
            print(f"加载配置失败，使用默认配置: {e}")
            return default_config
    
    def _merge_config(self, config, default):
        """递归合并配置"""
        for key, value in default.items():
            if key not in config:
                config[key] = value
            elif isinstance(value, dict) and isinstance(config.get(key), dict):
                self._merge_config(config[key], value)
    
    def save_config(self, config=None):
        """保存配置文件"""
        if config is None:
            config = self.config
        
        # 确保配置目录存在
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, indent=2, encoding='utf-8', allow_unicode=True)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False
    
    def get(self, key_path, default=None):
        """获取配置值，支持点分隔的路径"""
        keys = key_path.split('.')
        value = self.config
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key_path, value):
        """设置配置值，支持点分隔的路径"""
        keys = key_path.split('.')
        config = self.config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value
        return self.save_config()
    
    def get_mirrors(self):
        """获取所有镜像源"""
        return self.config.get("mirrors", {})
    
    def add_mirror(self, name, url):
        """添加镜像源"""
        self.config["mirrors"][name] = url
        return self.save_config()
    
    def remove_mirror(self, name):
        """移除镜像源"""
        if name in self.config["mirrors"]:
            del self.config["mirrors"][name]
            return self.save_config()
        return False

# ===================== 下载线程类 =====================
class DownloadWorker(QThread):
    """下载线程类，负责多线程下载文件"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    speed = pyqtSignal(str)
    
    def __init__(self, url, save_path, threads=4):
        super().__init__()
        self.url = url
        self.save_path = save_path
        self.threads = max(1, min(64, threads))  # 限制线程数1-64
        self.chunk_queue = queue.Queue()
        self.running = True
        self.paused = False
        self.total_size = 0
        self.downloaded_size = 0
        self.lock = threading.Lock()
        self.start_time = None
        self.last_downloaded = 0
    
    def run(self):
        try:
            # 获取文件大小
            response = requests.head(self.url, allow_redirects=True, verify=False, timeout=10)
            response.raise_for_status()
            self.total_size = int(response.headers.get('content-length', 0))
            
            if self.total_size == 0:
                self.error.emit("无法获取文件大小")
                return
            
            # 创建保存目录
            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
            
            # 创建保存文件
            with open(self.save_path, 'wb') as f:
                f.write(b'')
            
            # 计算每个线程下载的块大小
            chunk_size = self.total_size // self.threads
            
            # 记录开始时间
            self.start_time = datetime.now()
            
            # 创建并启动下载线程
            threads = []
            for i in range(self.threads):
                start = i * chunk_size
                end = self.total_size - 1 if i == self.threads - 1 else (i + 1) * chunk_size - 1
                thread = threading.Thread(target=self.download_chunk, args=(start, end))
                threads.append(thread)
                thread.start()
            
            # 启动速度计算定时器
            speed_timer = threading.Thread(target=self.calculate_speed)
            speed_timer.daemon = True
            speed_timer.start()
            
            # 等待所有线程完成
            for thread in threads:
                thread.join()
            
            if self.running and not self.paused:
                self.finished.emit(self.save_path)
        except Exception as e:
            self.error.emit(str(e))
    
    def download_chunk(self, start, end):
        """下载文件的一个块"""
        headers = {'Range': f'bytes={start}-{end}'}
        
        try:
            response = requests.get(
                self.url, 
                headers=headers, 
                stream=True, 
                allow_redirects=True, 
                verify=False,
                timeout=10
            )
            response.raise_for_status()
            
            chunk_size = 8192
            downloaded = 0
            
            # 写入文件的指定位置
            with open(self.save_path, 'r+b') as f:
                f.seek(start)
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not self.running:
                        break
                    if self.paused:
                        while self.paused and self.running:
                            threading.Event().wait(0.1)
                        if not self.running:
                            break
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        with self.lock:
                            self.downloaded_size += len(chunk)
                            progress = int(self.downloaded_size / self.total_size * 100)
                            self.progress.emit(progress)
        except Exception as e:
            self.error.emit(f"下载块时出错: {str(e)}")
    
    def calculate_speed(self):
        """计算下载速度"""
        while self.running and self.downloaded_size < self.total_size:
            threading.Event().wait(1)
            if not self.running or self.paused:
                continue
            
            with self.lock:
                current = self.downloaded_size
                delta = current - self.last_downloaded
                self.last_downloaded = current
                
                if delta > 0:
                    speed = self.format_size(delta) + "/s"
                    self.speed.emit(speed)
    
    def pause(self):
        """暂停下载"""
        self.paused = True
    
    def resume(self):
        """恢复下载"""
        self.paused = False
    
    def stop(self):
        """停止下载"""
        self.running = False
        self.paused = False
        self.wait()
    
    @staticmethod
    def format_size(size_bytes):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"

# ===================== 镜像管理器 =====================
class MirrorManager:
    """镜像源管理器"""
    def __init__(self, config_manager):
        self.config_manager = config_manager
    
    def get_mirror_url(self, original_url, mirror_name):
        """根据镜像名称获取镜像URL"""
        mirrors = self.config_manager.get_mirrors()
        mirror_url = mirrors.get(mirror_name, "")
        if mirror_url:
            # 清理镜像URL末尾的空格
            mirror_url = mirror_url.strip()
            return urljoin(mirror_url, original_url)
        return original_url
    
    def get_mirror_names(self):
        """获取所有镜像名称"""
        return list(self.config_manager.get_mirrors().keys())
    
    def add_mirror(self, name, url):
        """添加镜像源"""
        return self.config_manager.add_mirror(name, url)
    
    def remove_mirror(self, name):
        """移除镜像源"""
        return self.config_manager.remove_mirror(name)

# ===================== 版本管理器 =====================
class VersionManager:
    """版本管理器，负责获取和解析版本信息"""
    GITHUB_API_URL = "https://api.github.com/repos/EndlessPixel/EndlessPixel-Modpack/releases"
    
    def __init__(self, mirror_manager, config_manager):
        self.mirror_manager = mirror_manager
        self.config_manager = config_manager
        self.current_mirror = self.config_manager.get("common.mirror", "GitHub")
    
    def set_mirror(self, mirror):
        """设置当前使用的镜像"""
        self.current_mirror = mirror
    
    def get_versions(self):
        """获取版本列表"""
        try:
            all_releases = []
            url = self.mirror_manager.get_mirror_url(self.GITHUB_API_URL, self.current_mirror)
            
            # 循环获取所有页面的版本信息
            while url:
                response = requests.get(url, verify=False, timeout=10)
                response.raise_for_status()
                
                # 添加当前页面的版本信息
                all_releases.extend(response.json())
                
                # 解析Link头字段，获取下一页的URL
                url = None
                if 'Link' in response.headers:
                    link_header = response.headers['Link']
                    next_match = re.search(r'<([^>]+)>; rel="next"', link_header)
                    if next_match:
                        url = next_match.group(1)
            
            releases = all_releases
            
            # 按版本号排序
            releases.sort(key=lambda x: self.parse_version(x['tag_name']), reverse=True)
            
            # 按MC版本分组
            mc_versions = {}
            for release in releases:
                tag_name = release['tag_name']
                assets = release.get('assets', [])
                
                # 跳过没有附件的版本
                if not assets:
                    continue
                
                # 查找符合格式的文件
                matched_asset = None
                pattern = r'^EndlessPixel\.\d+\.\d+(\.\d+)?-v\d+-(\d+\.\d+|b\d+)\.(zip|mrpack)$'
                
                for asset in assets:
                    asset_name = asset['name']
                    if re.match(pattern, asset_name):
                        matched_asset = asset
                        break
                
                # 跳过没有符合格式文件的版本
                if not matched_asset:
                    continue
                
                # 解析MC版本
                mc_version = self.extract_mc_version(tag_name)
                if not mc_version:
                    mc_version = "未知版本"
                
                # 获取版本详情
                release_notes = release.get('body', '暂无更新日志')
                published_at = release.get('published_at', '')
                
                # 添加到对应MC版本的列表中
                if mc_version not in mc_versions:
                    mc_versions[mc_version] = []
                
                mc_versions[mc_version].append({
                    'tag_name': tag_name,
                    'download_url': matched_asset['browser_download_url'],
                    'file_name': matched_asset['name'],
                    'file_size': matched_asset['size'],
                    'is_prerelease': release.get('prerelease', False),
                    'published_at': published_at,
                    'release_notes': release_notes,
                    'html_url': release.get('html_url', '')
                })
            
            return mc_versions
        except Exception as e:
            print(f"获取版本列表失败: {str(e)}")
            return {}
    
    def get_release_notes(self, tag_name):
        """获取指定版本的更新日志"""
        try:
            url = self.mirror_manager.get_mirror_url(self.GITHUB_API_URL, self.current_mirror)
            response = requests.get(url, verify=False, timeout=10)
            response.raise_for_status()
            
            releases = response.json()
            for release in releases:
                if release.get('tag_name') == tag_name:
                    return release.get('body', '暂无更新日志')
            return '未找到更新日志'
        except Exception as e:
            print(f"获取更新日志失败: {e}")
            return f"获取更新日志失败: {str(e)}"
    
    def extract_mc_version(self, tag_name):
        """从标签名中提取MC版本"""
        match = re.match(r'(\d+\.\d+(?:\.\d+)?)-', tag_name)
        if match:
            return match.group(1)
        return None
    
    def parse_version(self, version_str):
        """解析版本号，用于排序"""
        pre_release_match = re.search(r'-([a-zA-Z]+.*)$', version_str)
        pre_release = pre_release_match.group(1) if pre_release_match else ''
        
        main_version_str = version_str.replace(f"-{pre_release}", "") if pre_release else version_str
        main_parts = re.findall(r'\d+', main_version_str)
        main_parts = [int(part) for part in main_parts]
        
        pre_release_priority = 50 if pre_release else 100
        pre_release_num = 0
        
        if pre_release:
            pre_match = re.search(r'\d+', pre_release)
            if pre_match:
                pre_release_num = int(pre_match.group(0))
        
        return (main_parts, pre_release_priority, pre_release_num)

# ===================== 配置对话框 =====================
class ConfigDialog(QDialog):
    """配置对话框"""
    def __init__(self, config_manager, mirror_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.mirror_manager = mirror_manager
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumSize(600, 500)
        
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        main_layout = QVBoxLayout(self)
        
        # 创建标签页
        tab_widget = QTabWidget()
        
        # 常规设置标签页
        general_widget = QWidget()
        self.init_general_tab(general_widget)
        tab_widget.addTab(general_widget, "常规")
        
        # 镜像源设置标签页
        mirror_widget = QWidget()
        self.init_mirror_tab(mirror_widget)
        tab_widget.addTab(mirror_widget, "镜像源")
        
        main_layout.addWidget(tab_widget)
        
        # 按钮区
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply,
            Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply_settings)
        
        main_layout.addWidget(buttons)
    
    def init_general_tab(self, parent):
        """初始化常规设置标签页"""
        layout = QVBoxLayout(parent)
        
        # 下载设置组
        download_group = QGroupBox("下载设置")
        download_layout = QGridLayout(download_group)
        
        # 下载目录
        download_layout.addWidget(QLabel("默认下载目录:"), 0, 0)
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setText(self.config_manager.get("common.download_dir"))
        download_layout.addWidget(self.download_dir_edit, 0, 1)
        
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.browse_download_dir)
        download_layout.addWidget(browse_btn, 0, 2)
        
        # 最大线程数
        download_layout.addWidget(QLabel("最大下载线程数:"), 1, 0)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 64)
        self.threads_spin.setValue(self.config_manager.get("common.max_threads", 4))
        download_layout.addWidget(self.threads_spin, 1, 1)
        
        # 默认镜像源
        download_layout.addWidget(QLabel("默认镜像源:"), 2, 0)
        self.mirror_combo = QComboBox()
        self.mirror_combo.addItems(self.mirror_manager.get_mirror_names())
        current_mirror = self.config_manager.get("common.mirror", "GitHub")
        if current_mirror in self.mirror_manager.get_mirror_names():
            self.mirror_combo.setCurrentText(current_mirror)
        download_layout.addWidget(self.mirror_combo, 2, 1)
        
        # 启动时检查更新
        self.check_update_checkbox = QCheckBox("启动时自动检查更新")
        self.check_update_checkbox.setChecked(self.config_manager.get("common.check_update_on_startup", True))
        download_layout.addWidget(self.check_update_checkbox, 3, 0, 1, 2)
        
        layout.addWidget(download_group)
        
        # 窗口设置组
        window_group = QGroupBox("窗口设置")
        window_layout = QGridLayout(window_group)
        
        # 窗口大小
        window_layout.addWidget(QLabel("默认窗口宽度:"), 0, 0)
        self.window_width = QSpinBox()
        self.window_width.setRange(600, 1920)
        self.window_width.setValue(self.config_manager.get("common.window_geometry")[0])
        window_layout.addWidget(self.window_width, 0, 1)
        
        window_layout.addWidget(QLabel("默认窗口高度:"), 1, 0)
        self.window_height = QSpinBox()
        self.window_height.setRange(400, 1080)
        self.window_height.setValue(self.config_manager.get("common.window_geometry")[1])
        window_layout.addWidget(self.window_height, 1, 1)
        
        layout.addWidget(window_group)
        layout.addStretch()
    
    def init_mirror_tab(self, parent):
        """初始化镜像源设置标签页"""
        layout = QVBoxLayout(parent)
        
        # 镜像源列表
        self.mirror_list = QTreeWidget()
        self.mirror_list.setHeaderLabels(["名称", "URL"])
        self.mirror_list.setColumnWidth(0, 150)
        
        # 加载现有镜像源
        mirrors = self.config_manager.get_mirrors()
        for name, url in mirrors.items():
            item = QTreeWidgetItem([name, url])
            self.mirror_list.addTopLevelItem(item)
        
        layout.addWidget(self.mirror_list)
        
        # 操作按钮
        btn_layout = QHBoxLayout()
        
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self.add_mirror)
        btn_layout.addWidget(add_btn)
        
        edit_btn = QPushButton("编辑")
        edit_btn.clicked.connect(self.edit_mirror)
        btn_layout.addWidget(edit_btn)
        
        remove_btn = QPushButton("删除")
        remove_btn.clicked.connect(self.remove_mirror)
        btn_layout.addWidget(remove_btn)
        
        layout.addLayout(btn_layout)
        layout.addStretch()
    
    def browse_download_dir(self):
        """浏览下载目录"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择下载目录", self.download_dir_edit.text()
        )
        if dir_path:
            self.download_dir_edit.setText(dir_path)
    
    def add_mirror(self):
        """添加镜像源"""
        # 创建输入对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("添加镜像源")
        dialog.setModal(True)
        
        layout = QVBoxLayout(dialog)
        
        # 名称输入
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("名称:"))
        name_edit = QLineEdit()
        name_layout.addWidget(name_edit)
        layout.addLayout(name_layout)
        
        # URL输入
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("URL:"))
        url_edit = QLineEdit()
        url_layout.addWidget(url_edit)
        layout.addLayout(url_layout)
        
        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec_() == QDialog.Accepted and name_edit.text().strip():
            name = name_edit.text().strip()
            url = url_edit.text().strip()
            
            if self.mirror_manager.add_mirror(name, url):
                item = QTreeWidgetItem([name, url])
                self.mirror_list.addTopLevelItem(item)
                QMessageBox.information(self, "成功", "镜像源添加成功！")
            else:
                QMessageBox.warning(self, "失败", "镜像源添加失败！")
    
    def edit_mirror(self):
        """编辑镜像源"""
        selected = self.mirror_list.currentItem()
        if not selected:
            QMessageBox.warning(self, "警告", "请选择要编辑的镜像源！")
            return
        
        name = selected.text(0)
        url = selected.text(1)
        
        # 创建输入对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑镜像源")
        dialog.setModal(True)
        
        layout = QVBoxLayout(dialog)
        
        # 名称输入
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("名称:"))
        name_edit = QLineEdit(name)
        name_layout.addWidget(name_edit)
        layout.addLayout(name_layout)
        
        # URL输入
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("URL:"))
        url_edit = QLineEdit(url)
        url_layout.addWidget(url_edit)
        layout.addLayout(url_layout)
        
        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec_() == QDialog.Accepted and name_edit.text().strip():
            new_name = name_edit.text().strip()
            new_url = url_edit.text().strip()
            
            # 先删除旧的
            self.mirror_manager.remove_mirror(name)
            # 添加新的
            if self.mirror_manager.add_mirror(new_name, new_url):
                selected.setText(0, new_name)
                selected.setText(1, new_url)
                QMessageBox.information(self, "成功", "镜像源编辑成功！")
            else:
                # 恢复旧的
                self.mirror_manager.add_mirror(name, url)
                QMessageBox.warning(self, "失败", "镜像源编辑失败！")
    
    def remove_mirror(self):
        """删除镜像源"""
        selected = self.mirror_list.currentItem()
        if not selected:
            QMessageBox.warning(self, "警告", "请选择要删除的镜像源！")
            return
        
        name = selected.text(0)
        
        reply = QMessageBox.question(
            self, "确认", f"确定要删除镜像源「{name}」吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            if self.mirror_manager.remove_mirror(name):
                self.mirror_list.takeTopLevelItem(self.mirror_list.indexOfTopLevelItem(selected))
                QMessageBox.information(self, "成功", "镜像源删除成功！")
            else:
                QMessageBox.warning(self, "失败", "镜像源删除失败！")
    
    def apply_settings(self):
        """应用设置"""
        try:
            # 保存常规设置
            self.config_manager.set("common.download_dir", self.download_dir_edit.text())
            self.config_manager.set("common.max_threads", self.threads_spin.value())
            self.config_manager.set("common.mirror", self.mirror_combo.currentText())
            self.config_manager.set("common.check_update_on_startup", self.check_update_checkbox.isChecked())
            self.config_manager.set("common.window_geometry", [
                self.window_width.value(),
                self.window_height.value()
            ])
            
            QMessageBox.information(self, "成功", "设置已保存！")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"保存设置失败: {str(e)}")
    
    def accept(self):
        """确认设置"""
        self.apply_settings()
        super().accept()

# ===================== 更新日志对话框 =====================
class ReleaseNotesDialog(QDialog):
    """更新日志对话框"""
    def __init__(self, version_manager, tag_name, parent=None):
        super().__init__(parent)
        self.version_manager = version_manager
        self.tag_name = tag_name
        self.setWindowTitle(f"更新日志 - {tag_name}")
        self.setMinimumSize(800, 600)
        
        self.init_ui()
        self.load_release_notes()
    
    def init_ui(self):
        """初始化UI"""
        main_layout = QVBoxLayout(self)
        
        # Markdown渲染文本框
        self.notes_browser = QTextBrowser()
        self.notes_browser.setOpenExternalLinks(True)  # 允许打开外部链接
        self.notes_browser.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 15px;
                font-size: 14px;
                line-height: 1.6;
            }
        """)
        main_layout.addWidget(self.notes_browser)
        
        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #2196F3;
                color: white;
                border: none;
                height: 36px;
                border-radius: 8px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #1976D2;
            }
        """)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)
    
    def load_release_notes(self):
        """加载更新日志"""
        self.notes_browser.setText("正在加载更新日志...")
        
        # 在新线程中加载
        def load_thread():
            try:
                notes = self.version_manager.get_release_notes(self.tag_name)
                # 转换Markdown到HTML
                html = markdown.markdown(
                    notes,
                    extensions=['fenced_code', 'tables', 'nl2br']
                )
                
                # 美化HTML
                styled_html = f"""
                <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        body {{ font-family: Microsoft YaHei; line-height: 1.6; }}
                        h1, h2, h3 {{ color: #2196F3; }}
                        a {{ color: #2196F3; text-decoration: none; }}
                        a:hover {{ text-decoration: underline; }}
                        pre {{ 
                            background: #f5f5f5; 
                            padding: 10px; 
                            border-radius: 4px;
                            overflow-x: auto;
                        }}
                        code {{ 
                            background: #f5f5f5; 
                            padding: 2px 4px; 
                            border-radius: 4px;
                        }}
                        table {{ 
                            border-collapse: collapse; 
                            width: 100%; 
                            margin: 10px 0;
                        }}
                        th, td {{ 
                            border: 1px solid #ddd; 
                            padding: 8px; 
                            text-align: left;
                        }}
                        th {{ 
                            background-color: #f2f2f2; 
                        }}
                    </style>
                </head>
                <body>
                    {html}
                </body>
                </html>
                """
                
                # 在主线程中更新UI
                QTimer.singleShot(0, lambda: self.notes_browser.setHtml(styled_html))
            except Exception as e:
                error_html = f"<p style='color: red;'>加载更新日志失败: {str(e)}</p>"
                QTimer.singleShot(0, lambda: self.notes_browser.setHtml(error_html))
        
        thread = threading.Thread(target=load_thread)
        thread.daemon = True
        thread.start()

# ===================== 自定义事件类 =====================
EVENT_VERSIONS_LOADED = QEvent.registerEventType()
EVENT_ERROR = QEvent.registerEventType()
EVENT_UPDATE_AVAILABLE = QEvent.registerEventType()
EVENT_NO_UPDATE = QEvent.registerEventType()

class CustomEvent(QEvent):
    """自定义事件类"""
    def __init__(self, event_type, data=None):
        super().__init__(event_type)
        self.data = data

# ===================== 主窗口类 =====================
class MainWindow(QMainWindow):
    """主窗口类"""
    def __init__(self):
        super().__init__()
        
        # 初始化配置管理器
        self.config_manager = ConfigManager()
        
        # 初始化镜像管理器
        self.mirror_manager = MirrorManager(self.config_manager)
        
        # 初始化版本管理器
        self.version_manager = VersionManager(self.mirror_manager, self.config_manager)
        
        # 下载相关
        self.download_worker = None
        self.current_version = None
        self.save_path = ""
        
        # 初始化UI
        self.init_ui()
        
        # 加载版本列表
        self.load_versions()
        
        # 检查更新
        if self.config_manager.get("common.check_update_on_startup", True):
            self.check_for_updates()
    
    def init_ui(self):
        """初始化UI"""
        # 设置窗口大小和位置
        width, height = self.config_manager.get("common.window_geometry", [900, 600])
        pos_x, pos_y = self.config_manager.get("common.window_position", [100, 100])
        self.setGeometry(pos_x, pos_y, width, height)
        
        self.setWindowTitle("Minecraft整合包下载器 - EndlessPixel")
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建顶部工具栏
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(10)
        
        # 刷新按钮
        self.refresh_button = QPushButton("刷新版本列表")
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_button.clicked.connect(self.load_versions)
        self.refresh_button.setMinimumHeight(36)
        toolbar_layout.addWidget(self.refresh_button)
        
        # 设置按钮
        settings_btn = QPushButton("设置")
        settings_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        settings_btn.clicked.connect(self.open_settings)
        settings_btn.setMinimumHeight(36)
        toolbar_layout.addWidget(settings_btn)
        
        # 分隔符
        toolbar_layout.addStretch()
        
        # 镜像源选择
        mirror_label = QLabel("镜像源:")
        mirror_label.setStyleSheet("font-weight: 500;")
        toolbar_layout.addWidget(mirror_label)
        
        self.mirror_combo = QComboBox()
        self.mirror_combo.addItems(self.mirror_manager.get_mirror_names())
        current_mirror = self.config_manager.get("common.mirror", "GitHub")
        if current_mirror in self.mirror_manager.get_mirror_names():
            self.mirror_combo.setCurrentText(current_mirror)
        self.mirror_combo.currentTextChanged.connect(self.on_mirror_changed)
        self.mirror_combo.setMinimumWidth(150)
        self.mirror_combo.setMinimumHeight(36)
        toolbar_layout.addWidget(self.mirror_combo)
        
        # 创建垂直分割器
        vertical_splitter = QSplitter(Qt.Vertical)
        
        # 添加工具栏到垂直分割器
        vertical_splitter.addWidget(toolbar_widget)
        
        # 创建水平分割器
        horizontal_splitter = QSplitter(Qt.Horizontal)
        
        # ========== 左侧版本列表 ==========
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(5)
        
        # 版本列表
        self.version_tree = QTreeWidget()
        self.version_tree.setHeaderLabel("版本列表")
        self.version_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.version_tree.customContextMenuRequested.connect(self.show_context_menu)
        self.version_tree.itemDoubleClicked.connect(self.on_version_double_clicked)
        self.version_tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                background: white;
                padding: 5px;
            }
            QTreeWidget::item {
                height: 30px;
                padding-left: 5px;
            }
            QTreeWidget::item:hover {
                background: #e3f2fd;
            }
            QTreeWidget::item:selected {
                background: #2196F3;
                color: white;
            }
        """)
        left_layout.addWidget(self.version_tree)
        
        # 加载动画
        self.loading_label = QLabel("加载中...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setVisible(False)
        self.loading_label.setStyleSheet("font-size: 14px; color: #2196F3; padding: 20px;")
        left_layout.addWidget(self.loading_label)
        
        horizontal_splitter.addWidget(left_widget)
        
        # ========== 右侧下载区域 ==========
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(10)
        
        # 下载信息卡片
        info_card = QWidget()
        info_card.setStyleSheet("""
            QWidget {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        info_layout = QVBoxLayout(info_card)
        
        # 下载信息标签
        self.download_info = QLabel("请选择一个版本进行下载")
        self.download_info.setAlignment(Qt.AlignCenter)
        self.download_info.setStyleSheet("""
            QLabel {
                font-size: 14px;
                padding: 20px;
                color: #666;
            }
        """)
        info_layout.addWidget(self.download_info)
        
        # 查看更新日志按钮
        self.notes_button = QPushButton("查看更新日志")
        self.notes_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogInfoView))
        self.notes_button.clicked.connect(self.view_release_notes)
        self.notes_button.setEnabled(False)
        self.notes_button.setMinimumHeight(36)
        self.notes_button.setStyleSheet("""
            QPushButton {
                background: #f5f5f5;
                color: #2196F3;
                border: 1px solid #2196F3;
                border-radius: 8px;
                padding: 0 15px;
            }
            QPushButton:hover {
                background: #e3f2fd;
            }
            QPushButton:disabled {
                background: #f0f0f0;
                color: #999;
                border: 1px solid #ddd;
            }
        """)
        info_layout.addWidget(self.notes_button)
        
        right_layout.addWidget(info_card)
        
        # 下载进度区域
        progress_widget = QWidget()
        progress_widget.setStyleSheet("""
            QWidget {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        progress_layout = QVBoxLayout(progress_widget)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                height: 24px;
                border-radius: 12px;
                background: #f0f0f0;
                text-align: center;
                font-size: 12px;
            }
            QProgressBar::chunk {
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #64B5F6, stop:1 #2196F3);
            }
        """)
        progress_layout.addWidget(self.progress_bar)
        
        # 速度显示
        self.speed_label = QLabel("")
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setVisible(False)
        self.speed_label.setStyleSheet("font-size: 12px; color: #666; margin-top: 5px;")
        progress_layout.addWidget(self.speed_label)
        
        right_layout.addWidget(progress_widget)
        
        # ========== 修复：创建按钮容器Widget ==========
        control_widget = QWidget()
        control_layout = QHBoxLayout(control_widget)
        control_layout.setSpacing(10)
        
        self.select_path_button = QPushButton("选择保存路径")
        self.select_path_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.select_path_button.clicked.connect(self.select_save_path)
        self.select_path_button.setEnabled(False)
        self.select_path_button.setMinimumHeight(40)
        control_layout.addWidget(self.select_path_button)
        
        self.download_button = QPushButton("开始下载")
        self.download_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.download_button.clicked.connect(self.start_download)
        self.download_button.setEnabled(False)
        self.download_button.setMinimumHeight(40)
        control_layout.addWidget(self.download_button)
        
        self.pause_button = QPushButton("暂停")
        self.pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.pause_button.clicked.connect(self.pause_resume_download)
        self.pause_button.setEnabled(False)
        self.pause_button.setMinimumHeight(40)
        control_layout.addWidget(self.pause_button)
        
        # 添加按钮容器到右侧布局
        right_layout.addWidget(control_widget)
        right_layout.addStretch()
        
        horizontal_splitter.addWidget(right_widget)
        
        # 设置分割器比例
        horizontal_splitter.setSizes([400, 500])
        horizontal_splitter.setStyleSheet("QSplitter::handle { background: #e0e0e0; }")
        
        # 添加水平分割器到垂直分割器
        vertical_splitter.addWidget(horizontal_splitter)
        vertical_splitter.setStyleSheet("QSplitter::handle { background: #e0e0e0; }")
        
        # 添加垂直分割器到主布局
        main_layout.addWidget(vertical_splitter)
        
        # 状态栏
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")
        
        # 应用全局样式
        self.setStyleSheet("""
            /* 全局样式 */
            * {
                font-family: Microsoft YaHei;
            }
            
            QMainWindow {
                background: #fafafa;
            }
            
            /* 工具栏样式 */
            QWidget#toolbar {
                background: white;
                border-bottom: 1px solid #e0e0e0;
            }
            
            /* 按钮通用样式 */
            QPushButton {
                background: #2196F3;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 15px;
                font-size: 13px;
            }
            
            QPushButton:hover {
                background: #1976D2;
            }
            
            QPushButton:pressed {
                background: #0D47A1;
            }
            
            QPushButton:disabled {
                background: #cfd8dc;
                color: #90a4ae;
            }
            
            /* 下拉框样式 */
            QComboBox {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 0 10px;
                background: white;
            }
            
            QComboBox::drop-down {
                border: none;
            }
            
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #666;
                margin-right: 5px;
            }
        """)
    
    def open_settings(self):
        """打开设置对话框"""
        dialog = ConfigDialog(self.config_manager, self.mirror_manager, self)
        if dialog.exec_() == QDialog.Accepted:
            # 更新镜像源下拉框
            current_mirror = self.mirror_combo.currentText()
            self.mirror_combo.clear()
            self.mirror_combo.addItems(self.mirror_manager.get_mirror_names())
            
            # 恢复选中状态
            if current_mirror in self.mirror_manager.get_mirror_names():
                self.mirror_combo.setCurrentText(current_mirror)
            else:
                self.mirror_combo.setCurrentText(self.config_manager.get("common.mirror", "GitHub"))
            
            # 重新加载版本列表
            self.load_versions()
    
    def view_release_notes(self):
        """查看更新日志"""
        if hasattr(self, 'current_version') and self.current_version:
            dialog = ReleaseNotesDialog(self.version_manager, self.current_version['tag_name'], self)
            dialog.exec_()
    
    def check_for_updates(self):
        """检查应用程序更新"""
        self.status_bar.showMessage("正在检查更新...")
        update_thread = threading.Thread(target=self._update_check_thread)
        update_thread.start()
    
    def _update_check_thread(self):
        """在后台线程中检查更新"""
        try:
            response = requests.get(UPDATE_CHECK_URL, verify=False, timeout=10)
            response.raise_for_status()
            latest_release = response.json()
            
            latest_version = latest_release.get('tag_name', '').lstrip('v')
            
            if latest_version and self._is_newer_version(latest_version, APP_VERSION):
                QApplication.postEvent(self, CustomEvent(EVENT_UPDATE_AVAILABLE, {
                    'latest_version': latest_version,
                    'download_url': latest_release.get('html_url', '')
                }))
            else:
                QApplication.postEvent(self, CustomEvent(EVENT_NO_UPDATE, None))
        except Exception as e:
            print(f"检查更新失败: {str(e)}")
            QApplication.postEvent(self, CustomEvent(EVENT_NO_UPDATE, None))
    
    def _is_newer_version(self, latest, current):
        """比较版本号"""
        try:
            latest_parts = list(map(int, re.findall(r'\d+', latest)))
            current_parts = list(map(int, re.findall(r'\d+', current)))
            return latest_parts > current_parts
        except:
            return True
    
    def load_versions(self):
        """加载版本列表"""
        self.status_bar.showMessage("正在获取版本列表...")
        self.refresh_button.setEnabled(False)
        self.version_tree.clear()
        self.loading_label.setVisible(True)
        
        self.load_thread = threading.Thread(target=self._load_versions_thread)
        self.load_thread.start()
    
    def _load_versions_thread(self):
        """在新线程中加载版本列表"""
        try:
            mc_versions = self.version_manager.get_versions()
            QApplication.postEvent(self, CustomEvent(EVENT_VERSIONS_LOADED, mc_versions))
        except Exception as e:
            QApplication.postEvent(self, CustomEvent(EVENT_ERROR, str(e)))
    
    def update_version_tree(self, mc_versions):
        """更新版本树"""
        sorted_versions = sorted(
            mc_versions.keys(), 
            key=lambda v: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', v)], 
            reverse=True
        )
        
        for mc_version in sorted_versions:
            mc_item = QTreeWidgetItem([f"Minecraft {mc_version}"])
            mc_item.setExpanded(True)
            self.version_tree.addTopLevelItem(mc_item)
            
            for version in mc_versions[mc_version]:
                tag_name = version['tag_name']
                file_size = self.format_size(version['file_size'])
                published_at = version['published_at'].split('T')[0] if version['published_at'] else ''
                
                if version['is_prerelease']:
                    version_text = f"{tag_name} [测试版] - {file_size} ({published_at})"
                    version_item = QTreeWidgetItem([version_text])
                    version_item.setForeground(0, QColor(255, 165, 0))
                else:
                    version_text = f"{tag_name} [正式版] - {file_size} ({published_at})"
                    version_item = QTreeWidgetItem([version_text])
                    version_item.setForeground(0, QColor(0, 128, 0))
                
                version_item.setData(0, Qt.UserRole, version)
                mc_item.addChild(version_item)
        
        self.status_bar.showMessage(f"已加载 {len(mc_versions)} 个Minecraft版本的整合包")
        self.refresh_button.setEnabled(True)
        self.loading_label.setVisible(False)
    
    def show_context_menu(self, position):
        """显示右键菜单"""
        item = self.version_tree.itemAt(position)
        if not item or not item.parent():
            return
        
        menu = QMenu()
        
        download_action = QAction("下载", self)
        download_action.triggered.connect(lambda: self.on_version_double_clicked(item, 0))
        menu.addAction(download_action)
        
        copy_action = QAction("复制下载链接", self)
        copy_action.triggered.connect(lambda: self.copy_download_url(item))
        menu.addAction(copy_action)
        
        notes_action = QAction("查看更新日志", self)
        notes_action.triggered.connect(lambda: self.view_notes_from_menu(item))
        menu.addAction(notes_action)
        
        menu.exec_(self.version_tree.mapToGlobal(position))
    
    def view_notes_from_menu(self, item):
        """从右键菜单查看更新日志"""
        version = item.data(0, Qt.UserRole)
        if version:
            dialog = ReleaseNotesDialog(self.version_manager, version['tag_name'], self)
            dialog.exec_()
    
    def on_version_double_clicked(self, item, column):
        """双击版本项"""
        if not item.parent():
            return
        
        self.current_version = item.data(0, Qt.UserRole)
        if not self.current_version:
            return
        
        # 更新下载信息
        file_name = self.current_version['file_name']
        file_size = self.format_size(self.current_version['file_size'])
        self.download_info.setText(
            f"<b>选择的版本:</b> {self.current_version['tag_name']}<br>"
            f"<b>文件名:</b> {file_name}<br>"
            f"<b>文件大小:</b> {file_size}<br>"
            f"<b>发布时间:</b> {self.current_version['published_at'].split('T')[0] if self.current_version['published_at'] else '未知'}"
        )
        
        # 启用按钮
        self.download_button.setEnabled(True)
        self.select_path_button.setEnabled(True)
        self.notes_button.setEnabled(True)
        
        # 设置默认保存路径
        default_dir = self.config_manager.get("common.download_dir")
        os.makedirs(default_dir, exist_ok=True)
        self.save_path = os.path.join(default_dir, file_name)
    
    def select_save_path(self):
        """选择保存路径"""
        if not hasattr(self, 'current_version'):
            return
        
        file_name = self.current_version['file_name']
        path, _ = QFileDialog.getSaveFileName(
            self, "保存文件", self.save_path, 
            "整合包文件 (*.zip *.mrpack)"
        )
        
        if path:
            self.save_path = path
    
    def start_download(self):
        """开始下载"""
        if not hasattr(self, 'current_version') or not hasattr(self, 'save_path'):
            return
        
        # 检查文件是否已存在
        if os.path.exists(self.save_path):
            reply = QMessageBox.question(
                self, "文件已存在", "文件已存在，是否覆盖？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        
        # 获取下载链接
        original_url = self.current_version['download_url']
        download_url = self.mirror_manager.get_mirror_url(original_url, self.mirror_combo.currentText())
        
        # 获取线程数
        max_threads = self.config_manager.get("common.max_threads", 4)
        
        # 创建下载线程
        self.download_worker = DownloadWorker(download_url, self.save_path, max_threads)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        self.download_worker.speed.connect(self.update_speed)
        
        # 更新UI
        self.download_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("暂停")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.speed_label.setVisible(True)
        self.speed_label.setText("0 B/s")
        self.status_bar.showMessage(f"正在下载 {self.current_version['tag_name']}...")
        
        # 开始下载
        self.download_worker.start()
    
    def pause_resume_download(self):
        """暂停/恢复下载"""
        if not self.download_worker or not self.download_worker.isRunning():
            return
        
        if self.pause_button.text() == "暂停":
            self.download_worker.pause()
            self.pause_button.setText("恢复")
            self.status_bar.showMessage("下载已暂停")
        else:
            self.download_worker.resume()
            self.pause_button.setText("暂停")
            self.status_bar.showMessage(f"恢复下载 {self.current_version['tag_name']}...")
    
    def update_progress(self, value):
        """更新下载进度"""
        self.progress_bar.setValue(value)
        self.status_bar.showMessage(f"正在下载 {self.current_version['tag_name']}... {value}%")
    
    def update_speed(self, speed):
        """更新下载速度"""
        self.speed_label.setText(f"速度: {speed}")
    
    def download_finished(self, path):
        """下载完成"""
        self.progress_bar.setValue(100)
        self.speed_label.setText("下载完成")
        self.download_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.status_bar.showMessage(f"下载完成: {path}")
        
        reply = QMessageBox.question(
            self, "下载完成", 
            f"文件已下载到:\n{path}\n\n是否打开文件夹？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.open_file_folder(path)
    
    def download_error(self, error_msg):
        """下载错误"""
        self.status_bar.showMessage(f"下载错误: {error_msg}")
        QMessageBox.critical(self, "下载错误", f"下载失败:\n{error_msg}")
        self.download_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.speed_label.setText(f"错误: {error_msg[:30]}...")
    
    def copy_download_url(self, item):
        """复制下载链接"""
        version = item.data(0, Qt.UserRole)
        if not version:
            return
        
        url = version['download_url']
        clipboard = QApplication.clipboard()
        clipboard.setText(url)
        
        self.status_bar.showMessage("下载链接已复制到剪贴板")
    
    def on_mirror_changed(self, mirror):
        """镜像源改变"""
        self.version_manager.set_mirror(mirror)
        self.status_bar.showMessage(f"已切换到 {mirror} 镜像源")
    
    def event(self, event):
        """处理自定义事件"""
        if event.type() == EVENT_VERSIONS_LOADED:
            self.update_version_tree(event.data)
            return True
        elif event.type() == EVENT_ERROR:
            self.status_bar.showMessage(f"错误: {event.data}")
            QMessageBox.critical(self, "错误", f"加载版本列表失败:\n{event.data}")
            self.refresh_button.setEnabled(True)
            self.loading_label.setVisible(False)
            return True
        elif event.type() == EVENT_UPDATE_AVAILABLE:
            update_info = event.data
            latest_version = update_info.get('latest_version')
            download_url = update_info.get('download_url')
            
            msg_box = QMessageBox()
            msg_box.setWindowTitle("发现新版本")
            msg_box.setText(
                f"发现新版本: {latest_version}\n"
                f"当前版本: {APP_VERSION}\n\n"
                f"是否前往下载页面?"
            )
            msg_box.setIcon(QMessageBox.Information)
            msg_box.addButton(QMessageBox.Yes)
            msg_box.addButton(QMessageBox.No)
            
            if msg_box.exec_() == QMessageBox.Yes:
                import webbrowser
                webbrowser.open(download_url)
            
            self.status_bar.showMessage("更新检查完成")
            return True
        elif event.type() == EVENT_NO_UPDATE:
            self.status_bar.showMessage("已是最新版本")
            return True
        
        return super().event(event)
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 保存窗口位置和大小
        self.config_manager.set("common.window_position", [self.x(), self.y()])
        self.config_manager.set("common.window_geometry", [self.width(), self.height()])
        
        # 如果正在下载，提示用户
        if self.download_worker and self.download_worker.isRunning():
            reply = QMessageBox.question(
                self, "确认退出", 
                "当前有文件正在下载，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.download_worker.stop()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
    
    @staticmethod
    def format_size(size_bytes):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"
    
    @staticmethod
    def open_file_folder(path):
        """打开文件所在文件夹"""
        try:
            folder = os.path.dirname(path)
            if sys.platform == 'win32':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as e:
            QMessageBox.warning(None, "警告", f"无法打开文件夹: {str(e)}")

# ===================== 主函数 =====================
def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()