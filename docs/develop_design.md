# 设计文档

## 概述

srctl 通过读写 Shadowrocket 的本地文件实现终端控制，不依赖任何外部 API 或网络通信。

## 架构

```
srctl (shell wrapper)
  └── srctl.py (Python CLI)
        ├── NSKeyedUnarchiver          # 解析 ServerManager 文件
        ├── ServerManager 修改          # 直接修改 NSKeyedArchiver 对象图
        ├── Prefs 读写 (plistlib)       # 读写偏好设置 plist
        ├── cfprefsd 缓存刷新           # killall cfprefsd 强制刷新
        ├── 进程检测 (pgrep)            # 检测 Shadowrocket 运行状态
        ├── 交互选择器 (termios/tty)     # 上下键导航的服务器列表
        ├── 连通性测试 (socket)          # TCP 并发 ping
        └── URL Scheme 调用             # 控制 VPN 连接
```

## 核心机制

### 1. 服务器列表读取

**目标文件**: `~/Library/Group Containers/group.com.liguangming.Shadowrocket/ServerManager`

**格式**: Apple Binary Plist，内嵌 `NSKeyedArchiver` 序列化。

**解析流程**:

```
ServerManager (binary plist)
  → plutil -convert xml1                        # 转为 XML
  → NSKeyedUnarchiver._resolve_ref()             # 解析 CF$UID 引用图
  → 遍历 DLWServer / Subscribe 对象              # 提取服务器属性
  → dict[title] = {host, port, uuid, type, ...}
```

`NSKeyedUnarchiver` 实现了 NSKeyedArchiver 的对象图解析：
- `CF$UID` 整数引用 → 在 `$objects` 数组中按索引查找
- 递归解析，带深度限制（50 层）防止循环引用
- 特殊处理 `NSDate`、`NSString`、`NSArray`、`NSDictionary` 等容器类型
- `DLWServer` 和 `Subscribe` 类直接映射为 dict，保留 `_class` 标记

### 2. 服务器切换

**目标文件**: `~/Library/Group Containers/group.com.liguangming.Shadowrocket/Library/Preferences/group.com.liguangming.Shadowrocket.plist`

有效键值:

| Key | 类型 | 说明 |
|-----|------|------|
| `group.com.liguangming.SelectedServerUUID` | String | 选中服务器 UUID |
| `group.com.liguangming.SelectedServerName` | String | 选中服务器名称 |

**写入流程**:

```
plistlib.load(open(plist, "rb"))     # 直接读取 binary plist → dict
  → 更新 SelectedServerUUID / SelectedServerName
  → plistlib.dump(prefs, fmt=FMT_BINARY)  # 直接写回 binary plist
  → killall cfprefsd                  # 强制刷新偏好设置缓存
```

采用 **读取-修改-写回** 模式，保留其他键值（如 `DLWSubscribeUpdateDate`、权限设置等）。

> ⚠️ 早期实现走 `plutil -convert json` 中转 JSON，但该 plist 包含 `DLWSubscribeUpdateDate` (NSDate)，JSON 不支持 date 类型导致 `plutil` 直接报错 `Invalid object in plist for JSON format`。原代码 `try/except` 静默吞了异常返回 `{}`，结果"读-改-写"实际变成了"清空-改-写"，丢失其他键。改用 `plistlib` 后保留所有原生类型。

> ⚠️ `cfprefsd` 是 macOS 的偏好设置守护进程，会缓存 plist 值。直接写 plist 文件（绕过 `CFPreferences` API）后，Shadowrocket 通过 `UserDefaults(suiteName:)` 读取到的仍是旧值。必须在写入后 `killall cfprefsd` 强制重新读取磁盘。

> 注意: 之前尝试写入 `DLWServerNotify.nosync` 无效，该文件是 Shadowrocket 向 Widget 发送的单向通知，并非配置来源。

#### 运行时进程接管

Shadowrocket 主进程在运行时持有"选中服务器"的内存副本，并在某些时机（订阅更新、应用 idle、退出）将内存配置全量回写 plist，**覆盖 srctl 的修改**。

`cmd_switch` 自动管理生命周期，避免静默失效:

```
1. was_running = pgrep -x Shadowrocket
2. was_connected = was_running && scutil --nc list 含 "(Connected) ... com.liguangming.Shadowrocket"
3. if was_connected:
     open rocket://disconnect          # 先断开 VPN，避免残留隧道复用旧配置
     轮询 scutil 直至断开 (timeout 5s)
4. if was_running:
     osascript -e 'quit app "Shadowrocket"'
     轮询 pgrep 直至进程退出 (timeout 5s)
5. plistlib 写入 SelectedServerUUID / SelectedServerName + killall cfprefsd
6. if was_running:
     open -a Shadowrocket
     轮询 pgrep 直至进程出现 (timeout 10s)
     if was_connected:
       sleep 1.5    # 等待 app 初始化并注册 URL handler
       open rocket://connect
```

> 关键修复: switch 前先断开 VPN 再 quit，确保重启后 Shadowrocket 见到的是干净的隧道状态。否则系统残留的 VPN 隧道会使 Shadowrocket 误认为已连接，不真正重建连接，导致选中服务器未实际生效。

未运行情形不重启 Shadowrocket，仅写 plist；下次用户手动启动会读到新值。

> GUI 选中项的视觉刷新是异步的，启动后通常延迟 1–3 秒才更新。plist 内容、`srctl active`、命令行写入路径都是即时一致的。

### 3. 服务器属性修改（srctl set）

`cmd_set` 不再只是存根，而是直接修改 `ServerManager` 文件内的 `$objects` 数组。

**流程**:

```
ServerManager (binary plist)
  → plutil -convert xml1 → plistlib.load()
  → 根据 UUID 查找 DLWServer 对象
  → 通过 CF$UID 引用链定位字段值
  → 修改值（支持 NSString、int、bool、float）
  → plistlib.dump() → plutil -convert binary1 → 替换原文件
```

支持的值类型:
- `NSString` / `NSMutableString`: 修改 `NS.string`
- 纯字符串: 直接替换
- `int` / `float`: 按类型转换
- `bool`: 接受 `true/false/yes/no/0/1`

修改后 Shadowrocket 下次保存订阅时会覆盖，因此 `srctl set` 的修改仅在当前会话有效。

### 4. 交互选择器

`switch`、`config`、`set chain` 不带参数时进入上下键交互选择。

技术实现:
- `termios` + `tty`: 终端 raw 模式
- `os.read(fd)` + `select`: 读取单键，区分 Escape / 方向键
- `\x1b[2J\x1b[H`: 全屏清除重绘
- `\x1b[7m`: 反色高亮当前选中项
- 根据终端高度计算可见窗口，支持滚动 + PgUp/PgDn

按键: `↑/↓` 移动 | `Enter/Space` 确认 | `Esc/q` 取消 | `PgUp/PgDn` 翻页

### 5. 连通性测试（srctl ping）

TCP 连接测试每个服务器的 host:port，测量延迟。

技术实现:
- `socket.create_connection()` 超时 3s
- `ThreadPoolExecutor(max_workers=30)` 并发测试
- 按延迟排序，标记: `[+]` < 200ms, `[~]` < 500ms, `[-]` 偏慢, `[x]` 超时
- 自动跳过 Subscribe 条目和无 host/port 的服务器

### 6. VPN 连接控制

通过 macOS URL Scheme 实现：

| 命令 | URL |
|------|-----|
| `srctl on` | `rocket://connect` |
| `srctl off` | `rocket://disconnect` |
| `srctl toggle` | `rocket://toggle` |

URL Scheme 定义在 `Shadowrocket.app/Contents/Info.plist` 的 `CFBundleURLSchemes` 中，包括 `shadowrocket://`、`rocket://`、`ss://`、`sr://`、`shadowsocks://`。

### 7. App 生命周期管理

| 命令 | 功能 | 实现 |
|------|------|------|
| `srctl ps` | 查看状态 | pgrep + scutil + plist 读取 |
| `srctl open` | 启动 App | `open -a Shadowrocket` |
| `srctl close` | 退出 App | `osascript -e 'quit app "Shadowrocket"'` + 轮询确认 |

## Shortcuts Intents（预研）

Shadowrocket 注册了以下 Siri Intents（`Intents.appex`）：

| Intent | 用途 |
|--------|------|
| `DLWStartTunnelIntent` | 启动隧道 |
| `DLWStopTunnelIntent` | 停止隧道 |
| `DLWToggleTunnelIntent` | 切换隧道 |
| `DLWGlobalRoutingIntent` | 全局路由 |
| `DLWCronScriptIntent` | 定时脚本 |
| `DLWUpdateSubscriptionIntent` | 更新订阅 |

可通过 Shortcuts 应用创建快捷指令调用，但 srctl 选择 URL Scheme 方案以避免用户手动创建快捷指令。

## 已知限制

1. **switch 过程中 Shadowrocket 会重启**：主动断开 VPN → 退出 → 写 plist → 重新启动 → 恢复 VPN。整个过程约 5-15 秒。
2. **服务器修改非持久化**: `srctl set` 直接修改 ServerManager 的 NSKeyedArchiver 对象图，但 Shadowrocket 下次保存（订阅更新、退出等）时会覆盖修改。
3. **连通性测试为纯 TCP**: `srctl ping` 只测试 TCP 握手延迟，不经过代理链。链式代理（chain）的服务器实际连通性取决于上游代理状态。
4. **序号不稳定**: 服务器列表顺序来自底层数组，增删服务器后序号会变化。推荐使用名称搜索或交互选择。
