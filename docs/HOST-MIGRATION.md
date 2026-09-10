# 4Seas Bot — 换主机手册

状态：已实施 · 2026-09-10 · 依据一次真实迁移整理

把 bot 从一台 Linux 云主机迁到一台常驻 macOS 主机的完整流程。写成手册而不是流水账，
是因为换主机这件事以后还会发生，而其中大部分坑跟具体是哪两台机器无关。

本文不含任何主机地址、群组 ID 或凭据 —— 需要这些的时候去查对应主机的 runbook。

---

## 1. 唯一不能违反的约束

**同一个 Telegram token 只允许有一个长轮询实例。**

两个实例同时 `getUpdates`，Telegram 会给其中一个返回 409 Conflict，两边互相抢，
消息随机丢给谁不确定。所以整个迁移的形状是被这条约束决定的：

> 新主机上除了「启动长轮询」之外的一切，都可以在旧主机仍在服务时完成并验证。
> 真正的切换是一个瞬间动作：停旧 → 同步数据 → 起新。

不要为了"先测一下"而在旧实例还活着的时候把新实例拉起来。

---

## 2. 目录布局

新旧主机用同一套布局，这样运维直觉可以迁移，回滚也对称：

```
<root>/
  releases/<UTC 时间戳>-<commit sha>/   # 不可变。.env -> ../../env，data -> ../../shared/data
  current -> releases/<...>             # 原子切换靠改这个软链
  shared/data/                          # SQLite + YAML 配置，跨 release 存活
  env                                   # 0600
  backups/
  logs/
```

要点：**代码不可变，数据在代码之外**。发布是建新目录 + 改软链，不是就地覆盖；
上一个 release 永远保留作回滚点。

---

## 3. 流程

### 阶段一：准备（旧主机照常服务，零影响）

1. **确认目标解释器**。本项目 `requires-python >= 3.11`。macOS 自带的是 3.9，
   不满足；用 `uv` 拉一个托管解释器，不要指向 `/usr/bin/python3`。

2. **投放代码，锁定 commit**。用 `git archive <sha> | ssh <host> tar -x` 而不是在
   目标机上 clone —— 前者精确到 commit，也不需要目标机有仓库凭据。

   > 迁移时**不要顺带升级代码**。换主机已经改了一个变量，再叠加代码变更，
   > 出问题就无法归因。升版本留作切换稳定后的独立一步。

3. **接上软链**。仓库自带一个 `data/` 目录，直接 `ln -s ... data` 会把软链建到它
   *内部*去。先把它挪开：

   ```bash
   mv data data.seed && ln -sfn ../../shared/data data
   ln -sfn ../../env .env
   ```

4. **装依赖**：`uv sync --extra dev --locked`。

5. **搬 env**，只改绑定相关的项，其余逐字保留。用管道直传，别落地中间文件：

   ```bash
   ssh <old> 'cat <root>/env' | sed -E 's|^WEB_HOST=.*|WEB_HOST=<new>|' \
     | ssh <new> 'umask 077 && cat > <root>/env'
   ```

6. **预置数据**。SQLite 必须用在线备份，不能 `cp` —— 旧库还在写，WAL 会让直接
   复制拿到不一致的快照：

   ```python
   src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
   dst = sqlite3.connect(out)
   src.backup(dst)                                  # 在线一致性快照
   dst.execute("PRAGMA integrity_check").fetchone() # 必须是 ok
   ```

   `shared/data/` 下的 YAML / Markdown / `session_secret` 一并带走。

### 阶段二：预验证（仍然不碰 token）

7. **跑测试**：`uv run pytest -q`。基线见 README。

8. **单独验管理页**。`AdminServer.start()` 不依赖 Telegram，可以脱离 bot 起来，
   这样能在不抢 token 的前提下验证绑定和登录：

   ```python
   srv = AdminServer(None)   # manager 只在请求处理里用到，绑定阶段用不上
   await srv.start()
   ```

   要验三件事，缺一不可：
   - 目标地址 + 端口能绑上
   - 伪造 Host 打 `/api/login` 返回 **400 bad_host**
   - 真实密码登录返回 200 并下发 cookie，带 cookie 取受保护 API 能拿到数据

   第三条才证明 `WEB_PASSWORD_HASH` 搬对了；只看首页返回 200 是不够的（见 §4.2）。

9. 验完**务必停掉探针**，释放端口。

### 阶段三：切换

10. **停旧主机的服务，并禁用开机自启**。只 stop 不 disable，旧主机哪天重启就会
    冒出第二个实例，而这种冲突往往过很久才被发现。

11. **精确复查旧实例已死**。注意 `pgrep -af` 会匹配到你自己那条命令行，
    用 `ps -eo pid,args | grep -E "[p]attern"` 之类的写法避免自匹配。

12. **最终数据同步**。此刻旧库已静止，这一次拿到的才是精确快照。
    阶段一那次只是预置，用来缩短切换窗口。

13. **起新实例**，让进程管理器托管（launchd / systemd），不要裸跑。

### 阶段四：验证

14. 逐项确认，全部通过才算完成：

    - [ ] 日志里 **Conflict 计数为 0**
    - [ ] 无非预期的 ERROR
    - [ ] 启动同步端到端跑通（证明外网 API 与 DB 写入都正常）
    - [ ] `getWebhookInfo` 的 `pending_update_count` 为 0、`last_error` 为空
    - [ ] 管理页能登录，且读到的是迁移过来的真实数据
    - [ ] 旧主机服务确为 inactive 且 disabled
    - [ ] Telegram 里实际发一条命令，bot 有响应

15. **在旧主机上留一张字条**，写明迁去了哪、什么时候、怎么回滚。几个月后再登上
    那台机器的人（很可能是你自己）需要它。

---

## 4. 这次踩到的坑

### 4.1 非回环绑定有凭据门槛

`WEB_HOST` 不是回环地址时，若 `WEB_PASSWORD_HASH` 和 `WEB_TOKEN` 都为空，
`AdminServer.start()` 会**拒绝绑定并只记一条日志** —— bot 本身照常运行。
所以现象是"bot 好好的，就是管理页打不开"，不看日志会找错方向。

### 4.2 Host 校验的作用域比想象的窄

`_host_ok()` 只约束**发放和接受 session cookie**（即 `/api/login` 和后续带 cookie 的
请求），登录页 HTML 对任何 Host 都返回 200。这是设计如此：防的是 DNS rebinding
拿你的 cookie，而不是防止别人看到登录框。

因此 **用"首页返回 200"来验证配置正确是无效的**，必须真的走一次登录。

另外 `WEB_HOST` 本身会被自动加进 allowed hosts，绑到某个地址时不需要再重复配
`WEB_ALLOWED_HOSTS`。

### 4.3 runtime_config.json 覆盖 .env

管理页写的 `shared/data/runtime_config.json` 优先级高于 `.env`。这次迁移中，
`.env` 与实际生效值在播报目标群和静默名单上**恰好相反**。

**只读 `.env` 判断 bot 行为会得出错误结论。** 迁移后核对两台机器上这个文件的
校验和，是确认"行为逐字保留"的最直接证据。

### 4.4 LaunchAgent 需要 GUI 会话

macOS 的 LaunchAgent 只在用户 GUI 会话建立后才加载。**没开自动登录的话，
重启后 bot 不会自己回来**，而且不会有任何报错 —— 它只是不在了。

```bash
sudo defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser
```

同时确认主机不会睡眠（`pmset -g` 应有 `sleep 0`、`disksleep 0`）：睡过去的主机会
错过 19:00 播报，且醒来后不会补发。

不想依赖登录会话就得改用 LaunchDaemon，代价是要处理以非 root 用户运行和日志权限。

---

## 5. 回滚

旧主机的 release 和 `shared/data/` 原样保留，回滚就是把 §3 阶段三倒过来：

1. **先停新主机的实例** —— 顺序反了就是两个实例抢 token
2. 重新 enable + start 旧主机的服务
3. 按 §3 阶段四重新验证一遍

代价要事先想清楚：**回滚会丢掉新主机上产生的数据**（活动、播报记录等）。
真要保，得先把新主机的 `shared/data/` 搬回去 —— 那是一次反向迁移，不是一条命令。
