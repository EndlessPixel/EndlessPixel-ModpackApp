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
import requests
from urllib.parse import urljoin
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem, 
                            QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                            QProgressBar, QFileDialog, QComboBox, QMenu, QAction,
                            QMessageBox, QSplitter, QStyleFactory, QStyle)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QEvent
from PyQt5.QtGui import QIcon, QFont, QColor

# 应用程序版本号
APP_VERSION = "1.0"
# GitHub更新检查URL
UPDATE_CHECK_URL = "https://api.github.com/repos/EndlessPixel/EndlessPixel-ModpackApp/releases/latest"

# 设置中文字体支持
QApplication.setFont(QFont("SimHei"))

class DownloadWorker(QThread):
    """下载线程类，负责多线程下载文件"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, url, save_path, threads=4):
        super().__init__()
        self.url = url
        self.save_path = save_path
        self.threads = threads
        self.chunk_queue = queue.Queue()
        self.running = True
        self.total_size = 0
        self.downloaded_size = 0
        self.lock = threading.Lock()
        
    def run(self):
        try:
            # 获取文件大小
            response = requests.head(self.url, allow_redirects=True, verify=False)
            response.raise_for_status()
            self.total_size = int(response.headers.get('content-length', 0))
            
            if self.total_size == 0:
                self.error.emit("无法获取文件大小")
                return
            
            # 创建保存文件
            with open(self.save_path, 'wb') as f:
                f.write(b'')
            
            # 计算每个线程下载的块大小
            chunk_size = self.total_size // self.threads
            
            # 创建并启动下载线程
            threads = []
            for i in range(self.threads):
                start = i * chunk_size
                end = self.total_size - 1 if i == self.threads - 1 else (i + 1) * chunk_size - 1
                thread = threading.Thread(target=self.download_chunk, args=(start, end))
                threads.append(thread)
                thread.start()
            
            # 等待所有线程完成
            for thread in threads:
                thread.join()
            
            if self.running:
                self.finished.emit(self.save_path)
        except Exception as e:
            self.error.emit(str(e))
    
    def download_chunk(self, start, end):
        """下载文件的一个块"""
        headers = {'Range': f'bytes={start}-{end}'}
        
        try:
            response = requests.get(self.url, headers=headers, stream=True, allow_redirects=True, verify=False)
            response.raise_for_status()
            
            chunk_size = 8192
            downloaded = 0
            
            # 写入文件的指定位置
            with open(self.save_path, 'r+b') as f:
                f.seek(start)
                for chunk in response.iter_content(chunk_size=chunk_size):
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
    
    def stop(self):
        """停止下载"""
        self.running = False
        self.wait()

class MirrorManager:
    """镜像源管理器"""
    def __init__(self):
        self.mirrors = [
            { 'tag': 'GitHub', 'url': '', 'tip': '官方源' },
            { 'tag': 'Cloudflare', 'url': 'https://gh-proxy.org/', 'tip': '推荐' },
            { 'tag': 'Fastly', 'url': 'https://cdn.gh-proxy.org/', 'tip': '推荐' },
            { 'tag': 'Edgeone', 'url': 'https://edgeone.gh-proxy.org/', 'tip': '推荐' },
            { 'tag': 'Jasonzeng', 'url': 'https://gh.xmly.dev/', 'tip': '大文件慎用' },
            { 'tag': 'Imixc', 'url': 'https://gh.imixc.top/', 'tip': '大文件慎用' },
            { 'tag': '香港', 'url': 'https://hk.gh-proxy.org/', 'tip': '香港节点' },
        ]
    
    def get_mirror_url(self, original_url, mirror_tag):
        """根据镜像标签获取镜像URL"""
        for mirror in self.mirrors:
            if mirror['tag'] == mirror_tag:
                if mirror['url']:
                    return urljoin(mirror['url'], original_url)
                else:
                    return original_url
        return original_url
    
    def get_mirror_tags(self):
        """获取所有镜像标签"""
        return [mirror['tag'] for mirror in self.mirrors]
    
    def get_mirror_tip(self, mirror_tag):
        """获取镜像提示信息"""
        for mirror in self.mirrors:
            if mirror['tag'] == mirror_tag:
                return mirror['tip']
        return ''

class VersionManager:
    """版本管理器，负责获取和解析版本信息"""
    GITHUB_API_URL = "https://api.github.com/repos/EndlessPixel/EndlessPixel-Modpack/releases"
    
    def __init__(self):
        self.mirror_manager = MirrorManager()
        self.current_mirror = "GitHub"
    
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
                response = requests.get(url, verify=False)
                response.raise_for_status()
                
                # 添加当前页面的版本信息
                all_releases.extend(response.json())
                
                # 解析Link头字段，获取下一页的URL
                url = None
                if 'Link' in response.headers:
                    link_header = response.headers['Link']
                    # 查找rel="next"的链接
                    import re
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
                assets = release['assets']
                
                # 跳过没有附件的版本
                if not assets:
                    continue
                
                # 查找符合格式的文件：支持正式版(.zip)、测试版(bx.zip)和旧版本(.mrpack)
                matched_asset = None
                import re
                # 支持多种格式：EndlessPixel.x.xx.x-vx-x.x.zip, EndlessPixel.x.xx.x-vx-bx.zip, EndlessPixel.x.xx.x-vx-x.x.mrpack
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
                
                # 判断是否为预发布版
                is_prerelease = release['prerelease']
                
                # 获取下载链接和文件名
                download_url = matched_asset['browser_download_url']
                file_name = matched_asset['name']
                file_size = matched_asset['size']
                
                # 添加到对应MC版本的列表中
                if mc_version not in mc_versions:
                    mc_versions[mc_version] = []
                
                mc_versions[mc_version].append({
                    'tag_name': tag_name,
                    'download_url': download_url,
                    'file_name': file_name,
                    'file_size': file_size,
                    'is_prerelease': is_prerelease,
                    'published_at': release['published_at']
                })
            
            return mc_versions
        except Exception as e:
            print(f"获取版本列表失败: {str(e)}")
            return {}
    
    def extract_mc_version(self, tag_name):
        """从标签名中提取MC版本"""
        # 尝试从标签名中提取MC版本
        # 格式可能是: 1.21.11-v10-1.6 或 v10-1.6
        match = re.match(r'(\d+\.\d+(?:\.\d+)?)-', tag_name)
        if match:
            return match.group(1)
        
        # 如果没有找到，尝试从文件名中提取
        return None
    
    def parse_version(self, version_str):
        """解析版本号，用于排序"""
        # 提取预发布后缀
        pre_release_match = re.search(r'-([a-zA-Z]+.*)$', version_str)
        pre_release = pre_release_match.group(1) if pre_release_match else ''
        
        # 提取主版本号部分
        main_version_str = version_str.replace(f"-{pre_release}", "") if pre_release else version_str
        main_parts = re.findall(r'\d+', main_version_str)
        
        # 转换为数字列表
        main_parts = [int(part) for part in main_parts]
        
        # 预发布版本的优先级低于正式版
        # 正式版: 100, 预发布版: 50
        pre_release_priority = 50 if pre_release else 100
        
        # 提取预发布后缀中的数字
        pre_release_num = 0
        if pre_release:
            pre_match = re.search(r'\d+', pre_release)
            if pre_match:
                pre_release_num = int(pre_match.group(0))
        
        # 返回排序键
        return (main_parts, pre_release_priority, pre_release_num)

class MainWindow(QMainWindow):
    """主窗口类"""
    def __init__(self):
        super().__init__()
        self.version_manager = VersionManager()
        self.mirror_manager = MirrorManager()
        self.download_worker = None
        self.current_download = None
        
        self.init_ui()
        self.load_versions()
        # 检查更新
        self.check_for_updates()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("Minecraft整合包下载器 - EndlessPixel")
        self.setGeometry(100, 100, 900, 600)
        
        # 设置窗口图标
        # 使用Qt内置图标作为窗口图标
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建主布局
        main_layout = QVBoxLayout(central_widget)
        
        # 创建顶部工具栏
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(5, 5, 5, 5)
        toolbar_layout.setSpacing(8)
        
        # 刷新按钮
        self.refresh_button = QPushButton("刷新版本列表")
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_button.clicked.connect(self.load_versions)
        toolbar_layout.addWidget(self.refresh_button)
        
        # 镜像源选择
        toolbar_layout.addStretch()
        mirror_label = QLabel("镜像源:")
        mirror_label.setStyleSheet("font-weight: 500;")
        toolbar_layout.addWidget(mirror_label)
        self.mirror_combo = QComboBox()
        self.mirror_combo.addItems(self.mirror_manager.get_mirror_tags())
        self.mirror_combo.currentTextChanged.connect(self.on_mirror_changed)
        self.mirror_combo.setMinimumWidth(150)
        toolbar_layout.addWidget(self.mirror_combo)
        
        # 创建垂直分割器，用于调节上栏（工具栏）和下栏（主内容）的大小
        vertical_splitter = QSplitter(Qt.Vertical)
        
        # 添加工具栏到垂直分割器
        vertical_splitter.addWidget(toolbar_widget)
        
        # 创建水平分割器（包含左侧版本列表和右侧下载区域）
        horizontal_splitter = QSplitter(Qt.Horizontal)
        
        # 创建左侧版本列表和加载动画容器
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        
        # 版本列表
        self.version_tree = QTreeWidget()
        self.version_tree.setHeaderLabel("版本列表")
        self.version_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.version_tree.customContextMenuRequested.connect(self.show_context_menu)
        self.version_tree.itemDoubleClicked.connect(self.on_version_double_clicked)
        left_layout.addWidget(self.version_tree)
        
        # 加载动画
        self.loading_label = QLabel()
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setVisible(False)
        # 创建一个简单的加载文本，实际项目中可以使用GIF动画
        self.loading_label.setText("加载中...")
        self.loading_label.setStyleSheet("font-size: 14px; color: #1976d2;")
        left_layout.addWidget(self.loading_label)
        
        horizontal_splitter.addWidget(left_widget)
        
        # 创建右侧下载区域
        download_widget = QWidget()
        download_widget.setStyleSheet("background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 4px; margin: 5px;")
        download_layout = QVBoxLayout(download_widget)
        download_layout.setContentsMargins(15, 15, 15, 15)
        download_layout.setSpacing(15)
        
        # 下载信息
        self.download_info = QLabel("请选择一个版本进行下载")
        self.download_info.setAlignment(Qt.AlignCenter)
        self.download_info.setStyleSheet("font-size: 14px; padding: 20px; background-color: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 4px;")
        download_layout.addWidget(self.download_info)
        
        # 下载进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        download_layout.addWidget(self.progress_bar)
        
        # 下载控制按钮
        control_layout = QHBoxLayout()
        control_layout.setSpacing(10)
        
        self.select_path_button = QPushButton("选择保存路径")
        self.select_path_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.select_path_button.clicked.connect(self.select_save_path)
        control_layout.addWidget(self.select_path_button)
        
        self.download_button = QPushButton("开始下载")
        self.download_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.download_button.clicked.connect(self.start_download)
        self.download_button.setEnabled(False)
        control_layout.addWidget(self.download_button)
        
        self.pause_button = QPushButton("暂停")
        self.pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.pause_button.clicked.connect(self.pause_download)
        self.pause_button.setEnabled(False)
        control_layout.addWidget(self.pause_button)
        
        download_layout.addLayout(control_layout)
        
        # 添加下载区域到水平分割器
        horizontal_splitter.addWidget(download_widget)
        
        # 设置水平分割器比例
        horizontal_splitter.setSizes([400, 500])
        
        # 添加水平分割器到垂直分割器
        vertical_splitter.addWidget(horizontal_splitter)
        
        # 设置垂直分割器默认比例为2:8
        total_height = 600  # 窗口默认高度
        toolbar_height = int(total_height * 0.2)
        content_height = int(total_height * 0.8)
        vertical_splitter.setSizes([toolbar_height, content_height])
        
        # 添加垂直分割器到主布局
        main_layout.addWidget(vertical_splitter)
        
        # 状态栏
        self.statusBar().showMessage("就绪")
    
    def check_for_updates(self):
        """检查应用程序更新"""
        self.statusBar().showMessage("正在检查更新...")
        # 在新线程中检查更新，避免阻塞主线程
        update_thread = threading.Thread(target=self._update_check_thread)
        update_thread.start()
    
    def _update_check_thread(self):
        """在后台线程中检查更新"""
        try:
            # 发送请求获取最新版本信息
            response = requests.get(UPDATE_CHECK_URL, verify=False)
            response.raise_for_status()
            latest_release = response.json()
            
            # 解析最新版本号
            latest_version = latest_release.get('tag_name', '').lstrip('v')
            
            if latest_version:
                # 比较版本号
                if self._is_newer_version(latest_version, APP_VERSION):
                    # 在主线程中显示更新提示
                    QApplication.postEvent(self, CustomEvent(EVENT_UPDATE_AVAILABLE, {
                        'latest_version': latest_version,
                        'download_url': latest_release.get('html_url', '')
                    }))
                else:
                    # 已是最新版本
                    QApplication.postEvent(self, CustomEvent(EVENT_NO_UPDATE, None))
            else:
                # 无法获取版本信息
                QApplication.postEvent(self, CustomEvent(EVENT_ERROR, "无法获取最新版本信息"))
        except Exception as e:
            # 网络错误或其他异常
            print(f"检查更新失败: {str(e)}")
            # 不显示错误，避免影响用户体验
            QApplication.postEvent(self, CustomEvent(EVENT_NO_UPDATE, None))
    
    def _is_newer_version(self, latest, current):
        """比较版本号，判断是否有更新"""
        try:
            # 分割版本号为数字列表
            latest_parts = list(map(int, re.findall(r'\d+', latest)))
            current_parts = list(map(int, re.findall(r'\d+', current)))
            
            # 比较版本号
            return latest_parts > current_parts
        except:
            # 版本号格式错误，默认认为有更新
            return True
    
    def load_versions(self):
        """加载版本列表"""
        self.statusBar().showMessage("正在获取版本列表...")
        self.refresh_button.setEnabled(False)
        
        # 清空树并显示加载动画
        self.version_tree.clear()
        self.loading_label.setVisible(True)
        
        # 在新线程中获取版本列表
        self.load_thread = threading.Thread(target=self._load_versions_thread)
        self.load_thread.start()
    
    def _load_versions_thread(self):
        """在新线程中加载版本列表"""
        try:
            mc_versions = self.version_manager.get_versions()
            
            # 在主线程中更新UI
            QApplication.postEvent(self, CustomEvent(EVENT_VERSIONS_LOADED, mc_versions))
        except Exception as e:
            QApplication.postEvent(self, CustomEvent(EVENT_ERROR, str(e)))
    
    def update_version_tree(self, mc_versions):
        """更新版本树"""
        # 按MC版本排序（从高到低）
        sorted_versions = sorted(mc_versions.keys(), key=lambda v: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', v)], reverse=True)
        
        for mc_version in sorted_versions:
            # 创建MC版本节点
            mc_item = QTreeWidgetItem([f"Minecraft {mc_version}"])
            mc_item.setExpanded(True)
            self.version_tree.addTopLevelItem(mc_item)
            
            # 添加该MC版本下的所有整合包版本
            for version in mc_versions[mc_version]:
                tag_name = version['tag_name']
                file_name = version['file_name']
                file_size = self.format_size(version['file_size'])
                published_at = version['published_at'].split('T')[0]  # 只显示日期
                
                # 判断版本类型并设置图标
                if version['is_prerelease']:
                    version_text = f"{tag_name} [P] - {file_size} ({published_at})"
                    version_item = QTreeWidgetItem([version_text])
                    version_item.setForeground(0, QColor(255, 165, 0))  # 橙色
                else:
                    version_text = f"{tag_name} [R] - {file_size} ({published_at})"
                    version_item = QTreeWidgetItem([version_text])
                    version_item.setForeground(0, QColor(0, 128, 0))  # 绿色
                
                # 存储版本信息
                version_item.setData(0, Qt.UserRole, version)
                
                mc_item.addChild(version_item)
        
        self.statusBar().showMessage(f"已加载 {len(mc_versions)} 个Minecraft版本的整合包")
        self.refresh_button.setEnabled(True)
        # 隐藏加载动画
        self.loading_label.setVisible(False)
    
    def show_context_menu(self, position):
        """显示右键菜单"""
        item = self.version_tree.itemAt(position)
        if not item or not item.parent():  # 只对版本项显示菜单
            return
        
        menu = QMenu()
        
        # 下载菜单项
        download_action = QAction("下载", self)
        download_action.triggered.connect(lambda: self.on_version_double_clicked(item, 0))
        menu.addAction(download_action)
        
        # 复制下载链接菜单项
        copy_action = QAction("复制下载链接", self)
        copy_action.triggered.connect(lambda: self.copy_download_url(item))
        menu.addAction(copy_action)
        
        menu.exec_(self.version_tree.mapToGlobal(position))
    
    def on_version_double_clicked(self, item, column):
        """双击版本项"""
        if not item.parent():  # 忽略MC版本节点
            return
        
        # 获取版本信息
        version = item.data(0, Qt.UserRole)
        if not version:
            return
        
        # 更新下载信息
        self.current_version = version
        file_name = version['file_name']
        file_size = self.format_size(version['file_size'])
        self.download_info.setText(f"选择的版本: {version['tag_name']}\n文件名: {file_name}\n文件大小: {file_size}")
        
        # 启用下载按钮
        self.download_button.setEnabled(True)
        self.select_path_button.setEnabled(True)
        
        # 设置默认保存路径
        self.save_path = os.path.join(os.path.expanduser("~"), "Downloads", file_name)
    
    def select_save_path(self):
        """选择保存路径"""
        if not hasattr(self, 'current_version'):
            return
        
        file_name = self.current_version['file_name']
        path, _ = QFileDialog.getSaveFileName(self, "保存文件", os.path.join(os.path.expanduser("~"), "Downloads", file_name), "ZIP文件 (*.zip)")
        
        if path:
            self.save_path = path
    
    def start_download(self):
        """开始下载"""
        if not hasattr(self, 'current_version') or not hasattr(self, 'save_path'):
            return
        
        # 检查文件是否已存在
        if os.path.exists(self.save_path):
            reply = QMessageBox.question(self, "文件已存在", "文件已存在，是否覆盖？", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                return
        
        # 获取下载链接（使用当前选择的镜像）
        original_url = self.current_version['download_url']
        download_url = self.mirror_manager.get_mirror_url(original_url, self.mirror_combo.currentText())
        
        # 创建下载线程
        self.download_worker = DownloadWorker(download_url, self.save_path)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        
        # 更新UI
        self.download_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.statusBar().showMessage(f"正在下载 {self.current_version['tag_name']}...")
        
        # 开始下载
        self.download_worker.start()
    
    def pause_download(self):
        """暂停下载"""
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.stop()
            self.download_button.setEnabled(True)
            self.pause_button.setEnabled(False)
            self.statusBar().showMessage("下载已暂停")
    
    def update_progress(self, value):
        """更新下载进度"""
        self.progress_bar.setValue(value)
        self.statusBar().showMessage(f"正在下载 {self.current_version['tag_name']}... {value}%")
    
    def download_finished(self, path):
        """下载完成"""
        self.progress_bar.setValue(100)
        self.download_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.statusBar().showMessage(f"下载完成: {path}")
        
        # 显示完成消息
        reply = QMessageBox.question(self, "下载完成", f"文件已下载到:\n{path}\n\n是否打开文件夹？", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            # 打开文件夹
            if sys.platform == 'win32':
                os.startfile(os.path.dirname(path))
            elif sys.platform == 'darwin':
                os.system(f'open "{os.path.dirname(path)}"')
            else:
                os.system(f'xdg-open "{os.path.dirname(path)}"')
    
    def download_error(self, error_msg):
        """下载错误"""
        self.statusBar().showMessage(f"下载错误: {error_msg}")
        QMessageBox.critical(self, "下载错误", f"下载失败:\n{error_msg}")
        self.download_button.setEnabled(True)
        self.pause_button.setEnabled(False)
    
    def copy_download_url(self, item):
        """复制下载链接"""
        version = item.data(0, Qt.UserRole)
        if not version:
            return
        
        # 获取下载链接
        url = version['download_url']
        
        # 复制到剪贴板
        clipboard = QApplication.clipboard()
        clipboard.setText(url)
        
        self.statusBar().showMessage("下载链接已复制到剪贴板")
    
    def on_mirror_changed(self, mirror):
        """镜像源改变"""
        self.version_manager.set_mirror(mirror)
        tip = self.mirror_manager.get_mirror_tip(mirror)
        if tip:
            self.statusBar().showMessage(f"已切换到 {mirror} 镜像源 ({tip})")
        else:
            self.statusBar().showMessage(f"已切换到 {mirror} 镜像源")
    
    def event(self, event):
        """处理自定义事件"""
        if event.type() == EVENT_VERSIONS_LOADED:
            self.update_version_tree(event.data)
            return True
        elif event.type() == EVENT_ERROR:
            self.statusBar().showMessage(f"错误: {event.data}")
            QMessageBox.critical(self, "错误", f"加载版本列表失败:\n{event.data}")
            self.refresh_button.setEnabled(True)
            # 隐藏加载动画
            self.loading_label.setVisible(False)
            return True
        elif event.type() == EVENT_UPDATE_AVAILABLE:
            # 显示更新提示
            update_info = event.data
            latest_version = update_info.get('latest_version')
            download_url = update_info.get('download_url')
            
            msg_box = QMessageBox()
            msg_box.setWindowTitle("发现新版本")
            msg_box.setText(f"发现新版本: {latest_version}\n当前版本: {APP_VERSION}\n\n是否前往下载页面?")
            msg_box.setIcon(QMessageBox.Information)
            msg_box.addButton(QMessageBox.Yes)
            msg_box.addButton(QMessageBox.No)
            
            if msg_box.exec_() == QMessageBox.Yes:
                # 打开下载页面
                import webbrowser
                webbrowser.open(download_url)
            
            self.statusBar().showMessage("更新检查完成")
            return True
        elif event.type() == EVENT_NO_UPDATE:
            # 已是最新版本
            self.statusBar().showMessage("已是最新版本")
            return True
        
        return super().event(event)
    
    @staticmethod
    def format_size(size_bytes):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"

# 自定义事件类型
EVENT_VERSIONS_LOADED = QEvent.registerEventType()
EVENT_ERROR = QEvent.registerEventType()
EVENT_UPDATE_AVAILABLE = QEvent.registerEventType()
EVENT_NO_UPDATE = QEvent.registerEventType()

class CustomEvent(QEvent):
    """自定义事件类"""
    def __init__(self, event_type, data=None):
        super().__init__(event_type)
        self.data = data

def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    
    # 设置全局样式
    app.setStyleSheet("""
        /* 主窗口背景 */
        QMainWindow {
            background-color: #f0f0f0;
        }
        
        /* 版本列表树 */
        QTreeWidget {
            font-size: 14px;
            background-color: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            margin: 5px;
        }
        
        QTreeWidget::item {
            padding: 4px;
            border-bottom: 1px solid #f0f0f0;
        }
        
        QTreeWidget::item:hover {
            background-color: #f5f5f5;
        }
        
        QTreeWidget::item:selected {
            background-color: #e3f2fd;
            color: #1976d2;
        }
        
        /* 按钮样式 */
        QPushButton {
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 500;
            background-color: #1976d2;
            color: white;
            border: none;
            border-radius: 4px;
        }
        
        QPushButton:hover {
            background-color: #1565c0;
        }
        
        QPushButton:pressed {
            background-color: #0d47a1;
        }
        
        QPushButton:disabled {
            background-color: #bdbdbd;
            color: #757575;
        }
        
        /* 标签样式 */
        QLabel {
            font-size: 14px;
            color: #333333;
        }
        
        /* 下拉框样式 */
        QComboBox {
            padding: 5px 10px;
            font-size: 13px;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            background-color: #ffffff;
        }
        
        QComboBox:hover {
            border-color: #1976d2;
        }
        
        /* 进度条样式 */
        QProgressBar {
            height: 24px;
            font-size: 13px;
            background-color: #e0e0e0;
            border-radius: 12px;
            text-align: center;
        }
        
        QProgressBar::chunk {
            background-color: #4caf50;
            border-radius: 12px;
        }
        
        /* 下载信息区域 */
        QLabel#download_info {
            font-size: 14px;
            padding: 20px;
            background-color: #f9f9f9;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            margin: 5px;
        }
        
        /* 工具栏 */
        QWidget#toolbar {
            background-color: #ffffff;
            border-bottom: 1px solid #e0e0e0;
            padding: 8px;
        }
    """)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()