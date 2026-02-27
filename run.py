#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
有道翻译器启动脚本
自动检查和安装依赖
"""

import subprocess
import sys
import os

def check_and_install_dependencies():
    """检查并安装依赖库"""
    required = {
        "requests": "requests",
        "pystray": "pystray",
        "Pillow": "Pillow",
    }
    
    missing = []
    
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"正在安装缺失的依赖: {', '.join(missing)}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + missing
            )
            print("依赖安装完成!")
        except subprocess.CalledProcessError as e:
            print(f"依赖安装失败: {e}")
            print("请手动运行: pip install " + " ".join(missing))
            return False
    
    return True


def main():
    """主函数"""
    # 切换到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    # 检查依赖
    if not check_and_install_dependencies():
        sys.exit(1)
    
    # 启动主程序
    from main import main as run_app
    run_app()


if __name__ == "__main__":
    main()
