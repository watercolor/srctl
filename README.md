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
srctl switch <id>             # 切换服务器
srctl config <id>             # 查看服务器完整配置
srctl export                  # 导出所有服务器为 JSON
```

### 服务器标识

`<id>` 支持以下格式：

- **序号**: `srctl switch 0`
- **名称搜索**: `srctl switch "德国04"`
- **UUID**: `srctl switch "C62DF2EF-221A-4157-AEE6-21C62542D5F4"`

优先级：UUID 精确匹配 > 名称子串匹配 > 序号匹配

## 示例

```bash
# 查看所有服务器
srctl list

# 切换到德国服务器
srctl switch "德国04"

# 连接并查看状态
srctl on
srctl active

# 查看某个服务器配置
srctl config "香港AI"

# 导出备份
srctl export > servers.json
```

## 注意事项

- **`switch` 自动接管 Shadowrocket 生命周期**：检测到正在运行会先 `quit`，再写 plist，再 `open -a` 启动；如果 switch 前 VPN 处于连接状态，启动后自动重连。这是因为运行中的 Shadowrocket 会用内存里的旧选中覆盖 plist。
- 如果 Shadowrocket 未运行，`switch` 只写 plist，不会启动它。
- GUI 选中项可能在 Shadowrocket 启动后**延迟 1–3 秒**刷新显示，是正常现象。
- 序号会随服务器增删变化，推荐用名称搜索。
- 服务器列表顺序与 Shadowrocket 内部数据源一致，GUI 可能按分组显示略有不同。
