#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
译来翻译器 - 图形界面版本
支持系统托盘、快捷键唤醒、图片OCR识别
"""

import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from translator import YoudaoTranslator, YoudaoOCR, load_config
import fcntl
import subprocess
import base64
import io

# 单实例锁文件路径
LOCK_FILE = "/tmp/yilai_translator.lock"
# D-Bus 信号文件路径（用于系统快捷键触发）
SHOW_SIGNAL_FILE = "/tmp/yilai_translator_show.signal"
# 划词翻译信号文件路径
SELECTION_TRANSLATE_SIGNAL_FILE = "/tmp/yilai_translator_selection.signal"

# 尝试导入系统托盘库
try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ImageTk 用于图片预览，单独导入（可能失败但不影响托盘）
try:
    from PIL import ImageTk
    IMAGETK_AVAILABLE = True
except ImportError:
    IMAGETK_AVAILABLE = False


def check_single_instance():
    """检查是否已有实例运行，返回锁文件句柄或None"""
    try:
        lock_file = open(LOCK_FILE, 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except (IOError, OSError):
        return None


def send_show_signal():
    """发送显示窗口信号（用于外部快捷键触发）"""
    try:
        with open(SHOW_SIGNAL_FILE, 'w') as f:
            f.write('show')
    except:
        pass


def send_selection_translate_signal():
    """发送划词翻译信号"""
    try:
        with open(SELECTION_TRANSLATE_SIGNAL_FILE, 'w') as f:
            f.write('translate')
    except:
        pass


def get_selected_text() -> str:
    """获取当前选中的文本

    Wayland 会话下优先用 wl-paste 读 primary selection（X11 桥接通常拿不到
    Wayland 应用的选区），失败再回退到 xclip。
    """
    for cmd in (['wl-paste', '-p', '-n'], ['xclip', '-selection', 'primary', '-o']):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
    return ""


def get_image_from_clipboard() -> bytes:
    """
    从剪贴板获取图片数据
    支持多种格式：PNG、BMP等
    """
    # 尝试使用 xclip 获取图片
    try:
        # 首先尝试 PNG 格式
        result = subprocess.run(
            ['xclip', '-selection', 'clipboard', '-t', 'image/png', '-o'],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    
    # 尝试 BMP 格式
    try:
        result = subprocess.run(
            ['xclip', '-selection', 'clipboard', '-t', 'image/bmp', '-o'],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    
    # 尝试通用 image 格式
    try:
        result = subprocess.run(
            ['xclip', '-selection', 'clipboard', '-t', 'image', '-o'],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    
    return None


class TranslatorApp:
    """翻译器主应用类"""

    # 配色与字体（现代深色风格）
    COLORS = {
        "bg": "#1e1e24",
        "surface": "#26262e",
        "field": "#17171c",
        "fg": "#e4e4e7",
        "muted": "#9aa0aa",
        "accent": "#7aa2f7",
        "border": "#33333c",
    }
    FONT_FAMILY = "Noto Sans CJK SC"

    # 状态文件路径
    STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
    
    def __init__(self):
        self.config = load_config()
        self.translator = None
        self.ocr = None
        self.root = None
        self.tray_icon = None
        self.is_visible = True
        self.lock_file = None  # 保持锁文件引用
        
        # 动态翻译相关
        self.auto_translate_delay = 500  # 停止输入后多少毫秒自动翻译
        self._after_id = None  # 用于取消延迟任务
        
        # 信号监听相关
        self._signal_check_id = None
        
        # 当前识别的图片（用于显示预览）
        self._current_image = None
        self._current_image_data = None
        
        # 加载保存的状态（必须在划词翻译初始化之前）
        self.saved_state = self._load_state()
        
        # 划词翻译相关（从保存的状态加载）
        self.selection_translate_enabled = self.saved_state.get("selection_translate_enabled", False)
        
        # 检查配置
        if self._check_config():
            self.translator = YoudaoTranslator(
                self.config["app_key"],
                self.config["app_secret"]
            )
            self.ocr = YoudaoOCR(
                self.config["app_key"],
                self.config["app_secret"]
            )
        
        # 创建主窗口
        self._create_window()
        
        # 初始化系统托盘
        if TRAY_AVAILABLE:
            self._init_tray()
        else:
            print("提示: 安装 pystray 和 Pillow 库可启用系统托盘功能")
            print("  pip install pystray Pillow")
        
        # 启动信号监听（用于系统快捷键触发）
        self._start_signal_listener()
        print("提示: 可在系统设置中配置快捷键来唤醒窗口")
        print(f"  命令: python3 {os.path.abspath(__file__)} --show")
    
    def _start_signal_listener(self):
        """启动信号文件监听，用于系统快捷键触发"""
        self._last_signal_time = 0
        self._check_signal()
    
    def _check_signal(self):
        """检查是否有显示窗口的信号"""
        try:
            if os.path.exists(SHOW_SIGNAL_FILE):
                # 读取并删除信号文件
                with open(SHOW_SIGNAL_FILE, 'r') as f:
                    content = f.read().strip()
                
                # 删除信号文件
                os.remove(SHOW_SIGNAL_FILE)
                
                if content == 'show':
                    self._do_show_window()  # 直接调用实际操作
        except Exception as e:
            print(f"检查显示信号出错: {e}")
        
        # 检查划词翻译信号
        try:
            if os.path.exists(SELECTION_TRANSLATE_SIGNAL_FILE):
                # 删除信号文件
                os.remove(SELECTION_TRANSLATE_SIGNAL_FILE)
                
                # 执行划词翻译
                self._do_selection_translate()
        except Exception as e:
            print(f"检查划词翻译信号出错: {e}")
        
        # 每200ms检查一次
        self._signal_check_id = self.root.after(200, self._check_signal)
    
    def _load_state(self) -> dict:
        """加载保存的状态"""
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def _save_state(self):
        """保存窗口状态"""
        try:
            state = {
                "window_width": self.root.winfo_width(),
                "window_height": self.root.winfo_height(),
                "window_x": self.root.winfo_x(),
                "window_y": self.root.winfo_y(),
                "selection_translate_enabled": self.selection_translate_enabled,
            }
            with open(self.STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass
    
    def _check_config(self) -> bool:
        """检查配置是否有效"""
        app_key = self.config.get("app_key", "")
        app_secret = self.config.get("app_secret", "")
        
        if not app_key or app_key == "请在这里填写你的appKey":
            messagebox.showwarning(
                "配置提醒",
                "请先在 config.json 中填写你的 appKey 和 app_secret"
            )
            return False
        
        if not app_secret or app_secret == "请在这里填写你的应用密钥":
            messagebox.showwarning(
                "配置提醒",
                "请先在 config.json 中填写你的 app_secret"
            )
            return False
        
        return True
    
    def _create_window(self):
        """创建主窗口"""
        # className 决定 X11 的 WM_CLASS，Wayland 下据此匹配窗口规则
        self.root = tk.Tk(className="yilai-translator")
        
        # 创建窗口后立即初始化 BooleanVar（必须在创建 root 之后）
        self.auto_translate = tk.BooleanVar(value=False)
        self.root.title("译来翻译器")
        
        # 设置窗口大小和位置（优先使用保存的状态）
        # 小窗浮层：默认紧凑尺寸
        width = self.saved_state.get("window_width", self.config.get("window_width", 660))
        height = self.saved_state.get("window_height", self.config.get("window_height", 480))
        x = self.saved_state.get("window_x")
        y = self.saved_state.get("window_y")
        
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(540, 380)
        
        # 浮在其他窗口之上
        self.root.attributes("-topmost", True)
        
        # 如果有保存的位置，使用保存的位置，否则居中
        if x is not None and y is not None:
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            self.root.update_idletasks()
            x = (self.root.winfo_screenwidth() - width) // 2
            y = (self.root.winfo_screenheight() - height) // 2
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        
        # 绑定窗口大小变化事件
        self.root.bind("<Configure>", self._on_window_configure)
        
        # 设置关闭行为：最小化到托盘
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Esc 关闭窗口（隐藏到托盘，不退出程序）
        self.root.bind("<Escape>", self._on_escape)
        
        # 先设样式，再创建界面组件
        self._set_style()
        self._create_widgets()
    
    def _on_window_configure(self, event):
        """窗口大小/位置变化时保存状态（延迟保存）"""
        if hasattr(self, '_save_state_after_id'):
            self.root.after_cancel(self._save_state_after_id)
        self._save_state_after_id = self.root.after(500, self._save_state)
    
    def _create_widgets(self):
        """创建界面组件 - 现代紧凑单列布局"""
        # 本轮改成单列布局，不再使用图片预览画布（保留属性兼容旧逻辑）
        self.image_canvas = None

        c = self.COLORS
        F = self.FONT_FAMILY
        self.root.configure(bg=c["bg"])

        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        content = ttk.Frame(root, padding=(16, 14, 16, 10), style="App.TFrame")
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)   # 原文
        content.rowconfigure(5, weight=1)   # 译文

        # ===== 语言栏 =====
        lang_bar = ttk.Frame(content, style="App.TFrame")
        lang_bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        self.from_lang_var = tk.StringVar(value="自动检测")
        self.from_lang_combo = ttk.Combobox(
            lang_bar,
            textvariable=self.from_lang_var,
            values=list(YoudaoTranslator.LANGUAGES.keys()),
            state="readonly",
            width=11,
            font=(F, 10),
        )
        self.from_lang_combo.pack(side=tk.LEFT)

        self.swap_btn = ttk.Button(
            lang_bar, text="⇄", width=3,
            command=self._swap_languages, style="Icon.TButton"
        )
        self.swap_btn.pack(side=tk.LEFT, padx=6)

        default_to = self.config.get("default_to", "zh-CHS")
        default_to_name = "中文简体"
        for name, code in YoudaoTranslator.LANGUAGES.items():
            if code == default_to:
                default_to_name = name
                break
        self.to_lang_var = tk.StringVar(value=default_to_name)
        self.to_lang_combo = ttk.Combobox(
            lang_bar,
            textvariable=self.to_lang_var,
            values=list(YoudaoTranslator.LANGUAGES.keys()),
            state="readonly",
            width=11,
            font=(F, 10),
        )
        self.to_lang_combo.pack(side=tk.LEFT)

        self.auto_translate_check = ttk.Checkbutton(
            lang_bar, text="动态翻译",
            variable=self.auto_translate,
            command=self._on_auto_translate_toggle,
            style="Toolbutton",
        )
        self.auto_translate_check.pack(side=tk.RIGHT)

        # ===== 原文 =====
        ttk.Label(content, text="原文", style="Caption.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 4)
        )

        self.input_text = self._make_text_area(content, row=2, readonly=False)

        self.input_hint = tk.Label(
            self.input_text.master,
            text="在这里输入或粘贴文字，回车翻译",
            bg=self.COLORS["field"], fg=self.COLORS["muted"],
            font=(F, 10), anchor="nw",
        )
        self.input_hint.bind("<Button-1>", lambda e: self.input_text.focus_set())

        # ===== 工具条 =====
        toolbar = ttk.Frame(content, style="App.TFrame")
        toolbar.grid(row=3, column=0, sticky="ew", pady=10)

        self.translate_btn = ttk.Button(
            toolbar, text="翻译", width=9,
            command=self._translate, style="Accent.TButton"
        )
        self.translate_btn.pack(side=tk.LEFT)
        self.paste_btn = ttk.Button(
            toolbar, text="粘贴文本", width=9, command=self._paste_text
        )
        self.paste_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.ocr_btn = ttk.Button(
            toolbar, text="图片 OCR", width=9, command=self._paste_image
        )
        self.ocr_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.clear_btn = ttk.Button(
            toolbar, text="清空", width=6, command=self._clear
        )
        self.clear_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.ocr_lang_var = tk.StringVar(value="自动识别")
        self.ocr_lang_combo = ttk.Combobox(
            toolbar,
            textvariable=self.ocr_lang_var,
            values=list(YoudaoOCR.OCR_LANGUAGES.keys()),
            state="readonly",
            width=8,
            font=(F, 9),
        )
        self.ocr_lang_combo.pack(side=tk.RIGHT)
        ttk.Label(toolbar, text="OCR 语言", style="Caption.TLabel").pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        # ===== 译文 =====
        ttk.Label(content, text="译文", style="Caption.TLabel").grid(
            row=4, column=0, sticky="w", pady=(0, 4)
        )

        self.output_text = self._make_text_area(content, row=5, readonly=True)

        self.output_hint = tk.Label(
            self.output_text.master,
            text="译文会显示在这里",
            bg=self.COLORS["field"], fg=self.COLORS["muted"],
            font=(F, 10), anchor="nw",
        )

        # ===== 状态栏 =====
        status_bar = ttk.Frame(content, style="App.TFrame")
        status_bar.grid(row=6, column=0, sticky="ew", pady=(10, 0))

        self.status_var = tk.StringVar(value="选中文字后按 Ctrl+\\ 划词翻译，Esc 收起")
        self.status_label = ttk.Label(
            status_bar, textvariable=self.status_var, style="Muted.TLabel"
        )
        self.status_label.pack(side=tk.LEFT)

        self.copy_btn = ttk.Button(
            status_bar, text="复制译文", width=9, command=self._copy_result
        )
        self.copy_btn.pack(side=tk.RIGHT)

        # ===== 快捷键 =====
        self.input_text.bind("<Return>", self._on_enter_press)
        self.input_text.bind("<Shift-Return>", self._on_shift_enter_press)
        self.input_text.bind("<Shift-BackSpace>", self._on_shift_backspace)
        self.input_text.bind("<KeyRelease>", self._on_text_change)
        self.input_text.bind("<KeyRelease>", self._update_hints, add="+")
        # Ctrl+V：剪贴板有图片就 OCR，否则粘贴文本
        self.input_text.bind("<Control-v>", self._smart_paste)
        self.input_text.bind("<Control-V>", self._smart_paste)

        self._update_hints()

    def _make_text_area(self, parent, row: int, readonly: bool = False, height: int = 6) -> tk.Text:
        """创建一个带 1px 边框和滚动条的文本区"""
        c = self.COLORS
        F = self.FONT_FAMILY
        wrap = tk.Frame(
            parent, bg=c["field"],
            highlightthickness=1,
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
        )
        wrap.grid(row=row, column=0, sticky="nsew")

        text = tk.Text(
            wrap,
            wrap=tk.WORD,
            font=(F, 11),
            height=height,
            bg=c["field"],
            fg=c["fg"],
            insertbackground=c["accent"],
            selectbackground=c["accent"],
            selectforeground="#101014",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            undo=not readonly,
        )
        scrollbar = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        if readonly:
            text.configure(state=tk.DISABLED)

        # 聚焦时边框变强调色
        text.bind("<FocusIn>", lambda e: wrap.configure(highlightbackground=c["accent"]))
        text.bind("<FocusOut>", lambda e: wrap.configure(highlightbackground=c["border"]))
        return text

    def _update_hints(self, event=None):
        """空白时显示占位提示"""
        for text_widget, hint in (
            (getattr(self, "input_text", None), getattr(self, "input_hint", None)),
            (getattr(self, "output_text", None), getattr(self, "output_hint", None)),
        ):
            if text_widget is None or hint is None:
                continue
            try:
                empty = not text_widget.get("1.0", "end-1c").strip()
            except Exception:
                empty = False
            if empty:
                hint.place(x=12, y=10)
            else:
                hint.place_forget()

    def _smart_paste(self, event=None):
        """Ctrl+V：剪贴板有图就 OCR，否则当文本粘贴"""
        image_data = get_image_from_clipboard()
        if image_data:
            self._set_image(image_data)
            self.root.after(100, self._do_ocr)
        else:
            self._paste_text()
        return "break"

    def _paste_image(self, event=None):
        """从剪贴板粘贴图片并自动OCR识别"""
        image_data = get_image_from_clipboard()
        
        if image_data:
            self._set_image(image_data)
            # 自动开始OCR识别
            self.root.after(100, self._do_ocr)
        else:
            self.status_var.set("剪贴板中没有图片")
    
    def _select_image_file(self):
        """选择图片文件并自动OCR识别"""
        file_path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("所有文件", "*.*")
            ]
        )
        
        if file_path:
            try:
                with open(file_path, 'rb') as f:
                    image_data = f.read()
                self._set_image(image_data)
                # 自动开始OCR识别
                self.root.after(100, self._do_ocr)
            except Exception as e:
                self.status_var.set(f"加载图片失败: {str(e)}")
    
    def _on_image_canvas_resize(self, event):
        """图片Canvas大小变化时重新显示图片（当前布局无画布，保留兼容）"""
        if self.image_canvas is None:
            return
        if self._current_image_data and IMAGETK_AVAILABLE:
            self._display_image_on_canvas()
    
    def _set_image(self, image_data: bytes):
        """设置当前图片并显示预览"""
        self._current_image_data = image_data
        
        try:
            # 保存原始PIL图片（单列布局下不再显示预览）
            self._pil_image = Image.open(io.BytesIO(image_data))
        except Exception:
            self._current_image = None
            self._pil_image = None
            self.status_var.set("图片加载失败")
    
    def _display_image_on_canvas(self):
        """在Canvas上显示图片（自适应大小；当前布局无画布则跳过）"""
        if self.image_canvas is None:
            return
        if not hasattr(self, '_pil_image') or self._pil_image is None:
            return
        
        # 获取Canvas的实际大小
        canvas_width = self.image_canvas.winfo_width()
        canvas_height = self.image_canvas.winfo_height()
        
        if canvas_width < 10 or canvas_height < 10:
            return
        
        pil_image = self._pil_image
        
        # 计算缩放比例，保持宽高比，填充整个Canvas
        ratio = min(canvas_width / pil_image.width, canvas_height / pil_image.height)
        new_size = (int(pil_image.width * ratio), int(pil_image.height * ratio))
        
        if ratio != 1:
            resized_image = pil_image.resize(new_size, Image.Resampling.LANCZOS)
        else:
            resized_image = pil_image
        
        # 转换为tkinter可用的格式
        self._current_image = ImageTk.PhotoImage(resized_image)
        
        # 清空Canvas并居中显示图片
        self.image_canvas.delete("all")
        x = canvas_width // 2
        y = canvas_height // 2
        self.image_canvas.create_image(x, y, image=self._current_image, anchor="center")
    
    def _do_ocr(self):
        """执行OCR识别"""
        if not self.ocr:
            messagebox.showerror("错误", "请先配置 appKey 和 app_secret")
            return
        
        if not self._current_image_data:
            self.status_var.set("请先粘贴或选择图片")
            return
        
        # 获取OCR语言
        ocr_lang = YoudaoOCR.OCR_LANGUAGES.get(self.ocr_lang_var.get(), "auto")
        
        self.status_var.set("OCR识别中...")
        
        def do_ocr_thread():
            result = self.ocr.recognize(self._current_image_data, ocr_lang)
            self.root.after(0, lambda: self._on_ocr_complete(result))
        
        threading.Thread(target=do_ocr_thread, daemon=True).start()
    
    def _on_ocr_complete(self, result: dict):
        """OCR完成回调"""
        if result["success"]:
            # 将识别结果填入输入框
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert(tk.END, result["text"])
            self._update_hints()
            self.status_var.set(f"OCR识别完成，识别到 {len(result['text'])} 个字符")
        else:
            self.status_var.set(f"OCR识别失败: {result['error']}")
    
    def _on_enter_press(self, event):
        """Enter 键翻译"""
        self._translate()
        return "break"  # 阻止默认换行行为
    
    def _on_shift_enter_press(self, event):
        """Shift+Enter 换行"""
        # 允许默认换行行为
        return None
    
    def _on_shift_backspace(self, event):
        """Shift+Backspace 清空"""
        self._clear()
        return "break"  # 阻止默认删除行为
    
    def _on_auto_translate_toggle(self):
        """动态翻译开关切换"""
        if self.auto_translate.get():
            self.status_var.set("动态翻译已开启")
        else:
            self.status_var.set("动态翻译已关闭")
            # 取消待执行的翻译任务
            if self._after_id:
                self.root.after_cancel(self._after_id)
                self._after_id = None
    
    def _on_text_change(self, event):
        """文本变化时触发动态翻译"""
        if not self.auto_translate.get():
            return
        
        # 取消之前的延迟任务
        if self._after_id:
            self.root.after_cancel(self._after_id)
        
        # 设置新的延迟任务
        self._after_id = self.root.after(self.auto_translate_delay, self._delayed_translate)
    
    def _delayed_translate(self):
        """延迟翻译（动态翻译用）"""
        self._after_id = None
        text = self.input_text.get("1.0", tk.END).strip()
        if text:
            self._translate()
    
    def _set_style(self):
        """深色现代风格（clam 主题）"""
        c = self.COLORS
        F = self.FONT_FAMILY
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=c["bg"], foreground=c["fg"], font=(F, 10))
        style.configure("App.TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"], font=(F, 10))
        style.configure("Caption.TLabel", background=c["bg"], foreground=c["muted"], font=(F, 9))
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=(F, 9))

        style.configure(
            "TButton", padding=(10, 5), background=c["surface"], foreground=c["fg"],
            borderwidth=0, relief="flat", font=(F, 10),
        )
        style.map(
            "TButton",
            background=[("pressed", c["border"]), ("active", c["border"]), ("disabled", "#20202a")],
            foreground=[("disabled", c["muted"])],
        )

        style.configure(
            "Accent.TButton", background=c["accent"], foreground="#101014",
            font=(F, 10, "bold"), padding=(10, 5), borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#9ab8ff"), ("pressed", "#9ab8ff"), ("disabled", "#3a3a44")],
            foreground=[("disabled", c["muted"])],
        )

        style.configure("Icon.TButton", padding=(6, 5), font=(F, 12))

        style.configure(
            "App.TCheckbutton", background=c["bg"], foreground=c["muted"], font=(F, 9),
            indicatorbackground=c["field"], indicatorforeground=c["accent"],
            upperbordercolor=c["border"], lowerbordercolor=c["border"],
        )
        style.map(
            "App.TCheckbutton",
            background=[("active", c["bg"])],
            foreground=[("selected", c["fg"])],
        )

        style.configure(
            "TCombobox", padding=4, fieldbackground=c["field"], background=c["surface"],
            foreground=c["fg"], bordercolor=c["border"], arrowcolor=c["muted"],
            lightcolor=c["field"], darkcolor=c["field"], font=(F, 10),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["field"])],
            foreground=[("readonly", c["fg"])],
            selectbackground=[("readonly", c["field"])],
            selectforeground=[("readonly", c["fg"])],
        )

        style.configure(
            "Vertical.TScrollbar", background="#4b4b58", troughcolor=c["bg"],
            bordercolor=c["bg"], arrowcolor=c["muted"], relief="flat",
            lightcolor="#4b4b58", darkcolor="#4b4b58", width=7, arrowsize=0,
        )
        style.configure(
            "Toolbutton", padding=(10, 4), background=c["surface"],
            foreground=c["muted"], font=(F, 9), borderwidth=0, relief="flat",
        )
        style.map(
            "Toolbutton",
            background=[("selected", c["accent"]), ("active", c["border"])],
            foreground=[("selected", "#101014"), ("active", c["fg"])],
        )
        style.layout("Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])

        # 下拉列表配色
        self.root.option_add("*TCombobox*Listbox.background", c["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#101014")

    def _swap_languages(self):
        """交换源语言和目标语言"""
        from_lang = self.from_lang_var.get()
        to_lang = self.to_lang_var.get()
        
        # 不能交换"自动检测"
        if from_lang != "自动检测":
            self.from_lang_var.set(to_lang)
            self.to_lang_var.set(from_lang)
    
    def _translate(self):
        """执行翻译"""
        if not self.translator:
            messagebox.showerror("错误", "请先配置 appKey 和 app_secret")
            return
        
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            self.status_var.set("请输入要翻译的文本")
            return
        
        # 获取语言代码
        from_lang = YoudaoTranslator.LANGUAGES.get(self.from_lang_var.get(), "auto")
        to_lang = YoudaoTranslator.LANGUAGES.get(self.to_lang_var.get(), "en")
        
        # 检查是否为相同语言
        if from_lang == to_lang and from_lang != "auto":
            self.status_var.set("源语言和目标语言不能相同")
            return
        
        # 禁用按钮，显示加载状态
        self.translate_btn.config(state=tk.DISABLED)
        self.status_var.set("翻译中...")
        
        # 在线程中执行翻译
        def do_translate():
            result = self.translator.translate(text, from_lang, to_lang)
            
            # 在主线程更新UI
            self.root.after(0, lambda: self._update_result(result))
        
        thread = threading.Thread(target=do_translate, daemon=True)
        thread.start()
    
    def _update_result(self, result: dict):
        """更新翻译结果"""
        self.translate_btn.config(state=tk.NORMAL)
        
        if result["success"]:
            # 先解除只读状态
            self.output_text.config(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            translation = result["translation"]
            self.output_text.insert(tk.END, translation)
            # 滚动到顶部
            self.output_text.see("1.0")
            # 强制更新显示
            self.output_text.update_idletasks()
            # 重新设置为只读
            self.output_text.config(state=tk.DISABLED)
            self._update_hints()
            self.status_var.set("翻译完成 ✓")
        else:
            self.status_var.set(f"错误: {result['error']}")
            messagebox.showerror("翻译失败", result["error"])
    
    def _clear(self):
        """清空文本框和图片"""
        self.input_text.delete("1.0", tk.END)
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.config(state=tk.DISABLED)
        
        # 清空图片状态
        self._current_image = None
        self._current_image_data = None
        self._pil_image = None
        
        self._update_hints()
        self.status_var.set("已清空")
    
    def _paste_text(self):
        """从剪贴板粘贴文本到输入框"""
        try:
            clipboard_text = self.root.clipboard_get()
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert(tk.END, clipboard_text)
            self._update_hints()
            self.status_var.set("已粘贴")
            # 如果开启了动态翻译，会自动触发翻译
        except tk.TclError:
            self.status_var.set("剪贴板为空")
    
    def _do_selection_translate(self):
        """执行划词翻译"""
        if not self.selection_translate_enabled:
            return
        
        # 获取选中的文本
        selected_text = get_selected_text()
        if not selected_text:
            self._do_show_window()
            self.status_var.set("未检测到选中文本")
            return
        
        # 显示窗口
        self._do_show_window()
        
        # 粘贴到输入框
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert(tk.END, selected_text)
        self._update_hints()
        self.status_var.set("已获取选中文本")
        
        # 自动翻译
        self._translate()
    
    def _toggle_selection_translate(self):
        """切换划词翻译功能（线程安全）"""
        try:
            self.root.after(0, self._do_toggle_selection_translate)
        except:
            pass
    
    def _do_toggle_selection_translate(self):
        """实际切换划词翻译的操作"""
        self.selection_translate_enabled = not self.selection_translate_enabled
        # 保存状态
        self._save_state()
        if self.selection_translate_enabled:
            self.status_var.set("划词翻译已开启")
        else:
            self.status_var.set("划词翻译已关闭")
    
    def _copy_result(self):
        """复制翻译结果到剪贴板"""
        result = self.output_text.get("1.0", tk.END).strip()
        if result:
            self.root.clipboard_clear()
            self.root.clipboard_append(result)
            self.status_var.set("已复制到剪贴板")
        else:
            self.status_var.set("没有可复制的内容")
    
    def _on_close(self):
        """窗口关闭事件"""
        if TRAY_AVAILABLE and self.tray_icon:
            # 最小化到托盘
            self.root.withdraw()
            self.is_visible = False
        else:
            self.root.quit()
    
    def _on_escape(self, event=None):
        """Esc：隐藏窗口回到后台（托盘）"""
        self._on_close()
        return "break"
    
    def _show_window(self):
        """显示窗口（线程安全）"""
        # 使用 after 确保在主线程中执行
        try:
            self.root.after(0, self._do_show_window)
        except:
            pass
    
    def _do_show_window(self):
        """实际显示窗口的操作（在主线程中执行）"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        try:
            self.input_text.focus_set()
        except Exception:
            pass
        self.is_visible = True
    
    def _hide_window(self):
        """隐藏窗口（线程安全）"""
        try:
            self.root.after(0, self._do_hide_window)
        except:
            pass
    
    def _do_hide_window(self):
        """实际隐藏窗口的操作（在主线程中执行）"""
        self.root.withdraw()
        self.is_visible = False
    
    def _toggle_window(self):
        """切换窗口显示/隐藏"""
        if self.is_visible:
            self._hide_window()
        else:
            self._show_window()
    
    def _quit_app(self):
        """退出应用"""
        # 保存状态
        self._save_state()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.quit()
    
    def _create_tray_icon(self):
        """创建托盘图标 - 使用系统字典图标"""
        # 托盘图标路径（预先转换好的）
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "tray_icon.png")
        
        if os.path.exists(icon_path):
            try:
                return Image.open(icon_path)
            except:
                pass
        
        # 如果找不到图标，创建一个简单的备选图标
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), color='white')
        dc = ImageDraw.Draw(image)
        dc.rectangle([8, 8, 56, 56], fill='#4CAF50', outline='#2E7D32')
        dc.text((12, 20), "译", fill='white')
        
        return image
    
    def _init_tray(self):
        """初始化系统托盘"""
        icon_image = self._create_tray_icon()
        
        # 创建托盘菜单
        menu_items = [
            item('显示窗口', self._show_window, default=True),
            item('划词翻译', self._toggle_selection_translate, checked=lambda item: self.selection_translate_enabled),
            item('开机自启动', self._toggle_autostart, checked=lambda item: is_autostart_enabled()),
            item('退出', self._quit_app),
        ]
        
        self.tray_icon = pystray.Icon(
            "yilai_translator",
            icon_image,
            "译来翻译器",
            menu_items
        )
        
        # 在后台线程启动托盘
        tray_thread = threading.Thread(
            target=self.tray_icon.run,
            daemon=True
        )
        tray_thread.start()
    
    def _toggle_autostart(self):
        """切换开机自启动（线程安全）"""
        try:
            self.root.after(0, self._do_toggle_autostart)
        except:
            pass
    
    def _do_toggle_autostart(self):
        """实际切换开机自启动的操作"""
        if is_autostart_enabled():
            setup_autostart(False)
            self.status_var.set("已关闭开机自启动")
        else:
            setup_autostart(True)
            self.status_var.set("已启用开机自启动")
    
    def run(self):
        """运行应用"""
        self.root.mainloop()


def setup_autostart(enable: bool = True):
    """设置开机自启动"""
    autostart_dir = os.path.expanduser("~/.config/autostart")
    autostart_file = os.path.join(autostart_dir, "yilai-translator.desktop")
    script_path = os.path.abspath(__file__)
    
    if enable:
        # 创建自启动目录
        os.makedirs(autostart_dir, exist_ok=True)
        
        # 创建desktop文件
        desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=译来翻译器
Comment=译来翻译器 - 开机自启动
Exec=python3 {script_path}
Icon=accessories-dictionary
Terminal=false
Categories=Utility;Office;
StartupNotify=true
"""
        with open(autostart_file, 'w', encoding='utf-8') as f:
            f.write(desktop_content)
        return True
    else:
        # 删除自启动文件
        if os.path.exists(autostart_file):
            os.remove(autostart_file)
        return False


def is_autostart_enabled() -> bool:
    """检查是否已设置开机自启动"""
    autostart_file = os.path.expanduser("~/.config/autostart/yilai-translator.desktop")
    return os.path.exists(autostart_file)


def main():
    """主函数"""
    # 检查单实例
    lock_file = check_single_instance()
    if lock_file is None:
        # 已有实例运行，发送显示信号
        send_show_signal()
        print("程序已在运行中，已发送显示窗口信号")
        sys.exit(0)
    
    app = TranslatorApp()
    app.lock_file = lock_file  # 保持锁文件引用
    try:
        app.run()
    finally:
        # 清理锁文件
        if lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
            try:
                os.remove(LOCK_FILE)
            except:
                pass


if __name__ == "__main__":
    # 命令行参数处理
    if len(sys.argv) > 1:
        if sys.argv[1] == "--show":
            # 发送显示窗口信号
            send_show_signal()
            print("已发送显示窗口信号")
            sys.exit(0)
        elif sys.argv[1] == "--selection-translate":
            # 发送划词翻译信号
            send_selection_translate_signal()
            print("已发送划词翻译信号")
            sys.exit(0)
        elif sys.argv[1] == "--enable-autostart":
            setup_autostart(True)
            print("已启用开机自启动")
            sys.exit(0)
        elif sys.argv[1] == "--disable-autostart":
            setup_autostart(False)
            print("已关闭开机自启动")
            sys.exit(0)
        elif sys.argv[1] == "--check-autostart":
            print("开机自启动:", "已启用" if is_autostart_enabled() else "已关闭")
            sys.exit(0)
    
    main()