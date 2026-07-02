# 贡献指南

感谢参与改进。本项目当前只接受 Windows 10/11 适配，提交前请遵循以下规则。

## 隐私与测试数据

- 不要提交真实账号、密码、手机令牌、Cookie、SendKey、课程或成绩。
- 不要提交 `.browser-profile/`、`config.local.json`、`credentials.dat` 或 `data/`。
- 接口结构问题应使用程序生成的脱敏诊断，并在提交前人工复查。
- 自动化测试必须使用虚构数据和模拟网络请求，不得访问真实成绩接口。

## 本地验证

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile grade_alert.py grade_alert_gui.py environment_check.py local_secrets.py
```

涉及 GUI 的改动还应运行：

```powershell
.\.venv\Scripts\python.exe grade_alert_gui.py --smoke-test
```

## Pull Request

- 保持改动范围清晰，说明用户可见行为和验证方式。
- 登录、成绩解析、通知和卸载逻辑的改动应补充相应测试。
- 不要为了测试提高真实平台的查询频率。
- 确认 Windows CI 通过，并再次检查提交内容中没有敏感信息。
