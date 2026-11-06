# workbuddy-auto-checkin

这是 WorkBuddy 自动签到和活跃地图打卡。用 GitHub Actions 领取「Buddy 加油站」每天 100 积分，电脑不用开着。活跃地图如果还停在「轻轻路过」，同一轮会再补一次有回复的短对话。

本仓库采用 [MIT 许可证](LICENSE)。你可以复制这份代码、修改它，并自己运行。

建议把公开仓库命名为 **`workbuddy-auto-checkin`**。这个名字对应自动签到和活跃地图打卡，不包含个人账号，别人搜 WorkBuddy 签到时也能对上。

这不是官方工具。接口来自桌面端的公开行为，可能变更。请遵守 WorkBuddy 服务条款，风险自负。

## 技术框架

Python 3.12，只用标准库，没有第三方依赖，也没有前端。定时任务在 GitHub Actions 上跑。登录态只放在仓库 Secrets 里。

```text
GitHub Actions（北京时间 00:36 / 12:23 / 20:41）
        │
        ▼
scripts/checkin.py
        ├─ 登录态    Secrets 里的 accessToken；401 时用 refreshToken 再换一次
        ├─ 加油站    查询今日是否已领 → 未领则领取 100 积分（重复跑不会多领）
        ├─ 余额      读取真实资源包，核对这次有没有新额度
        └─ 活跃地图  当天分数 ≤ 2（页面上的「轻轻路过」）时：
                     先上报 1 条桌面对话事件；仍不超过 2 分，
                     再发一句短对话并等到模型回复
```

| 部分 | 位置 |
|------|------|
| 签到脚本 | `scripts/checkin.py` |
| 测试 | `python3 scripts/test_checkin.py`（不访问网络） |
| 定时 | `.github/workflows/daily-checkin.yml` |
| 接口域名 | `https://www.codebuddy.cn`，备选 `https://copilot.tencent.com` |

三条定时都躲开整点。GitHub 可能推迟或丢掉其中一槽，后面的槽会补上。定时任务只在默认分支上运行。

## 使用教程

### 1. 准备你自己的仓库

1. 电脑上安装并登录过 WorkBuddy 桌面端（5.3.8 及以上，会写出明文登录文件）。
2. 在 GitHub 新建一个仓库，名字用 `workbuddy-auto-checkin`。
3. 只放入**当前这份代码**。不要把带有旧提交的历史推上去。
4. 打开 **Settings → Actions → General**，允许 Actions 运行。默认分支保持为放有 workflow 的分支。

### 2. 取出登录态

用记事本或 VS Code 打开下面的文件。不要把文件本身提交到 Git，也不要发给别人。

| 系统 | 路径 |
|------|------|
| Windows | `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` |
| macOS | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| Linux | `~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |

需要的字段：

- `auth.accessToken`
- `account.uid`

企业账号再复制企业 ID，对应请求头 `X-Enterprise-Id`。

PowerShell 只打印 uid 和 token 末四位，用来确认找对了文件。不要把输出发到网上：

```powershell
$p = "$env:LOCALAPPDATA\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info"
$j = Get-Content -Raw $p | ConvertFrom-Json
"uid=$($j.account.uid)"
"token_tail=$($j.auth.accessToken.Substring($j.auth.accessToken.Length-4))"
```

### 3. 写入 GitHub Secrets

仓库页面：**Settings → Secrets and variables → Actions → New repository secret**

| Secret | 必填 | 填什么 |
|--------|------|--------|
| `WORKBUDDY_ACCESS_TOKEN` | 是 | `auth.accessToken`。也可以填 `auth.refreshToken`，或整份未加密的登录 JSON |
| `WORKBUDDY_UID` | 建议 | `account.uid` |
| `WORKBUDDY_REFRESH_TOKEN` | 否 | 单独的 refreshToken。填写后优先用它换新 accessToken |
| `WORKBUDDY_DOMAIN` | 否 | 默认 `www.codebuddy.cn` |
| `WORKBUDDY_ENTERPRISE_ID` | 否 | 仅企业账号 |

加密信封（字段里有 `$wbEncrypted`）不能直接贴进去。

### 4. 手动跑一次

1. 打开 **Actions → WorkBuddy Daily Checkin → Run workflow**。
2. 成功时，日志最后一行 JSON 的 `result` 是 `CLAIMED`（这次领到）或 `ALREADY`（今天已经领过）。
3. 完全退出 WorkBuddy 再打开，进入「Buddy 加油站」。今天的圆点是实心、连续天数加 1、本期累计加 100，就说明领到了。右上角还会过期的总额不能用来判断这次领取。

之后不用每天开电脑。三条定时会自己跑。

## 每天实际做了什么

1. **领取 100 积分。** 今天领过就跳过。新领的必须正好增加 100；已经领过时，今日积分是 100，并且本期累计等于连续天数 × 100。
2. **核对真实余额。** 看资源包里有没有新额度。过期扣掉的剩余不记成负增长。领取那一次会比较领取前和领取后。比较用的数字写在运行器上的本地账本里，不提交到 Git。
3. **活跃地图。** 2 分及以下页面显示「轻轻路过」。这时先上报 1 条桌面对话事件；分数仍不超过 2，再发送「你好，请只用一句话回答我。」并等待回复，输出上限 64 个 token。超过 2 分就不再发。不自动抽奖。

## 日志里会看到什么

Actions 上默认**不打印**账户总额、对比基准和历史签到日期。日志里仍有：

- 这次是领到还是已经领过
- 连续天数和本期累计（等于天数 × 100）
- 真实余额的增长额、失效额
- 今日活跃分，以及有没有补上对话

本机直接运行脚本时，仍会打印账户总额。私有仓库如果也想在 Actions 日志里看总额：到 **Settings → Secrets and variables → Actions → Variables**，新建 `WORKBUDDY_SHOW_BALANCE`，值填 `1`。公开仓库不要设这个变量。

## 失败了怎么看

到 [GitHub 通知设置](https://github.com/settings/notifications) 打开 Actions 邮件，可以只接收失败。任务变红就会收到邮件和这次运行的链接。

| 退出码 | 含义 | 怎么处理 |
|--------|------|----------|
| 0 | 成功，或今天已经领过 | 不用处理 |
| 1 | 没配登录态 | 检查 Secrets |
| 2 | 登录态失效 | 桌面端重新登录，把新的 accessToken 写回 Secret，再手动跑一次 |
| 3 | 网络或接口暂时不可用 | 等下一槽，或稍后手动跑 |
| 4 | 积分没增加 100、余额没增长，或活跃地图补完仍是轻轻路过 | 打开日志看增长额和活跃分 |

accessToken 过期后，如果 Secret 里有 refreshToken 或整份未加密登录 JSON，脚本会先换一张新 accessToken 再重试。新 token 只在这一次运行里使用，不会写回仓库。

## 本地试跑

不要把 token 写进文件再提交。

```bash
export WORKBUDDY_ACCESS_TOKEN='你的 token'
export WORKBUDDY_UID='你的 uid'
python3 scripts/checkin.py --status   # 只查询
python3 scripts/checkin.py            # 查询后领取
python3 scripts/test_checkin.py       # 不访问网络的测试
```

本地账本默认写到 `docs/wallet-daily.json`。这个文件已在 `.gitignore` 里。格式见 `docs/wallet-daily.example.json`。换路径可以设置 `WORKBUDDY_WALLET_LEDGER`。

## 公开仓库之前

仓库当前文件里没有 token、uid、手机号。登录态只在 GitHub Secrets 中，公开仓库也看不到 Secret 的值。

公开前请确认：

- 不要提交 `workbuddy-desktop.info`、`.env` 或 token 文件。`.gitignore` 已排除。
- 不要在 Issue、PR、README 里粘贴 token。
- 余额账本 `docs/wallet-daily.json` 不入库。
- `WORKBUDDY_SHOW_BALANCE` 不要设为 `1`。增长额仍会出现在日志里。
- 如果这个仓库以前跑过签到，旧的 Actions 日志和 Git 历史里可能有余额数字，早期日志还可能有 token 片段。不要把旧历史推到公开仓库。做法是新建一个仓库，只提交现在这棵文件树。原来的私有仓库如果继续自己用，先删掉旧的 Actions 运行记录，再考虑是否公开。
