# linuxdo-toolkit

轻量级 [linux.do](https://linux.do) 每日访问保持工具：自动登录 + 少量真实感浏览行为，维持个人账号的「访问天数」。

> **仅供个人学习使用。** 请阅读[免责声明](#免责声明)。

## 项目说明

- 单账号、单线程、克制运行：每次浏览少量帖子（默认 6，上限 20），全程约 2~5 分钟
- 登录优先级：Cookie（`LINUXDO_COOKIES`）→ 账号密码（自动回退）
- 登录 HTTP 请求优先使用 [curl-cffi](https://github.com/lexiforest/curl-cffi)（Chrome TLS 指纹），不可用时自动降级 httpx
- 浏览行为由 [Playwright](https://playwright.dev/python/)（Chromium）驱动：随机选帖、随机滚动、随机停留
- 默认**不点赞**；即使开启也限制概率 ≤30% 且每次运行 ≤3 次
- 可插拔通知（内置 Telegram），运行结束推送成功/失败摘要
- 支持本地运行与 GitHub Actions 定时运行

## 免责声明

本项目**仅供个人学习与研究使用**。使用本工具即表示你理解并同意：

1. **遵守社区规则**：使用本工具前请阅读并遵守 linux.do 社区的用户协议与行为规范。自动化访问可能违反站点服务条款。
2. **风险自担**：因使用本工具导致的账号限制、封禁或其他后果，由使用者自行承担。
3. **禁止滥用**：严禁多账号、多线程、高频运行、养号或任何给社区增加负担的行为。本工具的设计初衷是「以最小存在感维持访问记录」，请勿修改代码突破浏览数量与频率限制。
4. **凭据安全**：Cookie 与账号密码仅存储在你自己配置的环境变量 / GitHub Secrets 中，本项目不收集、不上传任何凭据。
5. **非官方**：本项目与 linux.do 官方无任何关联。

## 项目结构

```
linuxdo-toolkit/
├── .github/workflows/daily.yml   # GitHub Actions 定时任务
├── src/
│   ├── config.py                 # pydantic-settings 配置（fail-fast 校验）
│   ├── http_client.py            # curl-cffi 优先 / httpx 降级适配器
│   ├── auth.py                   # Cookie 解析 + 登录链 + 登录验证
│   ├── browser.py                # Playwright 生命周期封装
│   ├── browse.py                 # 浏览行为（随机滚动 + 停留 + 可选点赞）
│   ├── retry.py                  # 通用重试助手（带随机延迟）
│   ├── notify.py                 # 可插拔通知（内置 Telegram）
│   ├── models.py                 # 运行结果模型
│   └── main.py                   # 入口与编排
└── tests/                        # 单元测试（全部 mock 网络）
```

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `LINUXDO_COOKIES` | 二选一 | — | 浏览器复制的完整 Cookie 字符串（优先） |
| `LINUXDO_USERNAME` | 条件 | — | Cookie 缺失/失效时的回退登录 |
| `LINUXDO_PASSWORD` | 条件 | — | 同上 |
| `BROWSE_ENABLED` | 否 | `true` | 是否执行浏览行为（`false` 时仅登录验证） |
| `BROWSE_COUNT` | 否 | `6` | 浏览帖数（1~20，超出启动报错） |
| `LIKE_ENABLED` | 否 | `false` | 是否启用点赞（开启后概率 ≤30%、每次 ≤3 次） |
| `HEADLESS` | 否 | `true` | 无头浏览器 |
| `NOTIFY_ENABLED` | 否 | `false` | 是否发送通知 |
| `NOTIFY_CHANNEL` | 否 | `telegram` | 通知渠道 |
| `NOTIFY_TELEGRAM_BOT_TOKEN` | 条件 | — | 启用 Telegram 通知时必需 |
| `NOTIFY_TELEGRAM_CHAT_ID` | 条件 | — | 同上 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

## Cookie 获取方法

1. 用浏览器登录 [linux.do](https://linux.do)；
2. 按 `F12` 打开开发者工具 → 切到 **Network（网络）** 面板；
3. 刷新页面，点击任意一个发往 `linux.do` 的请求 → **Headers（标头）** → 找到 `Cookie` 请求头；
4. 右键复制整段值（形如 `_t=xxxxx; theme_ids=1; ...`），粘贴到 `LINUXDO_COOKIES`。

> 提示：`_t` 是关键的会话 Cookie。Cookie 有效期由站点控制，失效后工具会自动回退到账号密码登录（若已配置）。

## 本地运行

```bash
# 1. 安装 uv（已安装可跳过）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 安装依赖 + Chromium
uv sync
uv run playwright install chromium

# 3. 配置凭据
cp .env.example .env
# 编辑 .env，填入 LINUXDO_COOKIES（或 USERNAME/PASSWORD）

# 4. 运行
uv run python -m src.main            # 完整运行（登录 + 浏览 + 通知）
uv run python -m src.main --dry-run  # 仅登录验证，不浏览、不通知
```

## GitHub Actions 使用

1. **Fork 或使用本仓库**，进入仓库 **Settings → Secrets and variables → Actions**。
2. 在 **Secrets** 中添加（至少一种登录方式）：

   | Secret | 说明 |
   |--------|------|
   | `LINUXDO_COOKIES` | 浏览器复制的完整 Cookie 字符串（推荐） |
   | `LINUXDO_USERNAME` | 回退账号（可选） |
   | `LINUXDO_PASSWORD` | 回退密码（可选） |
   | `NOTIFY_TELEGRAM_BOT_TOKEN` | Telegram 通知（可选） |
   | `NOTIFY_TELEGRAM_CHAT_ID` | Telegram 通知（可选） |

   可选的**变量**（Settings → Variables，有默认值）：`BROWSE_ENABLED`、`BROWSE_COUNT`、`LIKE_ENABLED`、`NOTIFY_ENABLED`、`NOTIFY_CHANNEL`。

3. Workflow 说明（`.github/workflows/daily.yml`）：
   - **定时**：每天 UTC 1:23（北京时间 9:23）自动运行，避开整点；
   - **手动**：Actions 页面选择 "Daily Visit" → Run workflow；
   - push / 手动触发时会先跑 lint + 测试，定时运行直接执行以加速。

> 注意：GitHub Actions 的出口 IP 属于数据中心段，部分站点风控较严。若遇到问题，可降低频率或改为本地定时运行（如 macOS launchd / Linux crontab）。

## 扩展通知渠道

`src/notify.py` 采用注册表设计，新增渠道只需两步：

```python
class MySender:  # 实现 send(settings, title, body) 即可
    def send(self, settings, title, body) -> None: ...

notify.register_sender("my_channel", MySender())
```

## 开发

```bash
uv sync                                  # 安装依赖（含 dev 组）
uv run pytest -q                         # 运行测试（全部 mock，无真实网络请求）
uv run ruff check .                      # 代码检查
uv run python -m src.main --dry-run      # 只验证登录链路
```

## License

[MIT](./LICENSE)
