<div align="center">

<img src="docs/assets/logo-200.png" width="120" alt="4Seas Bot">

# 4Seas Bot

**每天晚上七点，替社区把明天的活动讲清楚。**

[![release](https://img.shields.io/github/v/release/4seas-community/4Seas-bot?style=flat-square&color=2f6f4f)](https://github.com/4seas-community/4Seas-bot/releases)
[![tests](https://img.shields.io/badge/tests-221%20passing-2f6f4f?style=flat-square)](tests/)
[![license](https://img.shields.io/badge/license-Apache--2.0-lightgrey?style=flat-square)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square)](pyproject.toml)

[快速开始](#快速开始) · [管理页](#管理页) · [运营手册](#运营不用碰代码) · [架构](#它是怎么工作的)

</div>

---

社区运营里最耗人的从来不是「点发送」，是每天盯日历、把活动写成人愿意读的样子、回同样的问题。这个 bot 把这三件事接过去，**保留最后一步给你**：什么时候上线、对谁说话，你说了算。

跑在 [@zuchiangmaibot](https://t.me/zuchiangmaibot)，服务清迈的 4Seas 社区。活动数据来自 [Social Layer](https://app.sola.day/event/4seas)。

## 它每天做什么

<table>
<tr><td width="46%" valign="top">

**每晚 19:00 · 明日预告**

不是活动列表的转储，是一条**读得下去**的社群文案：开场一句抓住当天的调性，每场活动一句推荐，结尾呼应开场。

推荐语里**必须出现具体信息**——奖金池、剩余名额、费用、截止时间。因为「一起来玩」说了等于没说。

</td><td width="54%" valign="top">

```
Saturday, two ways to spend the day: build
something smarter for the city, or learn to
fuel your body better.

10:00–19:00｜Nimman Mini Hackathon #1
📍 Event Space - 1st Floor 4Seas Nimman
Learn from experts, build prototypes, and
pitch your solution for a chance at the
THB 25,000 prize pool.

11:00–13:00｜The Energy Table #02:
        Eat Smart · Lose Fat · Keep Energy
📍 Venue shared after registration
A small-group lunch (only 6 spots, first
come, first served)—pay for your meal
only, no extra fee.

Whether you're coding or cooking, there's
a seat for you. 🙂

Details:
https://app.sola.day/event/4seas
```

</td></tr>
</table>

| | |
|---|---|
| 🗓 **每日预告** | 每晚 19:00（Asia/Bangkok）预告明天有什么 |
| 💬 **通用问答** | `/ask` 或 @ 它，基于社区 FAQ 回答；**答不上来就说不知道，绝不编** |
| 👋 **互动响应** | 新成员欢迎、被 @ 接话、`/events` 随时查 |
| 🔑 **关键词触发** | 群里聊到常见话题自动补一条有用的，带冷却防刷屏 |
| ⚙️ **自定义命令** | 丢个 YAML 或在网页上点几下，`/wifi` 就有了 |

## 文案是怎么保证不重样的

一个被要求「写得自然一点」的模型，一周之内必然收敛回同样那几句。所以这两条不靠提示词，靠**机制**：

```
开场/结尾   从命名角度集里选 → 选中的角度落库 → 次日排除最近两天用过的
            （一天深度挡不住 A/B/A/B 交替，读一周还是能看出模板）

邀请语      「也欢迎你自己发起活动」是计数器不是语感：
            不到 4 天绝不触发，超过 6 天强制触发
            （否则它会连输几次硬币，悄悄消失几周）
```

周一到周四清爽，周五轻快，周末松弛。禁用 "chill"。次日没活动就明说没有，**绝不拿别的日期的活动来凑**。

## 快速开始

需要 Python 3.11+。

```bash
git clone git@github.com:4seas-community/4Seas-bot.git
cd 4Seas-bot
uv venv --python 3.11 && uv pip install -e ".[dev]"
cp .env.example .env        # 填 token
./start.sh                  # 前台跑，Ctrl-C 停
```

```bash
./start.sh --bg       # 后台，日志 data/bot.log
./start.sh --status   # 看状态
./start.sh --stop     # 停
```

启动会自动补一次活动导入，第一次跑完库里就有数据。`start.sh` **拒绝启动第二个实例**——Telegram 长轮询同一个 token 只允许一个消费者。

<details>
<summary><b>必填环境变量</b></summary>

```dotenv
TELEGRAM_BOT_TOKEN=          # BotFather 给的
TELEGRAM_ADMIN_IDS=          # 管理员 user id，逗号分隔
TELEGRAM_ALLOWED_CHATS=      # 白名单群 id
DEEPSEEK_API_KEY=            # 问答主力
OPENAI_API_KEY=              # 可选，兜底
SOLA_GROUP=4seas
TZ=Asia/Bangkok
```

其余都能在管理页上改。完整清单见 [`.env.example`](.env.example)。

**怎么拿群 id**：把 bot 拉进群、发一条消息，然后 `python scripts/chat_ids.py`。⚠️ bot 运行时别跑这个，会抢 update。

</details>

<details>
<summary><b>⚠️ 一个必做的手工步骤</b></summary>

**关键词触发默认不工作。** Telegram bot 的 privacy mode 开着时，群里只能收到 `/命令` 和 @ 它的消息。

BotFather → `/setprivacy` → 选中 bot → **Disable**，然后**把 bot 移出群再加回去**（对已在群里的 bot 不生效）。

不改这步，其它三项能力正常，关键词触发**静默失效**。

</details>

<details>
<summary><b>开机自启（macOS / Linux）</b></summary>

```bash
# macOS
cp deploy/com.4seas.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.4seas.bot.plist

# Linux
sudo cp deploy/4seas-bot.service /etc/systemd/system/
sudo systemctl enable --now 4seas-bot
```

**跑在笔记本上要注意**：机器睡眠时 bot 挂起。19:00 那会儿如果睡着了，当晚预告不会发，**醒来也不补发**（`run_daily` 错过就是错过）。管理页上有「立即发送预告」可以补。

</details>

## 管理页

bot 起来时同进程带一个管理页，**中英双语，默认中文**：

```
http://127.0.0.1:8477/     ← 一个密码，没有用户名
```

- **自定义命令** —— 增删改停，reply 框失焦即校验 Telegram HTML（少一个 `</code>` 整条消息会被拒收，而失败发生在群里、静默的）
- **配置** —— 15 项运行时配置，保存即生效，改时间自动重排定时任务
- **立即发送预告** —— 补发漏掉的那一晚

切换目标群时会**先确认 bot 在不在那个群**：

```
-1001242897290  →  ✓ 4Seas Community — 775 members
-1009999999999  →  ✗ 找不到这个群，请先把 bot 加进去
```

指向一个 bot 没加入的群，失败会发生在 19:00、在群里、只留一行没人看的日志。**要在人还盯着屏幕的时候就查出来。**

### 登录

设一个密码就行，用户名不需要——用这个页面的只有你：

```bash
python -m bot.web.passwd          # 随机生成一个强密码，摘要写进 .env，明文打印一次
python -m bot.web.passwd --ask    # 或者自己定
./start.sh --stop && ./start.sh --bg
```

存进 `.env` 的只有 **scrypt 摘要**（`scrypt$N$r$p$salt$hash`），盐每次随机、就编在摘要里。
**摘要刻意不写进代码**——这个仓库是公开的，写死等于把校验值发布出去让人拿回本地慢慢跑字典。

| | |
|---|---|
| 密码存储 | scrypt（N=2¹⁵），登录约 60ms，离线爆破按世纪算 |
| 登录态 | HMAC 签名的 HttpOnly + SameSite=Strict cookie，默认 7 天 |
| 改密码 | 已发出的会话**全部立即失效**（签名密钥拌了密码摘要） |
| 试密码 | 5 分钟内失败 8 次就锁，走隧道时会连自己一起锁——刻意的 |
| `?token=` | 仍然可用，留给 curl / 脚本；人用密码，机器用 token |

> 默认只绑 `127.0.0.1`。远程访问走 SSH 隧道：`ssh -N -L 8477:127.0.0.1:8477 user@host`。
> 真要挂域名，`WEB_ALLOWED_HOSTS` 必须填上那个域名——默认只认回环 Host 头，防的是
> DNS rebinding：恶意域名可以解析到 `127.0.0.1`，再带着你浏览器里的 cookie 打这个端口。
> **密钥刻意不能在页面上改**——能改 bot token 的网页表单，等于把 localhost 页面变成凭据库。

## 运营不用碰代码

| 文件 | 管什么 | 怎么生效 |
|---|---|---|
| `data/faq.md` | 问答知识库 | `/reload` |
| `data/keywords.yaml` | 关键词规则 | `/reload` |
| `data/commands/*.yaml` | 自定义命令 | `/reload` 或管理页 |

<details>
<summary><b>FAQ 里的隐藏检索别名</b></summary>

bot 一律用英文回答，但成员会用中文、泰文提问。检索是词面匹配（BM25），中文查询对不上英文正文，所以每条加一行隐藏别名：

```markdown
## How do I join the community
<!-- also: 怎么加入 如何加入 报名 เข้าร่วม -->

Events are open — pick one on Social Layer and show up.
```

参与检索，不进答案、不给模型看。**少了它，中文提问会全部落到「我不确定」。**

</details>

<details>
<summary><b>自定义命令</b></summary>

```yaml
- command: wifi              # → /wifi
  description: 场地 Wi-Fi
  reply: |
    📶 <b>Wi-Fi</b>
    Network: <code>4Seas-Guest</code>
  enabled: true
  admin_only: false
  scope: all                 # all | group | private
```

配置写错不会拖垮其它命令——逐条报告哪个文件哪一行有问题，其余照常加载。内置命令名不允许被覆盖，否则一个坏配置就能把 `/reload` 本身顶掉，你再也改不回来。

</details>

## 它是怎么工作的

```
Social Layer          08:30 / 18:30            本地 SQLite            19:00
api.sola.day    ────── 导入 ──────▶   events 表（幂等，无重复）  ────▶  群内预告
未来 60 天              + 详情补齐              ▲              读库
                                                │
                                       /events  管理页  问答
```

**导入是幂等的**，三层保证：主键 `(source, event_id)` + UPSERT、`content_hash` 判断内容是否真的变过、上游取消的打软删除而非物理删除。跑 100 次和跑 1 次库状态完全一致。

晚上 18:30 那次导入是专门给 19:00 兜底的——白天新加的活动能赶上当晚的预告。

<details>
<summary><b>数据源：从开源前端里挖出来的</b></summary>

sola.day 没有公开 API 文档。端点是从它的开源前端 [`sociallayer-im/seastar-app`](https://github.com/sociallayer-im/seastar-app) 的 `packages/sola-sdk` 源码里读出来的：

```
列表  GET /api/v1/events?group_id=4seas&collection=upcoming
详情  GET /api/v1/events/{id}          ← venue 和 content 只有这里有
iCal  GET /api/v1/groups/4seas/calendar.ics   ← 降级用
```

**契约随时可能变**，所以字段缺失一律容错，单个活动解析失败只跳过它，不让整次导入挂掉。三级降级：`Sola API → iCal → 本地 YAML`。

</details>

<details>
<summary><b>安全上认真对待的几件事</b></summary>

**Prompt 注入** —— 任何人都能在 sola.day 上建活动，描述会进 LLM 的 prompt。两层防御：提示词里把活动内容标为不可信数据，输出侧 `strip_links()` 硬过滤。
`esc()` 不够——**Telegram 对纯文本 URL 和 @handle 会自动加链接**，转义挡不住。显示名也一样过滤：改个名进群，bot 会替你把链接播给全群。

**退群做成 opt-in** —— 非白名单群默认只静默忽略。白名单少填一个 id 就自动退出正式群、丢掉管理员身份，代价太大。

**静默名单** —— 已加入但暂时不希望它开口的群，收消息、不说话。测试期把正式群放这儿，比从白名单里删掉安全。

**发错比不发严重** —— 读不到数据时不发、不标记已播、只告警。发一条假的「明天没有活动」比沉默糟糕得多。

</details>

## 命令

| 命令 | 谁能用 | 作用 |
|---|---|---|
| `/start` `/help` | 所有人 | 介绍和命令列表 |
| `/events` | 所有人 | 看明天的活动（`/events 3` 看更多天） |
| `/ask <问题>` | 所有人 | 基于 FAQ 提问 |
| `/faq` | 所有人 | 列出 FAQ 目录 |
| `/sync` | 管理员 | 立刻导入一次（幂等，随便点） |
| `/reload` | 管理员 | 重载 FAQ、关键词、自定义命令 |
| `/status` | 管理员 | 运行状态 |

管理员命令对非管理员**不显示也不响应**。判定只看 `.env` 里的 user id 白名单，与「群管理员」无关。

## 开发

```bash
uv pip install -e ".[dev]"
python -m pytest -q          # 221 passing
```

不需要 `.env`、不需要任何密钥——`tests/conftest.py` 会填占位配置并显式关掉 `.env`，保证本机和 CI 跑的是同一套。

## 路线图

- [x] Telegram：每日预告、问答、互动、关键词
- [x] 管理页：命令管理 + 运行时配置 + 中英双语 + 密码登录
- [ ] 接本地模型（`Qwen3.6-35B-A3B` MLX，实测质量与 DeepSeek 打平，零成本、数据不出机器）
- [ ] Twitter/X：定时发推、活动同步（**前置**：X API 凭据；免费档大概率读不了 mentions）
- [ ] 入群验证反广告

---

<div align="center">
<sub>Apache-2.0 · 自托管 · 数据留在你自己的机器上</sub><br>
<sub>logo 由本地 FLUX.2 Klein 生成，没有调用任何云端服务</sub>
</div>
