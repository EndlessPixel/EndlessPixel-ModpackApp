#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本，用于测试下载器的核心功能
"""

import sys
import os
import json
from version_manager import VersionManager
from mirror_manager import MirrorManager

def test_version_parsing():
    """测试版本解析功能"""
    print("测试版本解析功能...")
    
    vm = VersionManager()
    
    # 测试版本排序
    versions = [
        "v10-1.6",
        "v10-b4",
        "v10-1.5",
        "v9-2.0",
        "1.21.11-v10-1.6",
        "1.21.11-v10-b1",
        "1.20.12-v10-1.0"
    ]
    
    # 按版本号排序
    versions.sort(key=lambda x: vm.parse_version(x), reverse=True)
    
    print("排序后的版本：")
    for version in versions:
        print(f"  {version}")
    
    print("版本解析测试完成！\n")

def test_mirror_url():
    """测试镜像URL生成"""
    print("测试镜像URL生成...")
    
    mm = MirrorManager()
    
    original_url = "https://github.com/EndlessPixel/EndlessPixel-Modpack/releases/download/v10-1.6/EndlessPixel-Modpack-v10-1.6.zip"
    
    print("原始URL:", original_url)
    print("\n各镜像源URL:")
    
    for mirror in mm.get_mirror_tags():
        mirror_url = mm.get_mirror_url(original_url, mirror)
        tip = mm.get_mirror_tip(mirror)
        print(f"  {mirror} ({tip}): {mirror_url}")
    
    print("镜像URL测试完成！\n")

def test_get_versions():
    """测试获取版本列表"""
    print("测试获取版本列表...")
    
    vm = VersionManager()
    vm.set_mirror("GitHub")
    
    try:
        versions = vm.get_versions()
        
        print(f"获取到 {len(versions)} 个Minecraft版本的整合包：")
        
        for mc_version, modpack_versions in versions.items():
            print(f"  Minecraft {mc_version}:")
            for version in modpack_versions[:3]:  # 只显示前3个版本
                tag = version['tag_name']
                prerelease = "[P]" if version['is_prerelease'] else "[R]"
                print(f"    - {tag} {prerelease}")
            
            if len(modpack_versions) > 3:
                print(f"    ... 还有 {len(modpack_versions) - 3} 个版本")
        
        print("获取版本列表测试完成！")
    
    except Exception as e:
        print(f"获取版本列表失败: {str(e)}")

if __name__ == "__main__":
    # 添加当前目录到Python路径
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    
    print("=== Minecraft整合包下载器测试 ===\n")
    
    # 测试镜像URL生成
    test_mirror_url()
    
    # 测试版本解析
    test_version_parsing()
    
    # 测试获取版本列表（可选，需要网络连接）
    if input("\n是否测试获取版本列表？(y/n): ").lower() == 'y':
        test_get_versions()
    
    print("\n=== 测试完成 ===")