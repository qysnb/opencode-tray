# opencode-tray

[English](README.md) · **简体中文**

一个面向 **OpenCode** 后台服务的 Windows 系统托盘指示器。

右键托盘图标即可看到服务是否在工作、重启 / 停止 / 启动服务，并直接跳进任意一个 OpenCode
会话。界面提供 English（默认）与简体中文两种语言，可在 **Language / 语言** 子菜单切换，
选择会记在 `settings.json` 里。

```
OpenCode 服务:运行中 v2.0.32
─────────────────────────────────
会话                              ▸  ● OpenCode Windows 托盘…
─────────────────────────────────
重启服务 / 停止服务 / 启动服务
─────────────────────────────────
语言                              ▸  ○ English
                                     ● 简体中文
─────────────────────────────────
退出
```

## 功能

- **三态图标**——绿色：服务运行中；灰色：未运行；红色：无响应或认证失败。
  悬停可看到版本、PID 与活跃会话数。
- **会话子菜单**——跨**所有项目**列出最近 15 条会话，运行中的排在前面，并显示相对时间。
  点一条即可在新终端窗口里接管该会话。
- **服务生命周期**——重启 / 停止 / 启动，直接在菜单里完成。
- **两种语言**——默认英文，简体中文一键切换。
- **安静地待在托盘里**——单实例（命名互斥体），并可自行安装开机自启与桌面快捷方式。

它是**纯观察者**：绝不会在背后替你启动后台服务，只读取服务自己的注册文件、访问它的本地
HTTP API。只有你主动点菜单，服务状态才会改变。

## 环境要求

- Windows 10/11
- Python 3.9+（开发与验证使用 3.13.5）
- [`pystray`](https://pypi.org/project/pystray/) 与 [`Pillow`](https://pypi.org/project/Pillow/)
  —— 仅有的两个第三方依赖，且只被托盘界面本身使用：
  ```
  pip install pystray Pillow
  ```
- 本机已安装 OpenCode V2（`opencode2` 命令）。脚本会在 `PATH` 上查找，
  再回退到 `%APPDATA%\npm`，并校验主版本号为 2。

## 用法

```
pythonw opencode_tray.pyw                     # 启动托盘(无控制台窗口)
python  opencode_tray.pyw --selftest          # 运行自检,不开窗口、无副作用
python  opencode_tray.pyw --demo 5            # 显示 5 秒后自动退出
python  opencode_tray.pyw --install-startup   # 开机自启(HKCU\...\Run)
python  opencode_tray.pyw --uninstall-startup
python  opencode_tray.pyw --install-shortcut  # 桌面快捷方式
python  opencode_tray.pyw --uninstall-shortcut
```

也可以直接双击 `start-tray.cmd`。它在启动前会先确认 `PATH` 上的 `pythonw` 能 import
`pystray` 和 `Pillow` —— 因为 `pythonw` 没有控制台，缺依赖时会完全静默、什么提示都没有。

创建快捷方式时，图标会被现画成 `opencode_tray.ico`（已 gitignore），
所以仓库里不需要提交二进制图标。

## 文件结构

| 文件 | 作用 |
|---|---|
| `opencode_service.py` | 数据层：服务健康、会话列表、生命周期、启动会话。**仅用标准库。** |
| `opencode_tray.pyw`  | 托盘界面：图标、菜单、轮询、自启、桌面快捷方式。需要 `pystray` + `Pillow`。 |
| `i18n.py`            | 文案表（英文 / 简体中文）与偏好持久化。仅用标准库。 |
| `win_shortcut.py`    | 通过 ctypes 调 COM 创建与读取 Windows `.lnk`。仅用标准库。 |
| `start-tray.cmd`     | 便捷启动器，先检查依赖。 |

这样拆分是有意的：`opencode_service.py`、`i18n.py`、`win_shortcut.py` 都可以脱离显示器
单独测试，它们的 `--selftest` 在没装 `pystray` 的机器上也能跑。

## 验证

```
python  i18n.py             --selftest   # 13 项:含两种语言键集合一致性
python  opencode_service.py --selftest   # 35 项:含真实端到端参数保真
python  opencode_tray.pyw   --selftest   # 44 项:含已渲染 Win32 菜单状态
python  win_shortcut.py     --selftest   # 7 项:.lnk 往返,含非 ASCII
```

这些不是走过场的冒烟测试。它们会**真的启动 cmd.exe**，断言一个刁钻的目录名能逐字节原样
到达；也会用 `GetMenuState` 查**已渲染**的 Win32 菜单，而不是相信永远会重新求值的描述符。

## 关于几个 Windows 坑

有几处看起来奇怪，知道原因就合理了。

- **打开会话会绕一个临时 `.cmd` 文件。** 把目录直接交给 `subprocess` 会被毁掉：
  MSVCRT 的引号层会把字面引号变成参数值的一部分，路径里的 `&` / `^` 还会被 `cmd` 吃掉。
  把命令写进批处理文件，`cmd` 就只按自己的规则解析一次。实测 7 种刁钻目录名，
  这是唯一一种全部保真的写法。
- **优先 `cmd.exe` 而不是 PowerShell。** `subprocess` 配 `shell=True` 让 7 条测试路径全坏；
  PowerShell 的 `Start-Process -ArgumentList` 会把含空格的目录拆成多个参数。
- **会话在 Windows Terminal 里打开**（存在 `wt.exe` 时），并且启动器会设置
  `TERM=xterm-256color`。Windows Terminal **自己不会设 `TERM`**，缺了它 TUI 会走降级渲染。
- **菜单是显式重建的。** `pystray` 在 Windows 上**不会在显示菜单时才生成菜单**——它复用缓存的
  `HMENU`——所以只要相关状态变了就会重建；而菜单**正在显示时绝不重建**：
  那会销毁 Windows 正在跟踪的句柄，导致悬停高亮卡住或不出现。
- **开机自启用注册表而不是「启动」文件夹**，因为部分加固过的机器会直接拒绝
  `.cmd` / `.bat` / `.vbs`。
- **自启项与快捷方式会显式固定解释器**（用装它时的那个 Python 旁边的 `pythonw`），
  因为多 Python 机器上 `shutil.which` 可能选到另一个。

## 新增语言

在 `i18n.py` 的 `_STRINGS` 里加一个语言块，并在 `LANGUAGE_NAMES` 里加一项即可。
如果新块的键集合或占位符与其它语言不一致，`i18n.py --selftest` 会直接失败。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
