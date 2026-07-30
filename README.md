# 4Seas Bot

> 4Seas 社区的开源运营机器人。每日播报社区活动、回答常见问题、响应互动、关键词主动触达。
> 自托管、Apache-2.0、数据留在你自己的机器上。

Telegram: [@zuchiangmaibot](https://t.me/zuchiangmaibot) · 活动数据来自 [Social Layer / 4Seas](https://app.sola.day/event/4seas)

---

## 一、它做什么

社区运营里重复度最高的四件事，交给它：

| # | 能力 | 说明 |
|---|---|---|
| 1 | **每日活动播报** | 每天早上 9:00（Asia/Bangkok）自动把当天和未来几天的社区活动推到群里 |
| 2 | **通用问答** | `/ask` 或 @ 它，基于社区 FAQ 知识库回答；答不上来的老实说答不上来，不编 |
| 3 | **互动响应** | 新成员欢迎、被回复时接话、常用命令（`/events` `/faq` `/help`） |
| 4 | **关键词主动触发** | 群里出现「住宿」「签证」「怎么报名」这类词，自动补一条有用的信息 |

活动数据不用手填 —— 运营在 sola.day 建活动，bot 自动就播报了。

### 活动是怎么进来的

```
Social Layer          每天 08:30           本地 SQLite         每天 09:00
api.sola.day    ──导入──▶   events 表   ──读库──▶   群内播报
(未来 60 天)                (幂等,无重复)
```

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
.venv/bin/python -m bot      # 长轮询启动
```

启动时会自动补一次活动导入，所以第一次跑完库里就有数据了。

### 必填环境变量

```dotenv
TELEGRAM_BOT_TOKEN=          # BotFather 给的 token
TELEGRAM_ADMIN_IDS=          # 管理员 user id,逗号分隔
TELEGRAM_ALLOWED_CHATS=      # 允许 bot 工作的群 id,逗号分隔(白名单,防止被拉进乱七八糟的群)
DEEPSEEK_API_KEY=            # 问答用,主力模型
OPENAI_API_KEY=              # 可选,DeepSeek 挂了时兜底
SOLA_GROUP=4seas             # Social Layer 上的社区标识
SYNC_TIME=08:30              # 每天几点导入活动(比播报早半小时)
SYNC_HORIZON_DAYS=60         # 一次导入未来多少天
DAILY_REPORT_TIME=09:00      # 播报时间
DAILY_REPORT_DAYS_AHEAD=0    # 0=只播当天;3=当天+未来3天
TZ=Asia/Bangkok
```

改 `DAILY_REPORT_DAYS_AHEAD` 不需要重新导入 —— 库里存的是未来 60 天，
播报只是换个查询窗口。完整配置项见 [`.env.example`](.env.example)。

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

两个文件，改完 bot 自动热加载：

### `data/faq.md` — 问答知识库

普通 Markdown，按 `##` 分节。bot 会检索最相关的几节喂给模型，模型只能基于这些内容回答。

```markdown
## 怎么加入 4Seas 社区？
访问 https://app.sola.day/event/4seas ,或直接在群里问管理员。

## 清迈的住宿怎么安排？
Residency 期间提供 co-living,详见 ...
```

### `data/keywords.yaml` — 关键词触发规则

```yaml
- id: visa
  match: ["签证", "visa", "落地签"]
  cooldown: 3600          # 同一个群 1 小时内最多触发一次,防刷屏
  reply: |
    🛂 泰国签证相关整理在这里:<链接>
    有具体问题可以 @管理员

- id: housing
  match: ["住宿", "housing", "co-living"]
  cooldown: 3600
  reply: "🏠 住宿安排见 ..."
```

`cooldown` 是必填的 —— 社区 bot 最容易翻车的地方就是关键词刷屏。

---

## 四、命令

| 命令 | 谁能用 | 作用 |
|---|---|---|
| `/start` `/help` | 所有人 | 介绍和命令列表 |
| `/events` | 所有人 | 手动拉一次近期活动 |
| `/ask <问题>` | 所有人 | 基于 FAQ 提问（有频率限制） |
| `/faq` | 所有人 | 列出 FAQ 目录 |
| `/sync` | 管理员 | 立刻从 Social Layer 导入一次活动（幂等，随便点） |
| `/report` | 管理员 | 立刻手动触发一次每日播报 |
| `/reload` | 管理员 | 重新加载 faq.md 和 keywords.yaml |
| `/status` | 管理员 | 活动库存量、上次/下次同步、下次播报、问答用量 |

`/events` 也可以带参数：`/events 7` 看未来 7 天（直接查库，不打上游）。

---

## 五、成本与安全

- **LLM 只在 `/ask` 和 @ 时调用**，关键词触发走固定模板，不烧 token。
- 每用户每小时问答次数上限，超了礼貌拒绝。
- 群白名单：不在 `TELEGRAM_ALLOWED_CHATS` 里的群，bot 自动退出。
- 所有密钥走环境变量，仓库里只有 `.env.example`。

---

## 六、路线图

**一期（当前）· Telegram**
四项核心能力 + Social Layer 活动接入 + 自托管部署。

**二期 · Twitter/X**
参考 [CBots](https://github.com/jhfnetboy/CBots) 已验证的功能形态：定时发推、被 @ 时自动回复、活动同步发 X。
⚠️ 前置条件：需要 X API 凭据（当前尚未申请），且 X API 免费档限制严格，详见 [技术设计文档](docs/TECH-DESIGN.md#二期twitterx)。

**未来**
反广告入群验证（参考 [captcha-bot](https://github.com/AAStarCommunity/captcha-bot)）、活动报名提醒、社区数据看板。

---

## 七、文档

- [技术设计文档](docs/TECH-DESIGN.md) —— 架构、数据源、框架选型对比、部署方案、里程碑

## License

Apache-2.0
