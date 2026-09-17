# MaiBot LLM 中国象棋

[![Python tests](https://github.com/sky2002/maibot-xiangqi-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/sky2002/maibot-xiangqi-plugin/actions/workflows/test.yml)

让群友用「炮八平五」、坐标或自然语言，与 MaiBot 的 LLM 下中国象棋。
程序用 [pyffish 0.0.90](https://pypi.org/project/pyffish/0.0.90/) 生成合法着法和检查将军；LLM 从合法着法中选一步。没有调用引擎搜索，也不会在模型失败时随机落子。

![棋盘示例](docs/board.png)

## 安装到 Linux

适配 **MaiBot 1.2.5 系列的新插件运行时、maibot-plugin-sdk 2.8.1、Python 3.12+**。旧版 `BasePlugin` 插件系统不兼容。插件 ID 为 `sky2002.xiangqi`。

先停下 MaiBot，在 **MaiBot 根目录**执行：

```bash
git clone https://github.com/sky2002/maibot-xiangqi-plugin.git plugins/maibot-xiangqi-plugin
uv pip install --python .venv/bin/python -r plugins/maibot-xiangqi-plugin/requirements.txt
```

使用 pip 管理环境时，第二行可换为：

```bash
.venv/bin/python -m pip install -r plugins/maibot-xiangqi-plugin/requirements.txt
```

依赖必须安装在 **MaiBot 实际运行的 Python 环境**；环境路径不是 `.venv` 时请替换。不要安装到插件自己的虚拟环境后，仍用另一个环境启动宿主。

随后按原来的方式启动 MaiBot，在插件管理页确认「LLM 中国象棋」已加载并启用，授予其声明的 `send.text`、`send.image`、`llm.generate`、`config.get` 能力。SDK 会生成插件 `config.toml`，默认启用。宿主 `utils` 任务需要已有可用模型。

群内发送：

```text
下棋 开始
下棋 炮八平五
```

初次启动先发开局图；玩家合法落子后提示思考中，bot 走完后发新棋盘。棋评开启时，会另附一句使用宿主人设生成的短评。

### CPU、依赖和字体

- Linux x86_64 可使用 pyffish 发布的 manylinux wheel（需要 glibc 2.27+）。仓库 CI 在 Ubuntu 上验证 Python 3.12、3.13。
- ARM64 或其他没有匹配 wheel 的系统需要从源码编译 pyffish。Debian/Ubuntu 可先安装 `build-essential` 和匹配当前 Python 版本的开发头文件，再运行依赖安装命令；该平台尚未单独验证。
- 内置约 190 KB 的 Noto 中文字体子集，Pillow 直接生成 PNG。无需浏览器、外部画图服务或系统中文字体。
- 如果启动脚本会执行严格的 `uv sync`，可能清理额外安装的插件依赖。请使用宿主提供的插件依赖管理，或在同步后重新执行上面的插件依赖安装命令。

## 指令

所有指令都需要 `下棋` 加空格，群聊中的普通讨论不会触发落子。

| 指令 | 行为 |
| --- | --- |
| `下棋 开始` / `下棋 开始 红` | 发起者执红先走 |
| `下棋 开始 黑` | 发起者执黑，bot 执红先走 |
| `下棋 炮八平五` | 中文棋谱；数字和常见繁体字也支持 |
| `下棋 前车平八` | 支持前/后/中棋子；仍有歧义时列候选 |
| `下棋 b2 e2` / `下棋 b2e2` | 固定棋盘坐标 |
| `下棋 把b2的炮移到e2` | LLM 解析明确意图，程序校验 |
| `下棋 选择 1` | 由发起者确认本局候选列表中的走法 |
| `下棋 棋盘` | 任何群友可查看当前棋盘 |
| `下棋 悔棋` | 撤回最近一次玩家落子及 bot 回应 |
| `下棋 重试` | bot 失败或重启后轮到 bot 时，重新请求选招 |
| `下棋 认输` | 发起者认输，bot 获胜 |
| `下棋 结束` | 配置的本群管理员结束，不计胜负 |
| `下棋 帮助` | 查看使用说明 |

棋盘始终红方在下，`a0` 在左下，`i9` 在右上。棋谱的路数按执棋方视角计数：红方从右到左一至九，黑方从黑方自己的右到左 1 至 9。执黑时也不翻转图片，坐标不会变化。

每群同时一盘，绑定发起者账号。围观者只能查看棋盘，不能落子、悔棋、确认候选或重试。查看棋盘和无效指令不延长占桌时间。思考或解析期间拒绝新的落子和悔棋，仍允许发起者认输、管理员结束。

自然语言只用于解析本条落子意图，不能替玩家推荐或选择好棋。语义含糊时会列出候选或要求补充；LLM 的语义判断仍可能有误，精确操作建议使用棋谱或坐标。

## 配置

可在插件配置页编辑，或参考 [config.example.toml](config.example.toml)。不要覆盖已有配置。修改配置后重载插件或重启宿主。

| 字段（`[chess]`） | 默认 | 含义 |
| --- | --- | --- |
| `model_name` | `""` | 选招模型；填宿主已配置的具体模型名称，留空使用 `utils` |
| `request_timeout` | `30` | 每次请求最多等待的秒数，1–300 |
| `idle_minutes` | `30` | 无有效操作的占桌时限，1–10080 分钟 |
| `repetition` | `3` | 同一局面且同一方行棋出现几次后和棋，2–10 |
| `no_capture_halfmoves` | `120` | 连续无吃子半回合数，2–1000 |
| `commentary` | `true` | 是否生成简短人设棋评 |
| `admins` | `[]` | 管理员名单：`["平台:群号:用户号"]` |

自然语言解析和棋评复用 `utils`；`model_name` 只覆盖 bot 选招。请求沿用宿主的 token 与温度设置。选招和解析在超时、接口失败或无效输出后自动重试一次；最坏可能等两次超时时长，随后保留棋局。棋评单独限时 10 秒，失败不会影响已经保存的落子。

**管理员权限说明：** 当前 SDK 的群消息字段没有可信的群主/管理员角色。因此需要 bot 部署者将允许强制结束的群管理员写进名单，插件不根据发言内容或自称管理员授予权限。例如 QQ 群 `123456` 的管理员 `987654` 写为 `qq:123456:987654`。同一账号在别的群不会因此获得权限。

## 裁判规则与棋力

- 马腿、象眼、炮架、九宫、过河兵、将帅照面、自陷被将军等由 pyffish 检查。
- 将死和困毙均判无合法走法的一方负，优先于和棋规则。
- **插件自定义简化和棋规则**：棋子位置与行棋方相同的局面第 3 次出现，或连续 120 个半回合没有吃子，自动和棋。双方各走一步算两个半回合；兵移动也计入无吃子计数。阈值可配置。
- 长将、长捉不单独判责，可能通过反复将军达到重复和棋；首版不是赛事级裁判。
- 规则阈值和空闲时限在开局时保存，改配置后对新局生效。
- LLM 真正选招，所以棋力取决于所配置模型，可能合法地走出臭棋。

## 存档与故障恢复

棋谱保存在 SDK 分配的 `self.ctx.paths.data_dir/xiangqi.sqlite3`，通常为宿主 `data/plugins/sky2002.xiangqi/`。按群保留当前或最近结束的一盘；新局替换上一盘。只存储必要的群/玩家标识、棋谱及时间，不保存普通群聊或模型推理文本。

每一步先检查再原子保存，之后才发图或请求 bot。模型失败不会丢掉玩家已经走出的一步。重复投递的消息不会再次执行；认输、结束、超时或开新局后到达的旧模型回复会被丢弃。

重启后发送 `下棋 棋盘` 查看：轮到玩家时可直接走，轮到 bot 时发送 `下棋 重试`。歧义候选列表不持久化，重启后重新描述即可。停机时间计入空闲时限，超过时限的棋局启动时直接结束。结束的棋局不能悔棋，可查看末局棋盘或开新局。

## 更新

停下 MaiBot 后，在根目录执行：

```bash
git -C plugins/maibot-xiangqi-plugin pull --ff-only
uv pip install --python .venv/bin/python -r plugins/maibot-xiangqi-plugin/requirements.txt
```

随后重新启动。插件运行数据位于 SDK 的数据目录，更新源码不会覆盖存档。

## 开发验证

在插件目录运行：

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check .
```

测试使用真实 pyffish 和 Pillow，LLM/群消息接口使用可控替身；包括走法、判负、简化和棋、红黑开局、权限、重复消息、重试、悔棋、群隔离、迟到回复、SDK 入口加载与中文 PNG。真实聊天平台和模型服务需要安装后联调。

## 开源组件

- [pyffish / Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish)：规则绑定，GPL-3.0-or-later；使用的 [0.0.90 源码发布](https://pypi.org/project/pyffish/0.0.90/#files)。
- [Pillow](https://github.com/python-pillow/Pillow)：棋盘绘制，HPND。
- [maibot-plugin-sdk](https://github.com/Mai-with-u/maibot-plugin-sdk)：插件协议和宿主能力调用。
- [Noto CJK](https://github.com/notofonts/noto-cjk)：中文字体，SIL OFL 1.1；子集更名为 MaiBotXiangqi，见 [字体说明](xiangqi/assets/README.md)。

插件代码采用 [GPL-3.0-or-later](LICENSE)。
