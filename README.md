# srctl - Shadowrocket CLI Controller

macOS 终端控制 [Shadowrocket](https://apps.apple.com/app/shadowrocket/id932747118) 的命令行工具。

## 安装

```bash
cd ~/srctl && bash install.sh
```

默认安装到 `~/.local/bin/`，确保该路径在 `PATH` 中：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## 命令

### VPN 控制

```bash
srctl on          # 连接 VPN
srctl off         # 断开 VPN
srctl toggle      # 切换 VPN 状态
```

### 服务器管理

```bash
srctl list                    # 列出所有服务器，[*] 标记当前选中
srctl active                  # 显示当前选中的服务器
srctl switch [<id>]           # 切换服务器（无参数时交互选择）
srctl config [<id>]           # 查看服务器完整配置（无参数时交互选择）
srctl set <id> <key> [value]  # 修改服务器属性
srctl export                  # 导出所有服务器为 JSON
```

`switch` 和 `config` 不带参数时弹出上下键交互选择列表。

### 连通性测试

```bash
srctl ping              # 测试所有服务器 TCP 连通性
srctl ping <id>         # 测试指定服务器
```

并发 30 线程，按延迟排序，`[+]` < 200ms, `[~]` < 500ms, `[-]` 偏慢, `[x]` 超时。

### App 管理

```bash
srctl ps                # 查看 Shadowrocket 进程/VPN 状态
srctl open              # 启动 Shadowrocket
srctl close             # 退出 Shadowrocket
```

### 服务器标识

`<id>` 支持以下格式：

- **名称搜索**: `srctl switch "德国04"`
- **UUID**: `srctl switch "C62DF2EF-221A-4157-AEE6-21C62542D5F4"`
- **序号**: `srctl switch 0`

优先级：UUID 精确匹配 > 名称子串匹配 > 序号匹配

### 修改服务器属性

```bash
# 修改中转代理（不输入 value 弹出交互选择列表）
srctl set nyip chain
srctl set nyip chain "UUID-of-target-server"

# 修改其他字段
srctl set nyip host "new-host.com"
srctl set nyip port "8080"
```

## 示例

```bash
# 查看所有服务器
srctl list

# 交互切换到德国服务器
srctl switch

# 连接并查看状态
srctl on
srctl active

# 查看某个服务器配置
srctl config "香港AI"

# 修改 nyip 的中转代理
srctl set nyip chain

# 连通性测试
srctl ping

# 导出备份
srctl export > servers.json
```

## 注意事项

- **`switch` 自动接管 Shadowrocket 生命周期**：检测到正在运行会先断开 VPN → `quit` → 写 plist → `open -a` 启动 → 自动重连。这是因为运行中的 Shadowrocket 会用内存里的旧选中覆盖 plist，且残留的 VPN 隧道会复用旧配置。
- 写入 plist 后自动 `killall cfprefsd` 刷新缓存，确保 Shadowrocket 看到新值。
- GUI 选中项可能在 Shadowrocket 启动后**延迟 1–3 秒**刷新显示，是正常现象。
- 序号会随服务器增删变化，推荐用名称搜索或交互选择。
- `srctl set` 直接修改 `ServerManager` (NSKeyedArchiver) 文件，Shadowrocket 下次保存订阅时会覆盖，修改仅在当前会话有效。
