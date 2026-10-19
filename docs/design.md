# OpenCode 后台服务托盘指示器设计

- 日期：2026-10-19
- 状态：已批准（方案 A：读 `service.json` + HTTP 探测）
- 范围：Windows 系统托盘指示器项目（`opencode-tray/`），只读本机 OpenCode 服务的状态文件与 API

## 目标

在 Windows 任务栏通知区域（系统托盘）显示一个 OpenCode 后台服务的状态图标，做到：

1. 一眼看出后台服务**是否在工作**（运行中 / 无响应 / 未运行）。
2. 便捷执行**重启、停止、启动**服务。
3. 便捷**查看会话**（全局会话列表，含运行状态），并可一键在新终端接管某个会话。

## 背景与现状

OpenCode 采用客户端-服务器架构：一个后台服务持有会话、配置、插件与工具执行，所有本地客户端连它。
官方**没有**任何托盘/通知区域功能：

- CLI 只提供 `opencode service status|start|stop|restart`（命令行）。
- 有 OpenCode Desktop，但 V2 文档未描述其托盘行为。
- Windows 包管理器不受支持；本机是通过 npm 安装的 `opencode`（V1）与 `opencode2`（V2）两个 shim。

已在本机实测确认的数据通路：

| 用途 | 方式 | 实测结果 |
|---|---|---|
| 服务地址/PID/密码 | 读 `~/.local/state/opencode/service.json` | `{"id":…,"version":"2.0.32","url":"http://127.0.0.1:45278","pid":29352,"password":"…"}` |
| 健康检查 | `GET {url}/api/info`，`Authorization: Basic base64("opencode:<password>")` | 200，返回 `version`/`pid`/`urls` |
| 全局会话列表 | `GET /api/session` | 返回所有项目的会话，含 `title`/`location.directory`/`time`/`cost`/`tokens` |
| 活跃会话 | `GET /api/session/active` | `{"data":{"ses_…":{"type":"running"}}}` |
| 服务生命周期 | `opencode2 service restart\|stop\|start` | 可用 |

踩过的坑（决定实现细节）：

- `opencode api` 在**没有**健康服务时会**自动启动**服务。所以探测**不得**走 `opencode api`，否则托盘会变成服务的启动器。
- 本机 PowerShell 执行策略拦截 `opencode.ps1`（`UnauthorizedAccess`），但 `.cmd` 正常 → 启动子进程走 `cmd.exe`。
- `/api/session` 是**全局**的，而 `opencode session list` 只在**当前项目**内生效。托盘没有项目上下文，因此必须用 HTTP API 而非 `session list`。
- 无鉴权头、`Bearer`、`x-opencode-password`、查询参数 `?password=` 均返回 401；**只有 HTTP Basic（用户名固定 `opencode`）可用**。
- 环境现状：Python **3.13.5**（Conda，`F:\Tools\Conda`），`pystray` 与 `Pillow` **已安装**，`shutil.which("opencode2")` 直接命中 `opencode2.CMD`。
  （注：`AGENTS.md` 声称 Python 3.14，与实测不符。）
- **「启动」文件夹被加固**：实测向 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` 写入
  `.cmd` / `.bat` / `.vbs` 一律返回 `PermissionError(Errno 13)`，而 `.lnk` 与 `.txt` 放行
  （防持久化加固）。`schtasks /create /sc onlogon` 亦被拒（拒绝访问）。
  → 开机自启改用注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`：纯标准库 `winreg`、
  无需 COM、不受 PowerShell 执行策略影响，且同样出现在「任务管理器 → 启动」列表里。

## 非目标

- 不做浏览器打开 Web 端（`opencode pair`）。
- 不做独立的 Tkinter 会话详情窗口。
- 不做向会话发消息、中断会话（`/api/session/{id}/interrupt`）。
- 不做自定义 `.ico` 与主题。
- 不做多服务实例、OpenTunnel 远程地址（`https://…opentunnel.xyz`）的监控，只盯 `127.0.0.1`。
- 不做 `/api/event` SSE 实时事件流。
- 不修改 OpenCode 自身的配置、插件或数据库。
- 第二实例不做「唤醒第一个实例」的 IPC，只提示并退出。
- 不做 `open_log` / 「打开日志」：本机日志已达 35.8 MB，交给默认程序打开会卡住。
  该菜单项已按用户要求移除（`opencode_service.open_log` 与 `log_file_path` 一并删除）。

## 方案（A：读 `service.json` + HTTP 探测）

对比过的替代方案：

- **B：每轮 shell 调 `opencode2 service status` / `session list`** — 每轮 spawn 进程（数百 ms）；`session list` 是项目级的，拿不到全局会话；`opencode api` 会自动启动服务。**否决**。
- **C：订阅 `/api/event` SSE** — 真实时，但需断线重连与额外线程复杂度，对 3 秒级刷新属过度设计。**否决**。

## 架构

三个文件，数据层、系统集成层与界面层分离：

```
opencode_service.py      数据层：不 import 任何 GUI。可 --selftest 独立验证
win_shortcut.py          系统集成层：纯 ctypes 调 COM 创建/读取 .lnk。可 --selftest 独立验证
opencode_tray.pyw        界面层：托盘图标、菜单、轮询线程、CLI 定位、自启、桌面快捷方式
启动OpenCode托盘.cmd      双击启动（start "" pythonw opencode_tray.pyw）
```

拆分的理由：数据层与快捷方式层各含真实逻辑（状态分类、会话排序、命令拼装、COM vtable 调用），
值得脱离 GUI 单独测试；拆开后两者在未安装 pystray 的机器上也能运行。

### 单实例保护

用命名互斥体 `Local\OpenCodeServerTray.SingleInstance`：

- 选互斥体而非锁文件：进程无论正常退出还是崩溃，句柄都由内核释放，不会留下需要人工清理的过期锁。
- `Local\` 前缀表示只在当前登录会话内生效，一台机器多用户各自可跑一个托盘。
- `CreateMutexW` 必须以 `use_last_error=True` 取得 `GetLastError`，且 `restype` 必须设为 `HANDLE`
  （默认 `c_int` 会在 64 位下截断句柄）。
- 第二个实例：不静默退出，弹提示说明已在运行并指引去看通知区域，返回码 1。
- 一次性动作（`--install-*` / `--uninstall-*`）**不**取锁 —— 它们需要能与正在运行的托盘并存。

### 桌面快捷方式

- 用 `win_shortcut.create_shortcut()` 建 `.lnk`，指向 `pythonw` + 本脚本，工作目录为脚本所在目录。
- 桌面路径必须走 `SHGetFolderPathW(CSIDL_DESKTOPDIRECTORY)`，**不能**拼 `~/Desktop`：
  本机桌面已重定向到 OneDrive（`C:\Users\lenovo\OneDrive\桌面`）。
- 图标由 `draw_icon()` 现画成 `opencode_tray.ico`（16/24/32/48/64 多尺寸），
  因此仓库不必提交二进制图标；该文件已加入 `.gitignore`。
- `--install-shortcut` / `--uninstall-shortcut`，幂等，重复卸载给出友好提示。

### 踩过的坑：菜单在 Windows 上不会自动刷新，状态被冻结（已修，这是灰菜单的根因）

现象：托盘的「重启服务 / 停止服务 / 启动服务」三项变灰不可点，而「会话」「退出」正常。

**排查过程中我先给出过一个错误结论**（认为是 CLI 探测失败被永久缓存），
用户实际去悬停查看 tooltip 后否定了它 —— 提示里既没有「未找到 CLI」也没有「正在执行操作…」，
说明 `has_cli=true`、`busy=false`，描述符层本该是**可点**的。教训：猜测不能替代实测。

真正的机制（靠读 pystray 源码 + Win32 层实测确认）：

- pystray 在 Windows 上**不支持"显示菜单时才生成"**。右击时 `_on_notify` 直接用缓存的
  `self._menu_handle`（`_win32.py:214`）。
- 而 `update_menu()` 只在这三处被调用：`menu` setter、`_mark_ready()`（**托盘启动时一次**）、
  `_handler()`（**每次点击菜单项之后**）。
- 所以菜单是**启动那一刻的快照**,在用户点过任意一个菜单项之前**永远不会变**。
  `Menu(callable)` 的动态求值只在重建时发生 —— 我原先"动态子菜单"的设计在 Windows 上不成立。

后果不止是灰色：**会话子菜单同样是冻结的**,一直停在启动时的快照。

关键教训（写给自己）：验证必须查**渲染层**。`item.enabled` 会重新求值,永远显示"正确",
而 Windows 画的是缓存的 HMENU。我最初的验证脚本正是被这个假象骗过,才会得出错误结论。
现在 `--selftest` 用 `GetMenuState(hmenu, i, MF_BYPOSITION)` 直接查已渲染菜单。
（另一个坑：pystray 的菜单项 ID 不等于位置,子菜单项也占用 ID,所以必须用 `MF_BYPOSITION`。）

修法：`_sync_icon()` 里比较 `_menu_signature()` 指纹,变化时显式调用 `icon.update_menu()`。
指纹覆盖状态标签、版本、`has_cli`、`busy`、提示消息与会话列表,所以状态没变时不会每 3 秒无谓重建。

### 踩过的坑：CLI 探测失败被永久缓存（已修，潜在缺陷）

这是排查灰菜单时顺带发现的**另一个真实缺陷**,不是灰菜单的成因（成因见上一条）：

- `resolve_cli()` 曾把「没找到 CLI」的结果**永久缓存**（`_cli_cache["resolved"] = True`）。
- 而它要跑 `opencode2 --version` —— 启动一个很大的原生二进制,冷启动要好几秒。
  开机自启时那次探测一旦超时,`has_cli` 就永久为 False,只能重启托盘才能恢复。

修法：

- 失败结果只保留 `CLI_RETRY_SECONDS`（60 秒）,之后自动重试；成功结果长期复用。
- 候选路径除 PATH 外,显式兜底 `%APPDATA%\npm\opencode2.cmd` / `opencode.cmd`
  （自启环境下 PATH 可能不完整,不能只依赖 `shutil.which`）。
- `has_cli` 改为三态（`None` = 尚未确定）。**不在 `__init__` 里同步解析** ——
  否则冷启动时托盘要等好几秒才出现；改由轮询线程后台解析回填。
  未确定时菜单**保持可点**（点了会给出明确的错误说明）,只有确认为 False 才变灰。
- 探测超时由 30 秒收紧到 20 秒（有自动重试兜底,宁可快速失败）。
- 另加看门狗：`busy` 超过 `BUSY_TIMEOUT`（120 秒）视为卡死,不再算忙碌,
  免得任何一次意外又让菜单永久变灰。

**仍未完全确定的一点**（如实记录）：用户那次灰菜单,究竟是被冻结的哪个瞬时状态所致,
无法从已消失的旧进程里取证。理论上 `has_cli=False` 会让 tooltip 显示「未找到 CLI」,
而用户说没有 —— 所以不排除旧实例的 tooltip 未被完整读到。上述两处修复 + 菜单改为实时刷新后,
任何瞬时状态都不再会粘住,且 tooltip 会明确写出原因。


### 踩过的坑：菜单动作传了字符串（已修）

现象：点托盘里的「重启服务 / 停止服务 / 启动服务 / 打开日志」，弹气泡
`操作失败:'str' object is not callable`，动作不执行。

根因：`_make_action(kind)` 把 **`kind` 字符串**直接交给 `_run_async()`，
而 `_worker()` 会执行 `fn()`。会话项因为传的是 `lambda`（可调用对象）所以正常，
生命周期项全部失败 —— 正好解释了「能开会话但服务操作全报错」这一现象。

修法：新增 `SERVICE_ACTIONS: dict[kind, callable]` 映射表，`_make_action` 取映射值再传下去。
回归保护：验证脚本断言 4 个动作传给 `_run_async` 的都是可调用对象，并确认旧写法确实会抛 TypeError。

### 踩过的坑：新终端渲染与日常不一致（已修）

现象：会话能在新窗口打开，但 TUI 渲染与用户平时所见明显不同。

实测环境差异（**清掉父进程终端变量**以复刻托盘的真实启动环境，
托盘由资源管理器 / 开机自启拉起时环境里没有这些变量）：

| 启动方式 | `TERM` | `WT_SESSION` |
|---|---|---|
| 经典 conhost（原实现） | 空 | 空 |
| Windows Terminal | **空** | 有 |
| Windows Terminal + 启动器内 `set TERM` | `xterm-256color` | 有 |

两个叠加原因：

1. 本机 Windows 11 的 `DelegationConsole` / `DelegationTerminal` 全为 0，
   即默认终端是**经典 conhost**，所以 `start cmd.exe` 开出来的是 conhost 窗口；
   而用户日常用的是 Windows Terminal（字体与 VT 支持更好）。
2. `TERM` 缺失。用户日常会话里 `TERM=xterm-256color`，而托盘启动的环境里没有，
   opencode 的 TUI 于是走降级渲染。注意实测 Windows Terminal **自己不会设置 `TERM`**，
   必须显式设置。

修法：

- 启动器脚本里显式 `set TERM=xterm-256color`（与本机日常终端取值一致）。
- 新增 `build_terminal_command()`：优先用 `wt.exe`（`wt cmd.exe /k <启动器>`），
  找不到 `wt` 时退回经典 conhost。用 `cmd /k` 而非 `/c`，会话退出后保留窗口以便看报错。


```python
@dataclass
class ServerStatus:
    state: str          # running | unreachable | unauthorized | no_service
    version: str | None
    pid: int | None
    url: str | None
    detail: str         # 给 tooltip 的人类可读原因

@dataclass
class SessionItem:
    id: str; title: str; directory: str; running: bool; updated: int

def classify(state, http) -> str              # 纯函数，可单测
def probe_server() -> ServerStatus            # 每轮重新读 service.json
def list_sessions(limit=15) -> list[SessionItem]
def restart_server() / stop_server() / start_server() -> tuple[bool, str]
def open_session(item) -> None
```

### 状态判定规则（`classify()` 纯函数）

| `service.json` | `/api/info` | 图标 | tooltip |
|---|---|---|---|
| 不存在 | — | 灰 | 未运行（未安装 / 已 `disabled`） |
| 存在 | 超时 / 连接被拒 | 红 | 无响应（可能已崩溃） |
| 存在 | 401 | 红 | 认证失败（`service.json` 已过期） |
| 存在 | 200 | 绿 | 运行中 v2.0.32 · PID 29352 |

### 菜单结构

界面默认**英文**,可在 Language / 语言 子菜单切到简体中文(选择记进 `settings.json`)。

```
OpenCode Service: Running v2.0.32         ← 禁用标签行
────────────────────────────
Sessions     ▸  ● ses_eadcaf… OpenCode Windows tray…
                 ○ ses_ebb22f… (2h ago)
                 Refresh
────────────────────────────
Restart service / Stop service / Start service
────────────────────────────
Language     ▸  ● English
                 ○ 简体中文
────────────────────────────
Quit
```

### 国际化(i18n)

- 文案集中在 `i18n.py` 的语言表里,按键取值(`i18n._("menu.restart")`);数据层与界面层
  都只用键,**不硬编码文案**。这样两层都不需要知道当前语言。
- 语言自称(English / 简体中文)**不翻译**:界面是英文时也该能认出"简体中文"那一项。
- 偏好存在与本模块同目录的 `settings.json`(已 gitignore)。项目因此保持便携 ——
  整个文件夹搬走,语言设置跟着走。写不进去(目录只读)时只在当前进程内生效,不报错。
- `i18n.py --selftest` 会断言两种语言的**键集合与占位符名称完全一致** ——
  漏一条文案或写错一个 `{placeholder}` 会静默退回英文,只有显式检查才能发现。
- 界面层自检**先固定语言再断言**,并在结束时还原:否则用户在中文界面下跑自检会莫名失败。

### 关键实现决定

- **图标**：用 Pillow 现画 64×64 实心圆，绿 / 灰 / 红三态，16×16 缩放下仍可辨识。换自定义 `.ico` 只需改一个函数。
- **轮询**：后台 daemon 线程 + `threading.Event.wait(3)`（可中断）。**每轮重新读 `service.json`** —— 服务重启后端口与密码都会变。
- **会话子菜单**用 pystray 的动态求值，每次右键重新生成，不手动维护菜单树。
- **会话排序**：`running` 优先，再按 `time.updated` 倒序；最多 15 条；标题截断 40 字符；`&` 转义为 `&&`（Windows 菜单助记符，不转义会被吞掉或变下划线）。
- **点击会话**：写一个临时 `.cmd` 启动器，再用 `build_terminal_command()` 打开 ——
  优先 `wt.exe cmd.exe /k <启动器>`（Windows Terminal），退路是经典 conhost。
  启动器内先 `set TERM=xterm-256color` 再 `cd /d` 到会话目录。
  **不要**把命令直接拼进 cmd 的参数里 —— 原因见下面「踩过的坑」的三个条目。
- **菜单必须显式刷新**：Windows 上 pystray 不会在显示菜单时重建菜单，必须在状态变化时
  调用 `icon.update_menu()`。见下面「菜单在 Windows 上不会自动刷新」。
- **验证要看渲染层**：断言菜单灰/亮必须用 `GetMenuState` 查已渲染的 HMENU，
  查 `item.enabled` 会被动态求值骗过去。

### 数据层接口
- **CLI 定位**：`shutil.which("opencode2")` → 校验 `--version` 为 2.x → 回退 `opencode`；都失败则仅禁用生命周期菜单项，托盘其余功能照常。
  结果缓存，避免每轮 spawn 进程。
- **生命周期按钮**：后台线程执行，执行期间禁用对应菜单项防连点，完成后立即刷新状态。
- **依赖**：`pystray` + `Pillow`，本机已具备，零新增依赖。不建 `requirements.txt`（遵循项目现状），按 `sort_visualizer.py` 的写法在 docstring 头部标注依赖。
- **开机自启**：`--install-startup` 写入注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OpenCodeTray`，
  值为 `"<pythonw>" "<本脚本绝对路径>"`；`--uninstall-startup` 删除该值。
  不用「启动」文件夹的 `.cmd`，因为本机对 `.cmd`/`.bat`/`.vbs` 一律拒绝写入（见上）。
  额外收益：无登录时的窗口闪烁，且用户可在任务管理器里查看/禁用。
- **pythonw 无标准流**：以 pythonw 启动时 `sys.stdout` 为 `None`。模块顶部记录 `HAS_CONSOLE`，
  并把 `sys.stdout`/`sys.stderr` 接到空设备，避免任何意外的 `print` 或 argparse 写 `None` 崩溃；
  `--install-startup` 这类无控制台的反馈改用 `MessageBoxW` 弹窗。

### 踩过的坑：打开会话时目录被引号污染（已修）

现象：点托盘菜单里的会话，新终端报
`Error: ENOENT: no such file or directory, chdir 'F:\…\opencode_test ' -> 'F:\…\opencode_test '`，
目录末尾多出一个空格，会话打不开。

根因：最初把参数字典构造成 `f'"{directory}"'`（手工加引号）后交给 `subprocess`。
`subprocess` 会用 MSVCRT 规则再加一层引号转义，于是引号变成**字面字符**混进参数值。
实测探针收到的 argv 是 `'"F:\\…\\opencode_test"'` —— 引号成了值的一部分，被 Bun 解析后表现为末尾多一个空格。

修法与取舍（均用探针实测，非推测）：

| 写法 | 普通 | 含空格 | `&` | `^` | `%` | `( ) ;` |
|---|---|---|---|---|---|---|
| 手工加引号（原实现） | 坏 | 坏 | 坏 | 坏 | 坏 | 坏 |
| 只传原始字符串 | 好 | 好 | **坏** | **坏** | 好 | 好 |
| 整条命令行交给 `shell=True` | **全坏** | 全坏 | 全坏 | 全坏 | 全坏 | 全坏 |
| PowerShell `Start-Process -ArgumentList` | 好 | **坏**（被拆成多个参数） | — | — | — | — |
| **临时 `.cmd` 启动器（采用）** | 好 | **好** | **好** | **好** | **好** | **好** |

结论：把目录写进批处理**文件内容**，cmd 就只按自己的规则解析一次，既不会被 `subprocess`
二次转义，也不会把 `&`/`^` 当分隔符或转义符。批处理里 `%` 需写成 `%%`。
`shell=True` 与 PowerShell `Start-Process` 两条路都实测更差，故不采用。

回归保护：`opencode_service.py --selftest` 的第 7 节会**真的起 cmd**，用探针断言 6 种刁钻目录名
（普通 / 空格 / `&` / `^` / `%` / `( ) ;`）都原样到达目标程序。只断言字符串拼装是抓不住这个 bug 的。

### 错误处理

- `service.json` 不存在 → 灰图标，不抛异常。
- HTTP 超时 / 连接被拒 → 红图标，tooltip 写明原因。
- 401 → 红图标，tooltip 提示密码可能过期。
- `service.json` 内容损坏（JSON 解析失败）→ 按「不存在」处理。
- 会话获取失败 → 菜单内显示「(会话列表不可用)」，不影响图标状态与生命周期功能。
- 找不到 CLI → 生命周期菜单项禁用，tooltip 附加说明。

## 验证方式

- `python i18n.py --selftest` —— 两种语言的键集合/占位符一致性、切换生效、非法代码退回默认、容错。
- `python opencode_service.py --selftest` —— 不开 GUI：真实连接一次服务并打印状态；对 `classify()` 喂四种构造数据做断言；
  第 8 节真的起 cmd，验证 6 种刁钻目录名原样到达目标程序（防引号/元字符回归）；
  第 10 节验证 CLI 探测失败不会被永久缓存。
- `python opencode_tray.pyw --selftest` —— 不开窗口：菜单接线（防 `'str' object is not callable` 回归）、
  菜单已移除「打开日志」、语言子菜单、状态变化时会重建菜单、
  **用 `GetMenuState` 查渲染层的灰/亮**、语言切换后菜单与 tooltip 真的变化、看门狗、解释器固定。
- `python win_shortcut.py --selftest` —— 在桌面真建一个 `.lnk`，用 COM 读回校验（含中文与空格的往返），再删除。
- `python opencode_tray.pyw --demo 5` —— 托盘显示 5 秒后自动退出（沿用 `sort_visualizer.py --demo` 的项目惯例），用于自动化验证 GUI 能启动。
- **注意解释器**：托盘由桌面快捷方式 / 开机自启以**绝对路径** `F:\Tools\Conda\pythonw.EXE` 启动，
  所以自检应优先用同一个解释器跑一遍（`& "F:\Tools\Conda\python.exe" ...`），避免与 PATH 上的
  另一个 Python 混淆（本机 PATH 会变，已实测到 3.13.5 与 3.14.4 两种解析结果）。
- **安全约束一：测试期间严禁真的执行 `service stop` / `restart`。** 开发会话本身就跑在这个后台服务上，停掉它等于当场终止当前会话。生命周期路径只做命令拼装的单测，不发起真实调用。
- **安全约束二：严禁按进程名杀 `opencode`。** `opencode.exe` 既是 CLI 也是后台服务本体，
  `Get-Process opencode | Stop-Process` 会直接杀掉服务并中断当前会话（已实际踩过一次）。
  如需清理测试进程，先快照 PID，只清理「测试后新增且命令行含测试 session id」的那些 PID。

