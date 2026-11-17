# 译来翻译器（Linux）

基于有道智云 API 的 Linux 桌面翻译器：文本翻译、截图 OCR、划词翻译、系统托盘常驻。

原项目：[linxuan-sys/yilai_transtrator](https://github.com/linxuan-sys/yilai_transtrator)

## 功能

- 文本翻译（有道智云 NMT）
- 图片 OCR：`Ctrl+V` 粘贴截图自动识别，或点击「图片 OCR」选择文件
- 划词翻译：读取系统 primary selection（鼠标选中即复制的那段文字）
- 系统托盘：关闭窗口不退出，可最小化到托盘

## 适用环境

| 项目 | 说明 |
| --- | --- |
| 桌面 | X11 与 Wayland 均可（Wayland 下需要 `wl-clipboard`） |
| 实测环境 | Arch Linux + Python 3.14 + Umbriel（wlroots 系 Wayland 合成器，通过 Xwayland 运行 Tk）+ Noctalia 桌面 shell |
| Python | 3.9 以上；3.14 实测可用 |
| 系统依赖 | `python3`、`tk`、`python-pillow`（含 ImageTk）、`xclip`、`wl-clipboard` |
| Python 依赖 | `pystray`、`requests` |

## 安装

### Arch Linux

```bash
sudo pacman -S python-pip python-pillow tk xclip wl-clipboard
python3 -m venv --system-site-packages .venv
.venv/bin/pip install pystray requests
```

Arch 的系统 Python 受 PEP 668 保护，`pip install` 会被拒绝，所以用 venv；加 `--system-site-packages` 是为了复用系统已装的 Pillow 和 tkinter，避免在 venv 里再编译一遍图形库。

### Fedora / RHEL

```bash
sudo dnf install python3-pip python3-pillow python3-pillow-tk xclip wl-clipboard
pip3 install pystray requests
```

### Ubuntu / Debian

```bash
sudo apt install python3-pip python3-pil python3-pil.imagetk xclip wl-clipboard
pip3 install pystray requests
```

## 配置

在有道智云控制台 <https://ai.youdao.com> 创建应用，勾选「文本翻译」和「通用文字识别(OCR)」，然后把密钥填进 `config.json`：

```json
{
    "app_key": "应用ID",
    "app_secret": "应用密钥",
    "default_from": "auto",
    "default_to": "zh-CHS"
}
```

说明：

- `app_key` / `app_secret` 必填，对应控制台的「应用ID」和「应用密钥」
- `default_to` 生效，缺省 `zh-CHS`
- `default_from` 目前代码里并未读取，源语言固定按 `auto` 处理（保留字段仅为兼容旧配置）
- `window_width` / `window_height` 可选，缺省 660×480；运行时窗口尺寸会记到同目录 `state.json`

## 运行

```bash
.venv/bin/python main.py
```

或把 `main.py` 设为可执行后用系统 Python 运行（依赖需装在系统 Python 里）。

## 快捷键

| 操作 | 说明 |
| --- | --- |
| `Enter` | 翻译输入框内容 |
| `Shift+Enter` | 输入框换行 |
| `Shift+Backspace` | 清空输入框 |
| `Ctrl+V` | 剪贴板里是图片就 OCR，否则粘贴文本 |
| `Esc` | 隐藏窗口到托盘（不退出程序） |

划词翻译**没有内置全局热键**，需要在桌面环境里自建一条快捷键，命令为：

```bash
/path/to/main.py --selection-translate
```

同时托盘菜单里的「划词翻译」开关必须打开。注意：程序需要保持运行，快捷键只是往 `/tmp` 写一个信号文件，由主进程轮询处理。

## 相对上游的改动

这个分支在 `53d892b` 基础上做了三处改动。

### 1. 修复 Wayland 下划词翻译读不到选区

**原因**：`get_selected_text()` 原本固定调用 `xclip -selection primary -o`。Wayland 会话里 Wayland 应用持有的 primary selection 不会桥接给 X11 客户端，实测在 Umbriel 上：

```
wl-paste -p -n                          → 选中文字
xclip -selection primary -o (DISPLAY=:0) → 空，target STRING not available
```

**改动**：优先 `wl-paste -p -n`，失败再回退 `xclip`，X11 与 Wayland 都能用。

### 2. 界面重排为单列浮层布局

**原因**：原布局是一行五列（图片预览 / 按钮 / 输入 / 按钮 / 输出），窗口被压缩时内容全挤在一起；配合小窗浮层使用体验很差。

**改动**：

- 单列布局：语言栏 → 原文 → 工具条 → 译文 → 状态栏
- 深色配色，统一 1px 边框与聚焦高亮，按钮分主次（翻译为强调色）
- 内容区空白时显示占位提示
- `Ctrl+V` 改为智能粘贴：有图片走 OCR，否则粘贴文本
- 窗口默认 660×480，可调，`Esc` 隐藏到托盘后可用 `--show` 唤回
- `tk.Tk(className="yilai-translator")`：让 Wayland 合成器能按 app_id 匹配窗口规则（浮窗、尺寸、位置）

### 3. 移除图片预览画布

**原因**：单列小窗里放不下预览区，且它只是展示用途。

**影响**：OCR 功能完整保留（`Ctrl+V` 或「图片 OCR」按钮仍可用，识别结果直接填入输入框），只是不再显示图片缩略图。

## 兼容性说明

- 未改动有道 API 的调用与签名逻辑，`config.json` 结构保持兼容
- 移除图片预览是界面层面的删减，若需要保留可回退该部分而不影响其它改动
- 若上游不接受这些改动，同样的提交也保留在 fork：<https://github.com/go1em/wayDict>

## 许可

遵循原项目 LICENSE。
