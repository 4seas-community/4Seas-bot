# 4Seas Bot — 技术设计文档

状态：草案 · 2026-07-30 · 待评审

---

## 1. 目标与范围

一期交付一个 Telegram 社区机器人，覆盖四项能力：

1. **每日活动播报**（daily events report）
2. **通用问答**（answer general questions）
3. **互动响应**（response to interactions）
4. **关键词主动触发**（proactive response to keywords trigger）

二期扩展到 Twitter/X（见 §8）。

**非目标（一期明确不做）**：入群验证/反广告、活动报名与票务、多语言自动翻译、Web 管理后台。

---

## 2. 框架选型

### 2.1 候选对比

数据采集于 2026-07-30，来自 GitHub / PyPI / npm 官方 API：

| 框架 | 语言 | Stars | 最新版本 | 最后活动 | 内建定时器 | 结论 |
|---|---|---|---|---|---|---|
| **python-telegram-bot 22.8** | Python | 29,364 | 22.8 (2026-06-12) | 2026-07-28 | ✅ JobQueue | ✅ **采用** |
| aiogram 3.30.0 | Python | 5,810 | 3.30.0 (2026-07-17) | 2026-07-28 | ❌ | 备选 |
| grammY 1.45.1 | TypeScript | 3,694 | 1.45.1 (2026-07-17) | 2026-07-30 | ❌ | 备选（Serverless） |
| Telegraf 4.16.3 | TypeScript | 9,177 | 4.16.3 (**2024-02-29**) | 2025-01-11 | ❌ | ❌ 排除 |
| Telethon | Python | 12,069 | — | **仓库已归档** | ❌ | ❌ 排除 |
| teloxide | Rust | 4,195 | — | 2026-07-28 | ❌ | ❌ 排除 |

### 2.2 排除理由

- **Telegraf**：最后一个正式版本停在 2024-02，代码库最后一次推送 2025-01。事实停更约 2.5 年，Telegram Bot API 期间已迭代多个版本，不能作为长期依赖。
- **Telethon**：GitHub 仓库已归档（2026-02）。且它是 MTProto **userbot** 库，需要用个人账号登录，有账号被封风险。现有 `AAStarCommunity/CBots` 正是基于 Telethon —— 这本身就是必须换掉的理由。
- **teloxide**：活跃且类型安全，但社区 bot 的特点是运营需求高频变更、要频繁接各种 API。Rust 的迭代速度和团队协作门槛不匹配这个场景。

### 2.3 采用 python-telegram-bot 的理由

1. **唯一内建调度器的主流框架。** 能力 1（每日播报）用 `JobQueue.run_daily()` 直接实现，原生带时区支持：

   ```python
   app.job_queue.run_daily(
       daily_report,
       time=dt.time(9, 0, tzinfo=ZoneInfo("Asia/Bangkok")),
       chat_id=TARGET_CHAT,
   )
   ```

   aiogram / grammY 都需要额外接入 APScheduler 并自行处理持久化和时区。

2. **能力 3、4 有现成组件。** `MessageHandler(filters.Regex(...))` 覆盖关键词触发；`ConversationHandler` 覆盖多轮交互。四项能力中三项半是框架原生能力，而不是自己造。

3. **生态规模。** 29k stars，是 aiogram 的 5 倍。能力 2 要接 LLM、要做检索，Python 侧的库最成熟。

4. **既有约束匹配。** 仓库已初始化为 Python（`.gitignore` 是 Python 模板）+ Apache-2.0；`jhfnetboy/CBots` 已经历过一次 Telethon → PTB 的迁移，团队有经验。

### 2.4 关于复用 CBots 代码

**结论：不复用代码，复用功能清单与运维经验。**

`jhfnetboy/CBots` 共 3133 行 Python，其中混合了 Telethon、Flask、Quart、tweepy 四套栈，并存在 `main.py.bak`、`history/`、`bak/` 等历史目录。迁移和清理的成本高于按新架构重写。

值得继承的部分：
- 每日密码反广告的产品设计（挪到未来路线图）
- 自定义命令的配置化思路
- systemd / launchd 部署脚本形态（`cbots.service`、`com.cbots.service.plist`）

---

## 3. 数据源：Social Layer

4Seas 的活动发布在 Social Layer（sola.day）：<https://app.sola.day/event/4seas>

sola.day 前端 [`sociallayer-im/seastar-app`](https://github.com/sociallayer-im/seastar-app) 开源，其中 `packages/sola-sdk` 是官方 SDK。从中提取出两个**公开、免认证**的端点，均已实测通过：

### 3.1 JSON API（主用）

```
GET https://api.sola.day/api/v1/events?group_id=4seas&collection=upcoming&limit=100
```

实测（2026-07-30）返回 `200 application/json`，结构为 `{"data": [Event, ...]}`。字段包含 `id`、`title`、`start_time`、`end_time`、`location`、`group`、`owner`、`participant_count`、`image_url`、`meeting_url` 等。

`collection` 取值：`upcoming` | `past`。`group_id` 接受 group 名称（`4seas`）而非仅 ID。

样本输出：

```
2026-07-29T17:00:00Z | Language Corner 很高兴认识你，一起学中文！
2026-07-30T11:00:00Z | Build your AI Co-Founder
2026-07-31T04:00:00Z | Language & Culture Exchange
```

### 3.2 iCal 订阅（备用 / 交叉校验）

```
GET https://api.sola.day/api/v1/groups/4seas/calendar.ics
```

实测返回 `200 text/calendar`，377 KB，**429 个 VEVENT**，`X-WR-CALNAME: 4Seas Community — Social Layer`，时区 `Asia/Bangkok`，`REFRESH-INTERVAL: PT1H`。

### 3.3 选型与降级链

主用 JSON API：字段结构化，便于排版和过滤。iCal 是全量历史（429 条），每天解析 377 KB 浪费。

事件源设计为**可插拔适配器**，降级链：

```
SolaApiSource  ──失败──▶  SolaIcsSource  ──失败──▶  LocalYamlSource  ──▶  记录失败并私信管理员
```

`LocalYamlSource` 读 `data/events.yaml`，承担两个职责：所有上游都挂时的兜底，以及补充 sola.day 上没有的线下活动。

**注意**：sola.day 未提供正式 API 文档，端点信息来自开源 SDK 源码。契约可能变更 —— 因此 `services/events.py` 对字段缺失一律容错，单个事件解析失败只跳过它，不让整次同步挂掉（见 `tests/test_events.py` 里的 `test_unknown_fields_do_not_break_parsing`）。

### 3.4 导入落库与幂等

**不在播报时直接打上游**。定时任务把活动导入本地 SQLite，播报只读库。这样上游抖动不会影响播报，改播报范围也不需要重新拉数据。

```
Social Layer            每天 08:30            SQLite events 表        每天 09:00
api.sola.day     ──── sync_events ────▶   (source, event_id) 唯一   ──── 读库 ────▶  群内播报
未来 SYNC_HORIZON_DAYS 天                                              查询窗口 = DAILY_REPORT_DAYS_AHEAD
```

导入时间比播报早 30 分钟，保证播报读到的是当天最新数据。启动时额外补一次（`SYNC_ON_STARTUP`），避免刚部署完库是空的。

**幂等的三层保证**：

| 层 | 机制 | 解决什么 |
|---|---|---|
| 1 | `PRIMARY KEY (source, event_id)` + `ON CONFLICT DO UPDATE` | 同一活动重复导入只有一行 |
| 2 | `content_hash` | 内容没变就不动 `updated_at`，"这条改过没有"始终可查 |
| 3 | 窗口内软删除（`deleted_at`） | 上游取消的活动下架而非物理删除，恢复时自动复活 |

因此同步任务可以任意频率重复执行 —— 跑 100 次和跑 1 次的库状态完全一致。管理员的 `/sync` 命令随便点也不会产生脏数据。

三个刻意的设计取舍：

- **`participant_count` 不参与 `content_hash`。** 报名人数天天变，算进去会让每次同步都判定为"内容变了"，`updated_at` 就失去意义。但新的人数照常落库。
- **软删除只在同步窗口内对账。** 窗口外的历史数据不动，否则把 horizon 从 60 天改成 7 天会误删一大批。
- **上游返回空不等于全部取消。** 只有带 window 参数的同步才做下架对账；空结果不会清空表。

实测（2026-07-30，真实端点）：首次导入 79 条（60 天窗口，从 197 条 upcoming 里筛出），连续再跑两次均为「新增 0 · 更新 0 · 无变化 79」，行数不变。

同步结果写 `sync_log` 表，`/status` 直接展示最近一次的 拉取/新增/更新/无变化/下架 五个数。

---

## 4. 架构

```
4Seas-bot/
├── bot/
│   ├── __main__.py           # Application 构建、handler 注册、job 调度、启动
│   ├── config.py             # pydantic-settings；.env → 强类型配置
│   ├── models.py             # Event —— 跨数据源的统一模型
│   ├── render.py             # Event → Telegram HTML 消息
│   ├── storage.py            # SQLite：活动库(幂等 UPSERT)、同步日志、冷却、用量
│   ├── deps.py               # 进程级单例，便于 /reload 与测试替换
│   ├── handlers/
│   │   ├── commands.py       # /start /help /events /ask /faq + 管理命令
│   │   ├── interactions.py   # 能力3、4：欢迎、被 @、关键词触发
│   │   └── errors.py         # 群白名单守卫 + 全局 error handler
│   ├── jobs/
│   │   ├── sync_events.py    # 定时导入（幂等）
│   │   └── daily_report.py   # 能力1：读库 → 渲染 → 发送
│   └── services/
│       ├── events.py         # 三个事件源适配器 + 降级链
│       ├── keywords.py       # keywords.yaml → 编译后的正则规则
│       ├── kb.py             # FAQ 切分 + BM25 检索
│       └── llm.py            # 能力2：DeepSeek 主 / OpenAI 兜底
├── data/
│   ├── faq.md                # 运营维护，/reload 热加载
│   ├── keywords.yaml         # 运营维护，/reload 热加载
│   └── events.yaml           # 补充活动 / 最后兜底
├── docs/ · tests/ · .env.example · pyproject.toml
```

Handler 用 PTB 的 group 机制分优先级：

| group | 内容 |
|---|---|
| -1 | 群白名单守卫（非白名单群直接退群，抛 `ApplicationHandlerStop`） |
| 0 | 命令 + 新成员欢迎 |
| 1 | 被 @ / 被回复 → 问答（命中后抛 `ApplicationHandlerStop`，阻断 group 2） |
| 2 | 关键词主动触发 |

group 1 命中后阻断 group 2，是为了避免同一条消息既被当成提问回答、又被关键词规则二次回复。

### 4.1 四项能力的实现要点

**能力 1 · 每日播报**

`JobQueue.run_daily` 在 `DAILY_REPORT_TIME`（默认 09:00 Asia/Bangkok）触发，从库里查 `[今天 00:00, 今天+DAILY_REPORT_DAYS_AHEAD 23:59]` 窗口内的活动。当前配置 `DAILY_REPORT_DAYS_AHEAD=0`，即只播当天。

查询条件是 `start_ts <= 窗口末 AND COALESCE(end_ts, start_ts) >= 窗口初`，即"与窗口有交集"而不是"开始于窗口内" —— 这样昨天开始、今天还在进行的跨天活动也会出现在今天的播报里。

渲染用 `parse_mode=HTML` 而不是 MarkdownV2：后者要求转义 `_*[]()~>#+-=|{}.!`，而活动标题里这些字符满地都是（实测有中泰英混排、`&`、`#` 等），漏转义一个就整条消息发不出去。所有用户内容过 `html.escape`。

超长消息（>4096 字符）按行截断，保证不把 HTML 标签劈成两半 —— 测试断言 `<b>` 与 `</b>` 数量相等。

去重：`report_log` 记录已播报的 `(chat_id, date)`，进程重启不会同日重播；`/report` 可强制重发。

**能力 2 · 通用问答**

不做向量数据库。`faq.md` 按 `##` 切分为若干段，用 BM25（`rank_bm25`，纯 Python 无外部服务）检索 Top-3 段落注入 prompt。系统提示词明确约束：**只能基于给定片段回答，无相关内容时回答"我不知道，建议问管理员"**。

社区 FAQ 通常只有几十条，BM25 的召回质量足够，且省掉了 embedding 调用和向量库运维。规模超过 ~200 条时再考虑升级。

模型：DeepSeek 为主，OpenAI 兜底。两者都走 OpenAI 兼容接口，切换只改 `base_url` + `model`。

**能力 3 · 互动响应**

- `ChatMemberHandler` 处理新成员入群 → 欢迎语 + 引导看 `/help`
- `filters.REPLY & filters.Entity("mention")` → 被 @ 时走问答链路
- 命令响应统一加超时保护，LLM 调用前先 `send_chat_action("typing")`

**能力 4 · 关键词触发**

`keywords.yaml` 编译成一组 `MessageHandler(filters.Regex(pattern), group=N)`。用 PTB 的 handler `group` 机制放在较低优先级，确保命令和问答先匹配。

每条规则强制 `cooldown`（秒），以 `(chat_id, rule_id)` 为键记录在 SQLite。这是防刷屏的硬约束。

**⚠️ 前置条件**：Bot 当前 `getMe` 返回 `can_read_all_group_messages: false`，即 privacy mode 开启，群内普通消息收不到。必须在 BotFather 执行 `/setprivacy → Disable`，并将 bot 移出群后重新加入才生效。**此步骤未完成时能力 4 静默失效**。

---

## 5. 配置与密钥

密钥统一走环境变量，仓库内只提交 `.env.example`。

已确认可用的凭据（位于 `~/Dev/.env`，部署时需映射到本项目变量名）：

| 本项目变量 | `~/Dev/.env` 中的来源 | 状态 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `4SEA_TELE_BOT_TOKEN` | ✅ 已验证，`@zuchiangmaibot` (id 8674534389) |
| `TELEGRAM_ADMIN_IDS` | `TELE_BOT_OWNER_USER_ID` | ✅ 715297407 |
| `DEEPSEEK_API_KEY` | `DEEP_SEEK_API_LKEY` | ✅ 存在 |
| `OPENAI_API_KEY` | `OPENAI_API_KEY` | ✅ 存在 |
| `TELEGRAM_ALLOWED_CHATS` | — | ⏳ 需提供目标群 id |

`TELEGRAM_ALLOWED_CHATS` 是唯一缺失项，需要把 bot 加进目标群后从日志读取 chat id。

---

## 6. 部署

一期采用**长轮询（polling）+ systemd**：不需要公网 IP、域名或 TLS 证书，运维面最小。

```ini
# /etc/systemd/system/4seas-bot.service
[Service]
Type=simple
WorkingDirectory=/opt/4Seas-bot
EnvironmentFile=/opt/4Seas-bot/.env
ExecStart=/opt/4Seas-bot/.venv/bin/python -m bot
Restart=always
RestartSec=10
```

同时提供 `Dockerfile` + `docker-compose.yml` 作为替代。SQLite 文件挂载到宿主机持久化。

日志到 stdout，由 journald 收集。全局 error handler 捕获未处理异常，写日志并私信管理员。

**不上 webhook / Serverless 的理由**：JobQueue 依赖常驻进程。Cloudflare Workers 那条路要把定时改成 Cron Triggers、状态改成 KV/D1，属于架构级改动，一期不做。

---

## 7. 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| **M0** | 骨架：config、命令、白名单守卫、日志、错误告警 | ✅ 已完成 |
| **M1** | 能力 1：三个事件源 + 幂等导入 + JobQueue 播报 | ✅ 已完成，真实数据验证通过 |
| **M2** | 能力 4：keywords.yaml + 冷却 + 热加载 | ✅ 已完成 |
| **M3** | 能力 2 + 3：BM25 检索 + DeepSeek + 欢迎语 + 限流 | ✅ 已完成 |
| **M4** | 运营化：`/sync` `/status`、systemd、测试、文档 | 🔶 测试与文档已完成，systemd 部署待上机 |

前置条件 privacy mode 已于 2026-07-30 关闭（`getMe.can_read_all_group_messages = True`），能力 4 具备生效条件。

**已验证**：

- 30 个单元测试通过（渲染、时间窗、幂等、软删除、字段往返）
- 真实端点导入 79 条活动，连续三次同步行数不变
- bot 启动、两个定时任务正常调度、启动补同步执行成功
- 播报消息真实投递成功（发到管理员私聊，未发群）

**未验证**：群内的关键词触发、新成员欢迎、`/ask` 问答 —— 这几项需要 bot 在群里实际收发消息才能确认。

---

## 8. 二期：Twitter/X

参考 `jhfnetboy/CBots` 中 `twitter_core.py` / `twitter_api.py` 已验证的功能形态（tweepy + X API v2 Client）：

| 能力 | 说明 | CBots 中的状态 |
|---|---|---|
| 定时发推 | 每日把社区活动同步发到 X | 手动可用，定时未完成 |
| 被 @ 自动回复 | 有人 @ 账号时基于 FAQ 回复 | 尝试集成 AI，未完成 |
| 活动同步 | Telegram 播报的同一份内容同步到 X | 新增 |

复用一期已有的 `services/events.py` 和 `services/llm.py`，只新增 `channels/twitter/` 适配层 —— 这也是一期把「事件获取」和「渲染/发送」分离的原因。

### 8.1 阻塞项与风险

1. **凭据缺失。** `~/Dev/.env` 中当前没有任何 `TWITTER_*` / `X_API_*` 变量（已确认为 0 项）。需要先在 X Developer Portal 申请，拿到 API Key/Secret、Access Token/Secret、Bearer Token 共 5 个值。

2. **X API 免费档限制严格。** 免费档基本只支持有限的写入配额，**读取（拉取 mentions）通常不在免费档范围内**。也就是说「被 @ 自动回复」这项能力大概率需要付费档。CBots 当年这个功能"无法使用"，很可能正是撞在这里。

   → **二期启动前必须先确认账号档位**，否则会重复 CBots 的失败路径。

3. **建议的二期切分**：先做单向的「定时发推 / 活动同步」（只需写权限，免费档可行），把「被 @ 回复」拆成独立的 2.2 期，等档位确认后再动。

---

## 9. 待确认

- [ ] 目标群的 chat id（用于 `TELEGRAM_ALLOWED_CHATS` 和播报目标）
- [ ] 播报内容形态：只列标题+时间，还是带地点、封面图、报名链接？
- [ ] 播报范围：仅当天，还是「今天 + 未来 3 天」？
- [ ] `data/faq.md` 的初始内容由谁提供
- [ ] 二期：X 账号是否已有付费档，或计划升级
