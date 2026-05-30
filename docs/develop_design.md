# 设计文档

## 概述

srctl 通过读写 Shadowrocket 的本地文件实现终端控制，不依赖任何外部 API 或网络通信。

## 架构

```
srctl (shell wrapper)
  └── srctl.py (Python CLI)
        ├── NSKeyedUnarchiver     # 解析 ServerManager 文件
        ├── Prefs 读写 (plistlib)  # 读写偏好设置 plist
        ├── 进程检测 (pgrep)       # 拒绝在 Shadowrocket 运行时 switch
        └── URL Scheme 调用        # 控制 VPN 连接
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
```

采用 **读取-修改-写回** 模式，保留其他键值（如 `DLWSubscribeUpdateDate`、权限设置等）。

> ⚠️ 早期实现走 `plutil -convert json` 中转 JSON，但该 plist 包含 `DLWSubscribeUpdateDate` (NSDate)，JSON 不支持 date 类型导致 `plutil` 直接报错 `Invalid object in plist for JSON format`。原代码 `try/except` 静默吞了异常返回 `{}`，结果"读-改-写"实际变成了"清空-改-写"，丢失其他键。改用 `plistlib` 后保留所有原生类型。

> 注意: 之前尝试写入 `DLWServerNotify.nosync` 无效，该文件是 Shadowrocket 向 Widget 发送的单向通知，并非配置来源。

#### 运行时进程接管

Shadowrocket 主进程在运行时持有"选中服务器"的内存副本，并在某些时机（订阅更新、应用 idle、退出）将内存配置全量回写 plist，**覆盖 srctl 的修改**。

`cmd_switch` 自动管理生命周期，避免静默失效:

```
1. was_running = pgrep -x Shadowrocket
2. was_connected = was_running && scutil --nc list 含 "(Connected) ... com.liguangming.Shadowrocket"
3. if was_running:
     osascript -e 'quit app "Shadowrocket"'
     轮询 pgrep 直至进程退出 (timeout 5s)
4. plistlib 写入 SelectedServerUUID / SelectedServerName
5. if was_running:
     open -a Shadowrocket
     轮询 pgrep 直至进程出现 (timeout 10s)
     if was_connected:
       sleep 1.0    # 等待 URL handler 注册
       open rocket://connect
```

未运行情形不重启 Shadowrocket，仅写 plist；下次用户手动启动会读到新值。

> GUI 选中项的视觉刷新是异步的，启动后通常延迟 1–3 秒才更新。plist 内容、`srctl active`、命令行写入路径都是即时一致的。

### 3. VPN 连接控制

通过 macOS URL Scheme 实现：

| 命令 | URL |
|------|-----|
| `srctl on` | `rocket://connect` |
| `srctl off` | `rocket://disconnect` |
| `srctl toggle` | `rocket://toggle` |

URL Scheme 定义在 `Shadowrocket.app/Contents/Info.plist` 的 `CFBundleURLSchemes` 中，包括 `shadowrocket://`、`rocket://`、`ss://`、`sr://`、`shadowsocks://`。

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

1. **switch 必须在 Shadowrocket 退出时执行**: 运行中进程会用内存覆盖 plist。srctl 通过 `pgrep -x Shadowrocket` 检测并拒绝。典型流程:`osascript -e 'quit app "Shadowrocket"' && srctl switch X && open -a Shadowrocket`。
2. **服务器修改**: `srctl set` 为只读提示。`ServerManager` 是 NSKeyedArchiver 的复杂对象图，直接修改容易破坏数据结构，且 Shadowrocket 下次保存时会覆盖。
3. **序号不稳定**: 服务器列表顺序来自底层数组，增删服务器后序号会变化。推荐用名称搜索。
