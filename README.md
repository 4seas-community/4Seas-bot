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

活动数据不用手填 —— 直接读 4Seas 在 Social Layer 上的公开日历，运营在 sola.day 建活动，bot 自动就播报了。

---

## 二、快速开始

需要 Python 3.11+。

```bash
git clone git@github.com:4seas-community/4Seas-bot.git
cd 4Seas-bot
uv sync                      # 或 pip install -e .
cp .env.example .env         # 填 token,见下
python -m bot                # 长轮询启动
```

### 必填环境变量

```dotenv
TELEGRAM_BOT_TOKEN=          # BotFather 给的 token
TELEGRAM_ADMIN_IDS=          # 管理员 user id,逗号分隔
TELEGRAM_ALLOWED_CHATS=      # 允许 bot 工作的群 id,逗号分隔(白名单,防止被拉进乱七八糟的群)
DEEPSEEK_API_KEY=            # 问答用,主力模型
OPENAI_API_KEY=              # 可选,DeepSeek 挂了时兜底
SOLA_GROUP=4seas             # Social Layer 上的社区标识
DAILY_REPORT_TIME=09:00      # 播报时间
TZ=Asia/Bangkok
```

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
| `/report` | 管理员 | 立刻手动触发一次每日播报 |
| `/reload` | 管理员 | 重新加载 faq.md 和 keywords.yaml |
| `/status` | 管理员 | 查看运行状态、下次播报时间、今日 LLM 调用量 |

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
