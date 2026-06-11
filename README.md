# LCM CLI Tools

> 类 ROS2 风格的 LCM 命令行工具集 — 用于监控和调试 LCM（Lightweight Communications and Marshalling）网络。

## 功能概览

```
lcm topic echo <channel>   — 实时查看话题数据（类似 ros2 topic echo）
lcm topic list             — 列出活跃话题/通道（类似 ros2 topic list）
lcm topic stats            — 实时监控话题统计：频率、带宽、消息数、数据量（类似 ros2 topic hz）
lcm node list              — 列出发现的发布节点（类似 ros2 node list）
```

**亮点**：内置纯 Python `.lcm` 文件解析器，无需安装 `lcm-gen` 或配置 `PYTHONPATH`，指定 `.lcm` 文件即可自动解码消息。

## 安装

```bash
# 从源码安装（推荐）
cd lcm-tools
pip install -e .

# 如需传统 lcm-gen Python 包解码支持（可选）
pip install -e ".[decode]"
```

**依赖**: Python >= 3.9, `typer`, `rich`（`lcm` 包为可选依赖，仅用于传统 `--type module.Class` 方式解码）。

## 快速开始

```bash
# 查看所有子命令
lcm --help

# 列出活跃通道（监听 5 秒）
lcm topic list

# 实时查看特定通道的消息（原始 hex 格式）
lcm topic echo EXAMPLE

# 只接收 10 条消息
lcm topic echo EXAMPLE -n 10

# 用正则匹配多个通道
lcm topic echo "CAM.*"

# 监控所有通道的实时统计
lcm topic stats

# 只监控特定通道
lcm topic stats CAMERA

# 列出发现的发布节点
lcm node list
```

## 消息解码

### 方式一：直接指定 `.lcm` 文件（推荐）

无需安装 `lcm-gen`，无需配置 `PYTHONPATH`，工具内置纯 Python 解析器：

```bash
# 指定单个 .lcm 文件，自动按 fingerprint 匹配消息类型
lcm topic echo EXAMPLE --lcm-file types/example_t.lcm

# 指定目录（递归扫描所有 .lcm 文件）
lcm topic echo EXAMPLE -f types/

# 指定多个路径
lcm topic echo EXAMPLE -f types/ -f extra_types/

# 指定具体类型名（当 .lcm 文件中有多个 struct 时）
lcm topic echo EXAMPLE -f types/ --type example_t
```

支持完整的 LCM 类型系统：
- 所有原始类型（`int8_t` ~ `int64_t`、`float`、`double`、`string`、`boolean`、`byte`）
- 固定长度数组和变长数组（`double position[3]`、`int16_t ranges[num_ranges]`）
- 多维数组（`int32_t data[size_a][size_b][size_c]`）
- 嵌套结构体和跨文件类型引用
- 递归类型（如链表 `node_t` 中的 `node_t children[n]`）
- 常量声明（`const int32_t MAX_SIZE = 100`）

**工作原理**：解析 `.lcm` 文件 → 内存中构建解码类（`type()` 动态创建） → 按 payload 前 8 字节 fingerprint 自动匹配 → 解码并递归展开嵌套结构体。全程不生成任何文件。

### 方式二：传统 `lcm-gen` 生成文件

```bash
# 安装 lcm Python 包
pip install -e ".[decode]"

# 先用 lcm-gen 生成 Python 文件，并配置 PYTHONPATH
lcm-gen --python -d types/ types/example_t.lcm
export PYTHONPATH=types:$PYTHONPATH

# 使用 --type 指定解码类（module.Class 格式）
lcm topic echo EXAMPLE --type exlcm.example_t
```

### 自定义组播地址

```bash
lcm topic list --lcm-url 239.255.76.68 --lcm-port 7668
```

### 统计说明

| 指标 | 说明 |
|------|------|
| Rate (Hz) | 滑动窗口内的消息频率（最近 2000 条消息）|
| BW (KB/s) | 滑动窗口内的带宽 |
| Avg Size (B) | 每个消息的平均字节数 |
| Total (KB) | 累计传输总量 |

## 架构

```
┌───────────────────────────────────────────────┐
│                  CLI 层 (Typer)               │
│   topic echo │ topic list │ topic stats│ node │
├───────────────────────────────────────────────┤
│            显示层 (Rich Panel)                │
│   递归嵌套展开 │ hex dump │ 统计表格           │
├───────────────────────────────────────────────┤
│          类型解析层 (Pure Python)              │
│   .lcm 解析 → AST → fingerprint → 动态类生成   │
├───────────────────────────────────────────────┤
│           协议层 (Raw UDP Socket)              │
│     LCM Wire Protocol 解析（零依赖）            │
├───────────────────────────────────────────────┤
│           UDP 组播 (239.255.76.67)            │
└───────────────────────────────────────────────┘
```

- **零外部 LCM 依赖**：核心功能直接解析 UDP 组播数据包中的 LCM wire protocol
- **内置类型解析**：纯 Python 实现的 `.lcm` 文件解析器 + 运行时解码类生成器
- **节点发现**：通过 UDP 数据包的源 IP:port 推断不同发布者
- **传统解码兼容**：可选依赖 `lcm` Python 包，支持 `--type module.Class` 方式

## LCM 协议说明

LCM 使用 UDP 组播进行通信（默认 `239.255.76.67:7667`）。

**短消息**（< 64KB）：8 字节头 (magic=0x4c433032 + seqno) + channel name (\0结尾) + payload

**分片消息**：20 字节头 (magic=0x4c433033 + seqno + payload_size + fragment_offset + fragment_no + n_fragments)

参考：[LCM UDP Multicast Protocol](https://lcm-proj.github.io/lcm/content/udp-multicast-protocol.html)

## LCM 与 ROS2 概念映射

| LCM 概念 | ROS2 对应 | 说明 |
|----------|----------|------|
| Channel | Topic | 消息发布/订阅的通道 |
| UDP (IP:port) | Node | LCM 无原生 node 概念，通过发布者地址推断 |
| Fingerprint | Message Type Hash | 消息类型的唯一标识 |

## 项目结构

```
src/lcm_tools/
├── cli.py                       # Typer 入口，注册子命令
├── commands/
│   ├── topic_echo.py            # lcm topic echo
│   ├── topic_list.py            # lcm topic list
│   ├── topic_stats.py           # lcm topic stats
│   └── node_list.py             # lcm node list
├── core/
│   ├── discovery.py             # 被动通道/节点发现
│   ├── stats.py                 # 实时统计（频率、带宽）
│   ├── lcm_type_parser.py       # .lcm 文件解析器 + fingerprint 算法
│   └── lcm_type_builder.py      # 运行时解码类生成 + TypeRegistry
├── display/
│   ├── echo_display.py          # Rich 面板显示（含递归嵌套展开）
│   └── stats_display.py         # 统计表格显示
├── listener.py                  # UDP 组播监听线程
└── protocol.py                  # LCM Wire Protocol 解析
```

## 测试

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## 网络配置

如果收不到消息，请检查组播路由：

**macOS:**
```bash
# 查看组播路由
netstat -rn | grep 239

# 添加路由（如果需要）
sudo route add -net 239.255.76.0/24 -interface en0
```

**Linux:**
```bash
# 添加路由
sudo ip route add 239.255.76.0/24 dev eth0
```

## 许可证

MIT
