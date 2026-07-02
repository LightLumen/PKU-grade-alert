# 北大树洞成绩提醒（Windows）

一个仅适配 Windows 的本地成绩变化监控工具。程序复用专用 Microsoft Edge 会话，定时读取树洞成绩接口；只有课程成绩新增或变化时，才通过可选的 Server酱发送通知。

> 本项目是非官方个人工具，与北京大学、树洞或 Server酱无隶属或授权关系。请仅用于本人账户，遵守相关服务规则，并设置合理查询间隔。

## 功能

- 本科与研究生成绩结构兼容，包括数字成绩、P/F、NP 和 W
- 首次查询建立本地基线，后续仅提醒新增或变化的成绩
- Server酱手机通知，可选择是否在通知中显示具体分数
- 复用本机 Edge 登录会话
- 可选账号密码自动恢复登录，凭据由 Windows DPAPI 加密
- 登录需要动态令牌、短信或图形验证码时通知用户处理
- 一键环境检测、安装、启动和本地数据卸载
- 脱敏接口诊断，不输出 Cookie、令牌、课程名或成绩值

## 环境要求

- Windows 10/11
- Python 3.10 或更新版本，并可通过 `py -3` 或 `python` 调用
- Microsoft Edge
- 首次安装依赖时可访问 Python 软件包源

本项目直接使用系统 Edge，通常无需执行 `playwright install`。

## 快速开始

下载或克隆仓库后，双击：

```text
检测并安装.bat
```

脚本会在项目内创建 `.venv`、安装依赖、检测 Windows/Python/Edge/配置和磁盘空间，然后打开 GUI。以后可双击：

```text
启动成绩提醒.bat
```

首次设置：

1. 将查询间隔设为 30 分钟左右就可以，避免给学校平台带来不必要的压力。
2. 如需手机提醒，启用 Server酱、填写 SendKey 并测试推送。
3. 选择手动登录，或在“自动登录（可选）”页设置本机自动登录。
4. 点击“立即查询”建立成绩基线。
5. 点击“开始监控”，保持 GUI 运行。

锁屏或显示器熄灭不会停止程序。睡眠、休眠、关机和断网期间无法查询；恢复后，仍在运行的程序会继续工作。

## 使用方法补充
1. server酱网址：https://sct.ftqq.com 微信注册后获取 SendKey，将SendKey复制到GUI的server酱后面。

**千万注意不要将SendKey泄露给他人。同学，你也不想你的账户被刷爆吧**

2. 为了大家账号密码和SendKey的安全，程序完全在本地运行，但是一旦电脑进入睡眠或休眠，程序就会停止运行，无法继续监控成绩变化。请大家在使用时注意电脑的电源设置，以及笔记本电脑盒盖的设置，避免进入睡眠或休眠状态。

示例（Win11）：设置 → 系统 → 电源和电池 → 屏幕、睡眠和休眠超时→接通电源时,设置：关闭屏幕：5 分钟；进入睡眠：从不。

**如果这台电脑能一直连接电源最好了，这也是本地运行的不便之处**

3. 微信关注server酱公众号“方糖”后记得把消息免打扰关掉，这样你就能在微信上收到带消息提醒的推送通知了。

**惊心动魄的成绩变化提醒，值得你关注**


## 登录与隐私

### 手动登录

点击“首次登录 / 重新验证”，在专用 Edge 窗口中完成 IAAA 登录和可能出现的二次验证。浏览器会话保存在 `.browser-profile/`。

### 自动登录（可选）

- 不启用时无需填写任何账号信息。
- 启用时账号和密码必填，使用 Windows DPAPI 加密保存到 `credentials.dat`，只能由当前 Windows 用户在当前电脑上解密。
- 手机令牌可选，仅驻留内存并用于下一次登录尝试，用后立即清除，不写入磁盘。
- 自动登录时，账号、密码和当次令牌仅提交给北大官方 IAAA 登录页面。
- 如果认证系统要求新的动态令牌、短信验证码或图形验证码，程序会停止自动恢复并提示人工验证，无法绕过二次验证。

启用 Server酱后，通知内容会发送给 Server酱。可以关闭“通知中显示具体成绩”以减少通知内容中的敏感信息。

## 本地文件

以下内容已由 `.gitignore` 排除，切勿手动提交：

- `.browser-profile/`：浏览器登录会话
- `credentials.dat`：DPAPI 加密账号密码
- `config.local.json`：本地设置与 Server酱 SendKey
- `data/last_scores.json`：真实课程与成绩基线
- `.venv/`：本地 Python 虚拟环境

`data/diagnostics.json` 虽经过脱敏，也默认不进入版本控制。提交 Issue 前仍应自行检查内容。

## 卸载

先关闭 GUI，再双击：

```text
卸载本机环境.bat
```

输入 `DELETE` 后，脚本会删除 `.venv`、浏览器会话、成绩数据、本地配置、SendKey 和加密凭据，保留源代码与文档。

## 已知限制

- 只支持 Windows，不计划在当前版本适配 macOS 或 Linux。
- 电脑必须保持开机、联网且未进入睡眠或休眠。
- 学校登录页或成绩接口变更后，可能需要更新选择器或数据解析逻辑。
- 动态手机令牌、短信验证码和图形验证码不能长期自动生成。
- 本工具不保证成绩发布时效或学校服务的可用性。

## 开发与测试

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile grade_alert.py grade_alert_gui.py environment_check.py local_secrets.py
```

测试不得使用真实账号、密码、SendKey、Cookie 或成绩数据。贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
