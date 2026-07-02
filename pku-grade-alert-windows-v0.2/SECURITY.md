# 安全说明

## 报告安全问题

请优先使用 GitHub 的 Private vulnerability reporting 或 Security Advisory 私下报告安全问题。不要在公开 Issue、Pull Request、截图或日志中粘贴以下内容：

- 北大账号、密码或手机令牌
- Cookie、`pku_token` 或 `.browser-profile` 内容
- Server酱 SendKey
- `config.local.json`、`credentials.dat` 或 `data/last_scores.json`
- 含真实课程、教师或成绩的信息

如果敏感信息已经公开，请先删除公开内容，并立即修改密码、撤销 SendKey 或使相关会话失效。仅删除 GitHub 页面上的文本不能保证信息从历史记录或第三方缓存中消失。

## 安全边界

- 自动登录凭据使用 Windows DPAPI 当前用户范围加密，但当前 Windows 用户账户被控制时，攻击者仍可能读取这些凭据。
- 浏览器会话目录本身属于敏感登录凭据，DPAPI 不会额外加密整个 `.browser-profile`。
- Server酱通知会离开本机；不希望发送具体成绩时，应关闭 GUI 中的成绩值选项。
- 本项目不会尝试绕过动态令牌、短信验证码、图形验证码或学校认证策略。

目前只对最新的 `main` 分支提供安全修复。
