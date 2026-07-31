# 4Seas Bot

> 4Seas 社区的开源运营机器人。每晚预告次日活动、回答常见问题、响应互动、关键词主动触达。
> 自托管、Apache-2.0、数据留在你自己的机器上。

Telegram: [@zuchiangmaibot](https://t.me/zuchiangmaibot) · 活动数据来自 [Social Layer / 4Seas](https://app.sola.day/event/4seas)

---

## 一、它做什么

社区运营里重复度最高的四件事，交给它：

| # | 能力 | 说明 |
|---|---|---|
| 1 | **每日活动预告** | 每天晚上 19:00（Asia/Bangkok）预告**明天**有什么活动，一行一场，点标题跳详情 |
| 2 | **通用问答** | `/ask` 或 @ 它，基于社区 FAQ 知识库回答；答不上来的老实说答不上来，不编 |
| 3 | **互动响应** | 新成员欢迎、被回复时接话、常用命令（`/events` `/faq` `/help`） |
| 4 | **关键词主动触发** | 群里出现「住宿」「签证」「怎么报名」这类词，自动补一条有用的信息 |

活动数据不用手填 —— 运营在 sola.day 建活动，bot 自动就预告了。

### 活动是怎么进来的

```
Social Layer         08:30 / 18:30         本地 SQLite         每天 19:00
api.sola.day    ──导入──▶   events 表   ──读库──▶  群内预告「明天有什么」
(未来 60 天)                (幂等,无重复)
```

导入配了两个时间点：早上一次，晚上 18:30 再来一次 —— 后者保证白天新加的活动
能赶上当晚 19:00 的预告。

导入是**幂等**的：同一个活动无论同步多少次都只有一行。靠三层保证 ——
主键 `(source, event_id)` + UPSERT、`content_hash` 判断内容有没有真的变过、
上游取消的活动打软删除标记而不是物理删除。所以这个任务可以任意频率重跑，
跑 100 次和跑 1 次的库状态完全一致。管理员随时可以 `/sync` 手动补一次。

---

## 二、快速开始

需要 Python 3.11+。

```bash
git clone git@github.com:4seas-community/4Seas-bot.git
cd 4Seas-bot
uv venv --python 3.11 && uv pip install -e ".[dev]"
cp .env.example .env         # 填 token,见下
./start.sh                   # 前台跑，Ctrl-C 停
./start.sh --bg              # 后台跑，日志在 data/bot.log
./start.sh --status          # 看状态
./start.sh --stop            # 停
```

`start.sh` 会先检查虚拟环境和 `.env`，并且**拒绝启动第二个实例** ——
Telegram 长轮询同一 token 只允许一个消费者，两个进程会互抢 update。

启动时会自动补一次活动导入，所以第一次跑完库里就有数据了。

### 必填环境变量

```dotenv
TELEGRAM_BOT_TOKEN=          # BotFather 给的 token
TELEGRAM_ADMIN_IDS=          # 管理员 user id,逗号分隔
TELEGRAM_ALLOWED_CHATS=      # 允许 bot 工作的群 id,逗号分隔(白名单,防止被拉进乱七八糟的群)
DEEPSEEK_API_KEY=            # 问答用,主力模型
OPENAI_API_KEY=              # 可选,DeepSeek 挂了时兜底
SOLA_GROUP=4seas             # Social Layer 上的社区标识
SYNC_TIMES=08:30,18:30       # 每天几点导入活动(逗号分隔,可配多个)
SYNC_HORIZON_DAYS=60         # 一次导入未来多少天
DAILY_REPORT_TIME=19:00      # 播报时间
DAILY_REPORT_OFFSET_DAYS=1   # 播哪一天:0=当天,1=明天
DAILY_REPORT_DAYS_AHEAD=0    # 从起始日再多播几天,0=只播那一天
EVENTS_COMMAND_DAYS=7        # /events 默认看未来几天(从今天算起)
TZ=Asia/Bangkok
```

播报范围由 `OFFSET` + `AHEAD` 两个值组合：

| 想要的效果 | OFFSET | AHEAD |
|---|---|---|
| **每晚预告明天**（当前配置） | 1 | 0 |
| 早上播今天 | 0 | 0 |
| 每晚预告明天 + 后天 | 1 | 1 |
| 今天起一整周 | 0 | 6 |

改这两个值不需要重新导入 —— 库里存的是未来 60 天，播报只是换个查询窗口。
完整配置项见 [`.env.example`](.env.example)。

### 跑测试

```bash
uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

不需要 `.env`、不需要任何密钥 —— `tests/conftest.py` 会填占位配置，
并显式关掉 `.env`，保证本机和 CI 跑的是同一套配置。

### 怎么拿到群的 chat_id

把 bot 拉进群、在群里发一条消息，然后：

```bash
.venv/bin/python scripts/chat_ids.py          # 读一次
.venv/bin/python scripts/chat_ids.py --watch  # 蹲守,等新群出现
```

它会直接打印可以粘进 `.env` 的 `TELEGRAM_ALLOWED_CHATS=...`。

⚠️ **bot 正在运行时别跑这个** —— 长轮询同一个 token 只允许一个消费者，会互相抢消息。
另外超级群的 id 是负数且带 `-100` 前缀，别手滑写错。

### ⚠️ 一个必做的手工步骤

**关键词触发（能力 4）默认是不工作的。** Telegram bot 有 privacy mode，开启时 bot 在群里只能收到 `/命令` 和 @ 它的消息，普通聊天内容它看不见。

去 BotFather：

```
/setprivacy → 选择 @zuchiangmaibot → Disable
```

然后**把 bot 移出群再重新加回去**（privacy mode 的变更对已在群里的 bot 不生效）。

不改这一步，能力 1/2/3 正常，能力 4 静默失效。

---

## 三、运营怎么改内容（不用碰代码）

两个文件加一个目录，改完发 `/reload` 即生效（或在管理页上点）：

### `data/faq.md` — 问答知识库

普通 Markdown，按 `##` 分节。bot 会检索最相关的几节喂给模型，模型只能基于这些内容回答。

```markdown
## How do I join the community
<!-- also: 怎么加入 如何加入 报名 เข้าร่วม -->

Events are open — pick one on Social Layer and show up.
```

**bot 一律用英文回复**（`REPLY_LANGUAGE`），但成员会用中文、泰文提问。
BM25 是纯词面匹配，中文查询对不上英文正文，所以每条加一行
`<!-- also: ... -->` 隐藏别名：参与检索，不进答案、不给模型看。
少了它，中文提问会全部落到"我不确定"。

### `data/keywords.yaml` — 关键词触发规则

```yaml
- id: join
  match: ["how to join", "怎么加入", "如何加入"]   # 中英都写,回复统一英文
  cooldown: 3600          # 同一个群 1 小时内最多触发一次,防刷屏
  reply: |
    🙌 Welcome! All events live on <a href="https://app.sola.day/event/4seas">Social Layer</a>.
```

`cooldown` 是必填的 —— 社区 bot 最容易翻车的地方就是关键词刷屏。

---

## 四、命令

| 命令 | 谁能用 | 作用 |
|---|---|---|
| `/start` `/help` | 所有人 | 介绍和命令列表 |
| `/events` | 所有人 | 看**明天**的活动（跟 19:00 自动播报同一个窗口）；`/events 3` 看更多天 |
| `/ask <问题>` | 所有人 | 基于 FAQ 提问（有频率限制） |
| `/faq` | 所有人 | 列出 FAQ 目录 |
| `/sync` | 管理员 | 立刻从 Social Layer 导入一次活动（幂等，随便点） |
| `/report` | 管理员 | 立刻手动播报一次（明日预告） |
| `/reload` | 管理员 | 重新加载 faq.md 和 keywords.yaml |
| `/status` | 管理员 | 活动库存量、上次/下次同步、下次播报、问答用量 |

`/events` 默认跟 19:00 的自动播报**共用同一个窗口**（`DAILY_REPORT_OFFSET_DAYS`
+ `DAILY_REPORT_DAYS_AHEAD`），不另设配置 —— 有人查了 `/events` 之后又看到
19:00 播出来的是另一批活动，那会被当成 bug。

`/events` 不调 LLM（任何人都能随手发，没有频率限制），推荐语直接取主办方原文。

---

## 五、自定义命令（不用写代码）

在 `data/commands/` 放一个 YAML，群里发 `/reload`，命令立刻生效。删掉文件再 `/reload` 就移除。

```yaml
- command: wifi              # 必填 → /wifi
  description: Venue Wi-Fi   # 显示在 /help 和 Telegram 命令菜单里
  reply: |                   # 必填，Telegram HTML
    📶 <b>Wi-Fi</b>
    Network: <code>4Seas-Guest</code>
  enabled: true              # false = 保留配置但不注册
  admin_only: false
  scope: all                 # all | group | private
```

配置写错不会拖垮其它命令 —— `/reload` 会逐条报告哪个文件哪一行有问题，其余照常加载。
内置命令名（`start help events ask faq sync report reload status`）不允许被覆盖，
否则一个坏配置就能把 `/reload` 本身顶掉，你再也改不回来。

详见 [`data/commands/README.md`](data/commands/README.md)。

## 六、管理页

bot 启动时会同时起一个本地管理页，增删改停命令都能点，改完立刻生效：

```
http://127.0.0.1:8477/?token=<WEB_TOKEN>
```

启动日志里有完整链接。默认**只绑 127.0.0.1** —— 这个页面能改 bot 在 776 人群里说什么，
不该直接对公网开。远程访问走 SSH 隧道：

```bash
ssh -N -L 8477:127.0.0.1:8477 user@host
```

`WEB_TOKEN` 留空的话每次启动随机生成并打进日志（链接会变）；填了才稳定。
绑非回环地址而没显式设 token 时，管理页拒绝启动。

管理页起不来（端口占用等）**不会影响 bot 本身** —— 只记一条 error，Telegram 照常跑。

## 七、成本与安全

- **LLM 只在 `/ask` 和 @ 时调用**，关键词触发走固定模板，不烧 token。
- 每用户每小时问答次数上限，超了礼貌拒绝。
- 群白名单：不在 `TELEGRAM_ALLOWED_CHATS` 里的群，bot **静默忽略**（不是退群）。
  退群做成 opt-in（`LEAVE_UNKNOWN_CHATS`）—— 白名单少填一个 id 就自动退出正式群、
  丢掉管理员身份，代价太大。
- `TELEGRAM_MUTED_CHATS` 里的群：收消息但一句话不说。测试期把正式群放这里，
  比从白名单里删掉安全。
- 管理页默认只绑 127.0.0.1，且强制 token。
- 所有密钥走环境变量，仓库里只有 `.env.example`。

---

## 八、路线图

**一期（已完成）· Telegram**
四项核心能力 + Social Layer 活动幂等导入 + 自托管部署。

**二期 · Twitter/X**
参考 [CBots](https://github.com/jhfnetboy/CBots) 已验证的功能形态：定时发推、被 @ 时自动回复、活动同步发 X。
⚠️ 前置条件：需要 X API 凭据（当前尚未申请），且 X API 免费档限制严格，详见 [技术设计文档](docs/TECH-DESIGN.md#二期twitterx)。

**未来**
反广告入群验证（参考 [captcha-bot](https://github.com/AAStarCommunity/captcha-bot)）、活动报名提醒、社区数据看板。

---

## 九、文档

- [技术设计文档](docs/TECH-DESIGN.md) —— 架构、数据源、框架选型对比、部署方案、里程碑

## License

Apache-2.0
