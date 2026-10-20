# opencode-tray

[English](README.md) · **简体中文**

一个面向 Windows 上 **OpenCode V2** 后台服务的托盘图标 —— 给长期泡在 OpenCode TUI 里的人用的
（本项目基于 Windows Terminal / PowerShell 7 开发）。

OpenCode V2 用一个**后台 server** 管理所有对话。由此带来两个不足：

- **关掉所有窗口 ≠ 关掉 OpenCode。** server 还在跑，还会一直占着内存和 CPU，
  而屏幕上没有任何东西告诉你它到底在不在。
- **有时候必须重启那个 server** —— 比如装插件。常规做法是先退出 OpenCode，
  再开个终端敲 `opencode service restart`；为了一条命令折腾一整套。

它同时也是回到最近工作的最快方式：菜单里列出跨所有项目的最近会话，点一条就能在
OpenCode 的 TUI 里打开。

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

## 它能做什么

- **三态图标** —— 绿色：server 在跑；灰色：没跑；红色：无响应或认证失败。
  悬停可看到版本、PID 与活跃会话数。
- **在菜单里重启 / 停止 / 启动服务**，完全不用碰终端 —— 这正是它的意义：
  一次点击，取代"退出 OpenCode 再敲命令"。
- **最近的会话，一键直达** —— 跨**所有项目**列出最近 15 条会话，运行中的排在前面，
  并显示相对时间。点一条即可在 OpenCode TUI 里打开它，且落在正确的目录。
- **英文与简体中文**，在 Language / 语言 子菜单切换；默认英文。
  选择记在 `settings.json` 里。
- **单实例**，另外可按需安装桌面快捷方式与开机自启。

它是**纯观察者**：绝不会在背后替你启动 server，只读取服务自己的注册文件、访问它的本地 HTTP API。
只有你主动点菜单，服务状态才会改变。

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

**在它已经运行时再次启动会有用处**：正在运行的实例会被要求重新挂上托盘图标。
Windows 偶尔会丢掉通知图标（典型情况是开机自启早于 shell 就绪），
没有这个通知机制的话，你只能找到并杀掉进程才能把图标找回来。

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
python  opencode_service.py --selftest   # 42 项:含真实端到端参数保真
python  opencode_tray.pyw   --selftest   # 63 项:含已渲染 Win32 菜单状态
python  win_shortcut.py     --selftest   # 9 项:.lnk 往返与 AppUserModelID 标记
```

这些不是走过场的冒烟测试。它们会**真的启动 cmd.exe**，断言一个刁钻的目录名能逐字节原样
到达；也会用 `GetMenuState` 查**已渲染**的 Win32 菜单，而不是相信永远会重新求值的描述符。

## 关于几个 Windows 坑

有几处看起来奇怪，知道原因就合理了。

- **通知里的应用名由 AppUserModelID 决定，而不是解释器。** 以 `pythonw.exe` 启动的进程会继承
  该可执行文件的 `FileDescription`，所以气泡标题以前显示成"Python"。托盘现在声明
  `qysnb.opencode-tray`，并写一个带同一 ID 的开始菜单快捷方式 —— shell 就是靠它解析出应用名的。
  两者缺一不可；快捷方式在首次运行时创建，`--uninstall-shortcut` 会一并删除。
- **打开会话会绕一个临时 `.cmd` 文件。** 把目录直接交给 `subprocess` 会被毁掉：
  MSVCRT 的引号层会把字面引号变成参数值的一部分，路径里的 `&` / `^` 还会被 `cmd` 吃掉。
  把命令写进批处理文件，`cmd` 就只按自己的规则解析一次。实测 7 种刁钻目录名，
  这是唯一一种全部保真的写法。
- **这里优先 `cmd.exe` 而不是 PowerShell** —— 只针对启动器，不针对你自己的 shell。
  `subprocess` 配 `shell=True` 让 7 条测试路径全坏；PowerShell 的
  `Start-Process -ArgumentList` 会把含空格的目录拆成多个参数。
- **会话在 Windows Terminal 里打开**（存在 `wt.exe` 时），并且启动器会设置
  `TERM=xterm-256color`。Windows Terminal **自己不会设 `TERM`**，缺了它 TUI 会走降级渲染。
- **菜单是显式重建的。** `pystray` 在 Windows 上**不会在显示菜单时才生成菜单**——它复用缓存的
  `HMENU`——所以只要相关状态变了就会重建；而菜单**正在显示时绝不重建**：
  那会销毁 Windows 正在跟踪的句柄，导致悬停高亮卡住或不出现。
- **开机自启用注册表而不是「启动」文件夹**，因为部分加固过的机器会直接拒绝
  `.cmd` / `.bat` / `.vbs`。
- **自启项与快捷方式会显式固定解释器**（用装它时的那个 Python 旁边的 `pythonw`），
  因为多 Python 机器上 `shutil.which` 可能选到另一个。
- **图标会在启动稍后重挂一次**，之后每当有新的启动实例发来通知也会重挂。
  shell 还在启动时 `Shell_NotifyIcon` 可能失败，而 `pystray` 忽略返回值 ——
  于是进程跑得好好的，却一个图标都没有。

## 新增语言

在 `i18n.py` 的 `_STRINGS` 里加一个语言块，并在 `LANGUAGE_NAMES` 里加一项即可。
如果新块的键集合或占位符与其它语言不一致，`i18n.py --selftest` 会直接失败。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
