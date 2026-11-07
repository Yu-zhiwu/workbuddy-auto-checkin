# workbuddy-auto-checkin

这是 WorkBuddy 自动签到和活跃地图打卡。用 GitHub Actions 领取「Buddy 加油站」每天 100 积分，电脑不用开着。活跃地图如果还停在「轻轻路过」，同一轮会再补一次有回复的短对话。

本仓库采用 [MIT 许可证](LICENSE)。你可以复制这份代码、修改它，并自己运行。

第一次使用，从下面的操作手册第 1 步做起，做到第 8 步核验完就可以停。后面的技术说明是给想看原理的人准备的，做任务时可以不看。

这不是官方工具。接口来自桌面端的公开行为，可能变更。请遵守 WorkBuddy 服务条款，风险自负。

## 新用户操作手册

请用电脑上的浏览器操作。手机上不好复制登录文件，也不好对页面。

签到跑在**你自己的仓库**里，用的是**你自己的登录态**。原仓库的 Actions 只读取仓库主人的 Secrets。请在你自己的页面上点「运行」，地址栏里要能看到你的 GitHub 用户名。

做完时，这三件事要同时成立：

1. 你的仓库里，这一次运行左边是绿色对勾。
2. 日志最后一行有 `"result":"CLAIMED"` 或 `"result":"ALREADY"`。同一行还有「活跃地图今天已点亮」，或者「今日活跃分」后面的数字大于 2。
3. 完全退出并重新打开 WorkBuddy。「Buddy 加油站」里今天的圆点是实心的。`CLAIMED` 表示这次新领到，连续天数加 1，本期累计加 100。`ALREADY` 表示今天早就领过，数字保持原样，这也算完成。

右上角还会过期的总额不能用来判断这次有没有领到。

### 第 1 步：准备一个 GitHub 账号

1. 打开 [github.com](https://github.com)。
2. 还没有账号时，点 **Sign up**（注册），用邮箱完成注册，并打开邮箱里的验证信。
3. 注册或登录后，页面右上角能看到你的头像。

做完时你应该看到：右上角是你的头像，点开能看到你的用户名。

### 第 2 步：给原仓库点 Star，再 Fork 一份到你的账号

1. 打开 [suows/workbuddy-auto-checkin](https://github.com/suows/workbuddy-auto-checkin)。
2. 页面右上角点 **Star**（星标）。按钮上的数字会加 1。Star 让更多人能找到这个项目，不点也可以继续，但请顺手点上。
3. 点旁边的 **Fork**（复刻）。
4. 在 **Create a new fork** 页面确认 **Owner** 是你的用户名。仓库名保持 `workbuddy-auto-checkin`。这个名字已被占用时，改成 `workbuddy-auto-checkin-2` 也可以。
5. 点绿色的 **Create fork**（创建复刻），等到页面自己跳走。

做完时你应该看到：

- 地址栏是 `https://github.com/你的用户名/仓库名`。
- 仓库名下面有一行 **forked from suows/workbuddy-auto-checkin**。

地址栏如果仍是 `github.com/suows/workbuddy-auto-checkin`，你还在原仓库。回到这一步重新 Fork。后面的按钮都点你自己的这份。

Fork 出来的页面别人也能打开。登录态放在 Secrets 里，别人看不到 Secret 的值。Actions 日志别人能看到，所以按本手册操作时不要新建 `WORKBUDDY_SHOW_BALANCE`。

### 第 3 步：打开你这份仓库里的自动任务

新建的 Fork 默认不跑定时任务。

1. 在你的仓库顶部点 **Actions**。
2. 如果页面中间有按钮 **I understand my workflows, go ahead and enable them**（我了解我的工作流程，现在启用它们），点它。
3. 左侧点 **WorkBuddy Daily Checkin**。
4. 如果页面上有 **Enable workflow**（启用工作流程），点它。
5. 点顶部 **Settings**（设置）。看不到 Settings，说明还停在原仓库，回到第 2 步。
6. 左侧点 **Actions**，再点 **General**。
7. 看 **Actions permissions**。选中 **Allow all actions and reusable workflows**（允许所有操作和可重用工作流程）。已经选中就不要改。改过的话，滚到页面最下方点 **Save**。
8. 这一步只做确认：左侧最上方的 **General** 里，**Default branch**（默认分支）是 `main`。不要改这个页面上的其他选项。

做完时你应该看到：再打开 **Actions → WorkBuddy Daily Checkin**，右侧有一个 **Run workflow**（运行工作流程）按钮。

### 第 4 步：在电脑上找出登录文件

先打开 WorkBuddy 桌面端并登录，版本用 5.3.8 或更新。登录成功后完全退出一次，让它把登录文件写到磁盘上。网页版登录不会写出这个文件。

然后按你的系统打开文件夹：

**Windows**

1. 按 `Windows + E` 打开文件资源管理器。
2. 点窗口顶部的地址栏，删掉里面的字。
3. 粘贴下面这一行，按回车：

```text
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth
```

**macOS**

1. 打开「访达」。
2. 屏幕最上方菜单点 **前往 → 前往文件夹**。
3. 粘贴下面这一行，按回车：

```text
~/Library/Application Support/CodeBuddyExtension/Data/Public/auth
```

**Linux**

打开文件管理器，进入：

```text
~/.config/CodeBuddyExtension/Data/Public/auth
```

文件夹里找到 `workbuddy-desktop.info`。Windows 用记事本打开，macOS 用文本编辑打开。找不到这个文件时，先确认桌面端已经登录，然后完全退出再找一次。

打开后看里面有没有 `$wbEncrypted`。有的话，这份是加密信封，不能贴进 GitHub。请换 5.3.8 及以上版本重新登录，直到文件里能直接看到 `accessToken` 这几个字母。

做完时你应该看到：记事本里是一大段以 `{` 开头、以 `}` 结尾的文字，里面有 `accessToken`，并且没有 `$wbEncrypted`。

### 第 5 步：把整份登录文件放进 Secret

整份未加密文件可以直接当作登录态。不必自己剪出 token。文件里可能有邮箱或手机号，只贴进你自己仓库的 Secret，不要贴到聊天、Issue 或本页。

1. 在记事本里按 `Ctrl + A`（macOS 用 `Command + A`）全选，再复制。
2. 回到浏览器里**你的仓库**。
3. 点 **Settings → Secrets and variables → Actions**。
4. 点 **New repository secret**（新建仓库机密）。
5. **Name** 一格原样填入：

```text
WORKBUDDY_ACCESS_TOKEN
```

字母大小写要一致，前后不要空格。

6. **Secret** 一格粘贴刚才复制的整段文字。不要另加引号，也不要只粘贴最后几个字。
7. 点 **Add secret**（添加机密）。

做完时你应该看到：Secrets 列表里有一行 `WORKBUDDY_ACCESS_TOKEN`。列表只显示名字，不显示内容。这是正常的。贴错了就点这一行右侧的铅笔图标，重新粘贴再保存。

个人账号做到这里即可。企业账号如果第 7 步日志里写登录失败或没有权限，再新建一个 Secret，名字用 `WORKBUDDY_ENTERPRISE_ID`，值填企业 ID。

### 第 6 步：手动跑第一次

1. 在你的仓库点 **Actions**。
2. 左侧点 **WorkBuddy Daily Checkin**。
3. 右侧点 **Run workflow**。
4. **Branch** 选 `main`。下拉里如果没有 `main`，选带绿色默认标记的那一支。
5. 再点一次绿色的 **Run workflow**。
6. 几秒后，列表最上面出现一条黄色圆点的新记录。点进这条记录。

黄色圆点表示正在跑。多数时候一两分钟结束，最长大约 8 分钟。等它变成绿色对勾或红色叉。

### 第 7 步：在日志里核验

1. 点进这一次运行。
2. 点中间的任务名 **checkin**。
3. 点开 **Claim daily credits**。
4. 滚动到这段日志的最后一行。最后一行是一整行 JSON，以 `{"result":` 开头。

对照最后一行：

| 你看到的 | 说明 |
|----------|------|
| 运行左边是绿色对勾，且最后一行是 `"result":"CLAIMED"` | 这次新领到 100 积分 |
| 运行左边是绿色对勾，且最后一行是 `"result":"ALREADY"` | 今天已经领过，脚本没有重复领 |
| 同一行有「活跃地图今天已点亮」 | 今天的活跃地图已经补上 |
| 同一行有「今日活跃分」，数字大于 2 | 今天的活跃地图本来就已经点亮 |
| 红色叉，最后一行是 `"result":"ERROR"` | 还没完成。看同一行 `"report"` 里的中文，对照下一节处理 |
| 红色叉，但最后一行已经是 `"result":"CLAIMED"` 或 `"result":"ALREADY"` | 签到这项过去了，活跃地图或余额核对还没过。看同一行有没有「活跃地图仍是轻轻路过」或「真实账户积分未增长」 |

绿色对勾表示签到和活跃地图这两项都过了。`CLAIMED` 和 `ALREADY` 都是签到成功，区别只在今天是不是这次新领的。红色叉时，先不要去加油站里反复领取，按「某一步失败时」处理。

### 第 8 步：在 WorkBuddy 里再核验一次

1. 完全退出 WorkBuddy。只点窗口右上角的关闭不够，请从任务栏或托盘图标里退出。
2. 重新打开 WorkBuddy。
3. 进入 **Buddy 加油站**。
4. 看今天：圆点是实心的。日志如果是 `CLAIMED`，连续天数比运行前多 1，本期累计比运行前多 100。日志如果是 `ALREADY`，这两项保持你运行前的数字。
5. 打开活跃地图，看今天这一格。上面不再写「轻轻路过」。

右上角那一个还会过期的总额，过期扣减也会让它变化，不能用它判断这次签到。

三件事都符合开头的完成标准后，这次就做完了。电脑可以关掉。后面每天会自动跑，不用再打开 WorkBuddy。

### 做完之后会怎样

你的 Fork 每天自动跑三次，北京时间 00:36、12:23、20:41。GitHub 可能推迟或丢掉其中一次，后面的次数会补上。今天已经领过时，再跑会得到 `ALREADY`，不会多领 100 积分。

登录大约过一段时间会失效。失效时这次运行是红色的，日志里有登录态失效或 HTTP 401。处理办法：重新打开桌面端并登录，再按第 4 步和第 5 步，把新的整份文件更新进同一个 `WORKBUDDY_ACCESS_TOKEN`，然后按第 6 步再跑一次。

原仓库有更新时：打开你的 Fork，点 **Sync fork**（同步复刻），再点 **Update branch**（更新分支）。Secret 还在，不用重填。

别人也能打开的仓库如果连续 60 天没有新的提交或设置变更，GitHub 会关掉定时任务。手动运行有时仍可用，定时不再触发。回到 **Actions → WorkBuddy Daily Checkin**，重新点 **Enable workflow**。

### 某一步失败时

先看运行是不是红色。是红色就打开 **Claim daily credits** 最后一行的 `"report"`。

| 日志里的意思 | 回到哪一步 |
|--------------|------------|
| 缺少 `WORKBUDDY_ACCESS_TOKEN`，或登录态不是可用的 | 第 5 步。名字要完全一致；内容粘贴整份未加密文件 |
| 看到 `$wbEncrypted`，或提示加密信封 | 第 4 步。换桌面端重新登录，直到文件里没有这段字 |
| 登录态失效、HTTP 401、HTTP 403 | 桌面端重新登录，再做第 4 步和第 5 步，然后重跑 |
| 网络错误，或接口暂时不可用 | 等 10 分钟，再做一次第 6 步 |
| 积分没增加 100，或真实账户积分未增长 | 先看第 8 步加油站。圆点没变实心就再跑一次；仍失败就等下一次自动运行 |
| 「活跃地图仍是轻轻路过」 | 签到可能已经领到，地图还没点亮。等 10 分钟再跑一次第 6 步，然后重做第 8 步 |
| 页面上找不到 **Run workflow** | 第 3 步。先启用 Actions，再启用这个工作流 |

要让失败时收到邮件：打开 [GitHub 通知设置](https://github.com/settings/notifications)，把 Actions 的邮件打开，可以只接收失败。

### 选做：仓库只想自己看见

上面的 Fork 别人能打开页面和日志，看不见 Secret。如果你连日志也不想被人看见，另外新建一个 **Private**（私有）仓库，不要把 Fork 改成私有，GitHub 不允许这样做。私有仓库不计 Fork。

1. 回到 [原仓库](https://github.com/suows/workbuddy-auto-checkin) 点 **Star**。
2. 点 GitHub 右上角 **+ → New repository**。仓库名自定，可见性选 **Private**，不要勾选添加 README。点 **Create repository**。
3. 页面上会给出一段把代码推上去的命令。先在电脑上安装 [Git](https://git-scm.com/downloads)，打开终端，执行：

```bash
git clone --depth 1 https://github.com/suows/workbuddy-auto-checkin.git
cd workbuddy-auto-checkin
git checkout --orphan fresh
git add -A
git commit -m "Start from workbuddy-auto-checkin"
git branch -M main
git remote set-url origin https://github.com/你的用户名/你的私有仓库.git
git push -u origin main
```

把最后一行的地址换成你刚建的私有仓库地址。GitHub 要求登录时，按页面提示用浏览器登录。

4. 从本手册第 3 步做起，把「你的 Fork」理解成「你的私有仓库」。
5. 只有这种别人打不开的仓库，才可以在想看账户总额时，到 **Settings → Secrets and variables → Actions → Variables** 新建 `WORKBUDDY_SHOW_BALANCE`，值填 `1`。

以后原仓库更新了，再执行一次上面的克隆，把新文件拷进私有仓库。不要把 `workbuddy-desktop.info` 一起提交。

## 技术说明

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

### 每天实际做了什么

1. **领取 100 积分。** 今天领过就跳过。新领的必须正好增加 100；已经领过时，今日积分是 100，并且本期累计等于连续天数 × 100。
2. **核对真实余额。** 看资源包里有没有新额度。过期扣掉的剩余不记成负增长。领取那一次会比较领取前和领取后。比较用的数字写在运行器上的本地账本里，不提交到 Git。
3. **活跃地图。** 2 分及以下页面显示「轻轻路过」。这时先上报 1 条桌面对话事件；分数仍不超过 2，再发送「你好，请只用一句话回答我。」并等待回复，输出上限 64 个 token。超过 2 分就不再发。不自动抽奖。

### 日志里会看到什么

Actions 上默认不打印账户总额、对比基准和历史签到日期。日志里仍有：

- 这次是领到还是已经领过
- 连续天数和本期累计（等于天数 × 100）
- 真实余额的增长额、失效额
- 今日活跃分，以及有没有补上对话

本机直接运行脚本时，仍会打印账户总额。只有你自己能看的仓库，如果也想在 Actions 日志里看总额：到 **Settings → Secrets and variables → Actions → Variables**，新建 `WORKBUDDY_SHOW_BALANCE`，值填 `1`。别人也能打开这个仓库时，不要设这个变量。

### 退出码

| 退出码 | 含义 | 怎么处理 |
|--------|------|----------|
| 0 | 成功，或今天已经领过，并且活跃地图已点亮 | 不用处理 |
| 1 | 没配登录态 | 检查 Secrets |
| 2 | 登录态失效 | 桌面端重新登录，把新的登录文件写回 Secret，再手动跑一次 |
| 3 | 网络或接口暂时不可用 | 等下一槽，或稍后手动跑 |
| 4 | 积分没增加 100、余额没增长，或活跃地图补完仍是轻轻路过 | 打开日志看增长额和活跃分 |

accessToken 过期后，如果 Secret 里有 refreshToken 或整份未加密登录 JSON，脚本会先换一张新 accessToken 再重试。新 token 只在这一次运行里使用，不会写回仓库。

### 只想自己剪出 token 时

手册第 5 步粘贴整份文件即可。如果你只想保存两段文字，在登录文件里搜索这两个字段：

- `auth.accessToken` 或 `accessToken`
- `account.uid` 或 `uid`

复制引号里面的内容，不要带两侧的引号。`WORKBUDDY_ACCESS_TOKEN` 填 token，另外新建 `WORKBUDDY_UID` 填 uid。可选的 Secret 还有：`WORKBUDDY_REFRESH_TOKEN`（单独的 refreshToken，填写后优先用它换新 accessToken）、`WORKBUDDY_DOMAIN`（默认 `www.codebuddy.cn`）、`WORKBUDDY_ENTERPRISE_ID`（仅企业账号）。

Windows 可以用 PowerShell 核对找对了文件。它只打印 uid 和 token 末四位，不要把输出发到网上：

```powershell
$p = "$env:LOCALAPPDATA\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info"
$j = Get-Content -Raw $p | ConvertFrom-Json
"uid=$($j.account.uid)"
"token_tail=$($j.auth.accessToken.Substring($j.auth.accessToken.Length-4))"
```

### 本地试跑

只在本机跑时，电脑要开着，也不会出现在原仓库的 Fork 列表里。要每天自动跑，用上面的 Fork。不要把 token 写进文件再提交。

```bash
export WORKBUDDY_ACCESS_TOKEN='你的 token'
export WORKBUDDY_UID='你的 uid'
python3 scripts/checkin.py --status   # 只查询
python3 scripts/checkin.py            # 查询后领取
python3 scripts/test_checkin.py       # 不访问网络的测试
```

本地账本默认写到 `docs/wallet-daily.json`。这个文件已在 `.gitignore` 里。格式见 `docs/wallet-daily.example.json`。换路径可以设置 `WORKBUDDY_WALLET_LEDGER`。

### 保管好登录态

仓库文件里没有 token、uid 或手机号。登录态放在你自己仓库的 GitHub Secrets 里，Secret 的值不会写进仓库文件。

请留意：

- 不要提交 `workbuddy-desktop.info`、`.env` 或 token 文件。`.gitignore` 已排除。
- 不要在 Issue、PR、README 里粘贴 token。
- 余额账本 `docs/wallet-daily.json` 不入库。
- 别人也能看到仓库时，不要把 `WORKBUDDY_SHOW_BALANCE` 设为 `1`。增长额仍会出现在日志里。
- 如果另一份仓库里已经跑过签到，旧的 Actions 日志和 Git 历史里可能有余额数字，早期日志还可能有 token 片段。复制代码时只带上现在这棵文件树。
