#!/bin/bash
# 安装依赖脚本

echo "开始安装Minecraft整合包下载器依赖..."

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到Python3。请先安装Python 3.6或更高版本。"
    exit 1
fi

# 检查pip是否安装
if ! command -v pip3 &> /dev/null; then
    echo "错误: 未找到pip3。请先安装pip。"
    exit 1
fi

# 安装依赖
echo "正在安装PyQt5和requests..."
pip3 install -r requirements.txt

if [ $? -eq 0 ]; then
    echo "依赖安装成功！"
    echo "现在可以运行 ./main.py 启动程序"
else
    echo "依赖安装失败，请检查错误信息。"
    exit 1
fi