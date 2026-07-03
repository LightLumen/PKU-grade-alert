#!/usr/bin/env python3
"""Windows-only PKU Treehole grade change monitor.

Authentication stays in a dedicated browser profile. Optional credentials are
stored with Windows DPAPI and submitted only to the official PKU IAAA page.
The pku_token cookie is read only inside the browser page and is never returned
to Python or written by this application.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.local.json"
EXAMPLE_CONFIG_PATH = APP_DIR / "config.example.json"
PROFILE_DIR = APP_DIR / ".browser-profile"
DATA_DIR = APP_DIR / "data"
STATE_PATH = DATA_DIR / "last_scores.json"
DIAGNOSTICS_PATH = DATA_DIR / "diagnostics.json"

WEB_SCORE_URL = "https://treehole.pku.edu.cn/web/webscore"
TREEHOLE_HOME_URL = "https://treehole.pku.edu.cn/web"
TREEHOLE_LOGIN_URL = "https://treehole.pku.edu.cn/redirect_iaaa_login"
AUTH_CODES = {40002, 40008, 40009, 40010, 40077, 40088, 40099}


class GradeAlertError(RuntimeError):
    """Base error for expected runtime failures."""


class LoginRequired(GradeAlertError):
    """The persistent browser profile needs interactive login."""


class SecondFactorRequired(LoginRequired):
    """A fresh OTP, SMS code, or CAPTCHA is required."""


class ScoreResponseError(GradeAlertError):
    """The grade API returned an unexpected or unsuccessful response."""


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise GradeAlertError(f"{path.name} 的顶层必须是 JSON 对象")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def init_config() -> None:
    if CONFIG_PATH.exists():
        print(f"配置文件已经存在：{CONFIG_PATH}")
        return
    CONFIG_PATH.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"已创建配置文件：{CONFIG_PATH}")
    print("可以先保持默认配置；需要手机推送时再填写 Server酱 SendKey。")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise GradeAlertError("缺少 config.local.json，请先运行：python grade_alert.py init")
    config = read_json(CONFIG_PATH)
    poll_seconds = config.get("poll_seconds", 900)
    if not isinstance(poll_seconds, int) or poll_seconds < 60:
        raise GradeAlertError("poll_seconds 必须是大于或等于 60 的整数")
    return config


@contextmanager
def browser_page(config: dict[str, Any], headed: bool) -> Iterator[Any]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise GradeAlertError(
            "缺少 Playwright，请运行：python -m pip install -r requirements.txt"
        ) from error

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(config.get("navigation_timeout_seconds", 60)) * 1000
    channel = str(config.get("browser_channel", "msedge")).strip()

    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": not headed,
        }
        if channel:
            launch_options["channel"] = channel
        try:
            context = playwright.chromium.launch_persistent_context(**launch_options)
        except Exception as error:
            raise GradeAlertError(
                "无法启动浏览器。请确认已安装 Microsoft Edge，且没有其他程序占用 .browser-profile"
            ) from error
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(timeout_ms)
            try:
                page.goto(WEB_SCORE_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                # The site occasionally keeps loading background resources. The
                # page can still be usable, so the API probe below is authoritative.
                pass
            yield page
        finally:
            context.close()


def _locator_visible(page: Any, selector: str) -> bool:
    locator = page.locator(selector)
    return locator.count() == 1 and locator.is_visible()


def _navigation_destroyed_context(error: BaseException) -> bool:
    message = str(error).lower()
    return "execution context was destroyed" in message and "navigation" in message


def auto_login_treehole(
    page: Any,
    username: str,
    password: str,
    phone_token: str | None = None,
    trust_device: bool = True,
) -> dict[str, Any]:
    """Log in through PKU IAAA using the public login form.

    The optional phone token is treated as a one-time value and is never stored
    by this function. SMS and image CAPTCHA challenges always require the user.
    """

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as error:
        raise GradeAlertError("缺少 Playwright，无法执行自动登录") from error

    username = username.strip()
    if not username or not password:
        raise GradeAlertError("自动登录需要账号和密码")

    generated_uuid = f"Web_PKUHOLE_2.0.0_WEB_UUID_{uuid.uuid4()}"
    pku_uuid = generated_uuid
    try:
        page.goto(TREEHOLE_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        pass

    for attempt in range(3):
        try:
            stored_uuid = page.evaluate("() => localStorage.getItem('pku-uuid')")
            if stored_uuid:
                pku_uuid = stored_uuid
            else:
                page.evaluate(
                    "value => localStorage.setItem('pku-uuid', value)",
                    generated_uuid,
                )
            break
        except Exception as error:
            if not _navigation_destroyed_context(error):
                raise LoginRequired("树洞页面尚未准备好，无法开始统一认证") from error
            if attempt < 2:
                page.wait_for_timeout(500)
    # A fresh UUID is sufficient for the redirect when repeated navigation
    # prevents localStorage from being read. The next stable page persists it.
    login_url = TREEHOLE_LOGIN_URL + "?" + urllib.parse.urlencode({"uuid": pku_uuid})
    page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)

    if "treehole.pku.edu.cn" in page.url and "iaaa.pku.edu.cn" not in page.url:
        page.goto(WEB_SCORE_URL, wait_until="domcontentloaded", timeout=60_000)
        return fetch_score_payload(page)

    try:
        page.wait_for_selector("#user_name", state="visible", timeout=20_000)
    except PlaywrightTimeoutError as error:
        raise LoginRequired("统一认证登录页没有正常加载") from error

    page.locator("#user_name").fill(username)
    page.locator("#password").click()
    page.locator("#password").fill(password)
    page.wait_for_timeout(800)

    token_used = False
    phone_token = (phone_token or "").strip()
    if _locator_visible(page, "#otp_code") and phone_token:
        page.locator("#otp_code").fill(phone_token)
        token_used = True

    if trust_device:
        trust = page.locator("#remTrust_check")
        if trust.count() == 1 and not trust.is_checked():
            # IAAA visually replaces this native checkbox and keeps the input
            # hidden. Set its state in the DOM so Playwright does not try to
            # scroll or click an element that has no visible box.
            trust.evaluate(
                """element => {
                    element.checked = true;
                    element.dispatchEvent(new Event("input", {bubbles: true}));
                    element.dispatchEvent(new Event("change", {bubbles: true}));
                }"""
            )

    page.locator("#logon_button").click()

    for _attempt in range(2):
        try:
            page.wait_for_url("**treehole.pku.edu.cn/**", timeout=12_000)
        except PlaywrightTimeoutError:
            pass
        if "treehole.pku.edu.cn" in page.url and "iaaa.pku.edu.cn" not in page.url:
            page.goto(WEB_SCORE_URL, wait_until="domcontentloaded", timeout=60_000)
            return fetch_score_payload(page)

        if _locator_visible(page, "#otp_code"):
            if not phone_token or token_used:
                raise SecondFactorRequired("需要当前手机令牌，请在 GUI 中输入后重试")
            page.locator("#otp_code").fill(phone_token)
            token_used = True
            page.locator("#logon_button").click()
            continue
        if _locator_visible(page, "#sms_code"):
            raise SecondFactorRequired("需要短信验证码，请使用可见登录窗口完成验证")
        if _locator_visible(page, "#valid_code"):
            raise SecondFactorRequired("需要图形验证码，请使用可见登录窗口完成验证")

        message = ""
        message_locator = page.locator("#msg")
        if message_locator.count() == 1:
            message = message_locator.inner_text().strip()
        raise LoginRequired(message or "统一认证未完成，请检查账号密码")

    raise LoginRequired("统一认证未完成")


FETCH_SCORE_SCRIPT = r"""
async () => {
  const readCookie = (name) => {
    const prefix = name + "=";
    const item = document.cookie.split("; ").find((part) => part.startsWith(prefix));
    return item ? decodeURIComponent(item.slice(prefix.length)) : "";
  };

  const token = readCookie("pku_token");
  if (!token) {
    return {kind: "auth_missing", pageUrl: window.location.href};
  }

  const response = await fetch("/api/course/score_v2", {
    method: "GET",
    headers: {
      Authorization: "Bearer " + token,
      Uuid: localStorage.getItem("pku-uuid") || ""
    }
  });

  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = {parseError: String(error)};
  }
  return {kind: "response", status: response.status, body};
}
"""


def fetch_score_payload(page: Any) -> dict[str, Any]:
    result: Any = None
    for attempt in range(2):
        try:
            result = page.evaluate(FETCH_SCORE_SCRIPT)
            break
        except Exception as error:
            if _navigation_destroyed_context(error) and attempt == 0:
                try:
                    page.goto(
                        WEB_SCORE_URL,
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(500)
                continue
            raise ScoreResponseError(
                "浏览器中的成绩请求执行失败，请检查网络后重试；必要时使用 check --headed 排查"
            ) from error
    if not isinstance(result, dict):
        raise ScoreResponseError("浏览器没有返回可识别的查询结果")
    if result.get("kind") == "auth_missing":
        raise LoginRequired("未找到有效登录状态，请运行 login 命令")
    status = result.get("status")
    body = result.get("body")
    if status == 401:
        raise LoginRequired("登录状态已过期，请重新运行 login 命令")
    if status != 200 or not isinstance(body, dict):
        raise ScoreResponseError(f"成绩接口返回 HTTP {status}")
    code = body.get("code")
    if code in AUTH_CODES:
        raise LoginRequired(f"平台要求额外身份验证（接口代码 {code}），请运行 login 命令")
    return body


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def normalize_courses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ScoreResponseError("响应中缺少 data 对象")
    score_data = data.get("score")
    if not isinstance(score_data, dict):
        raise ScoreResponseError("响应中缺少 data.score 对象")
    if score_data.get("success") is False:
        message = score_data.get("errMsg") or payload.get("message") or "成绩查询失败"
        raise ScoreResponseError(str(message))

    graduate = score_data.get("xslb") == "yjs"
    source_name = "scoreLists" if graduate else "cjxx"
    raw_courses = score_data.get(source_name, [])
    if raw_courses is None:
        raw_courses = []
    if not isinstance(raw_courses, list):
        raise ScoreResponseError(f"data.score.{source_name} 不是数组")

    courses: list[dict[str, Any]] = []
    for item in raw_courses:
        if not isinstance(item, dict):
            continue
        score = clean_text(item.get("cj") if graduate else item.get("xqcj"))
        course_id = clean_text(item.get("kch")) or "unknown"
        name = clean_text(item.get("kcmc")) or "未命名课程"
        year = clean_text(item.get("xnd"))
        semester = clean_text(item.get("xq"))
        attempt = (
            clean_text(item.get("bkcjbh"))
            or clean_text(item.get("jxbh"))
            or name
        )
        key = "|".join(part or "" for part in (year, semester, course_id, attempt))
        courses.append(
            {
                "key": key,
                "course_id": course_id,
                "name": name,
                "year": year,
                "semester": semester,
                "credit": clean_text(item.get("xf")),
                "score": score,
                "grade_point": clean_text(item.get("jd")),
                "type": clean_text(item.get("kclb") if graduate else item.get("kclbmc")),
                "teacher": clean_text(item.get("skjsxm")),
                "graduate": graduate,
            }
        )
    courses.sort(key=lambda course: course["key"])
    return courses


def has_score(course: dict[str, Any]) -> bool:
    return clean_text(course.get("score")) not in {None, "--", "-.--"}


def detect_changes(
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, course in current.items():
        old = previous.get(key)
        if old is None:
            if has_score(course):
                changes.append({"kind": "new", "old_score": None, "course": course})
            continue
        old_score = clean_text(old.get("score"))
        new_score = clean_text(course.get("score"))
        if old_score != new_score and has_score(course):
            changes.append(
                {"kind": "updated", "old_score": old_score, "course": course}
            )
    return changes


def build_message(changes: list[dict[str, Any]], include_score: bool) -> tuple[str, str]:
    title = f"检测到 {len(changes)} 项成绩变化"
    lines = [title, f"查询时间：{now_text()}"]
    for change in changes:
        course = change["course"]
        term = f"{course.get('year') or '?'} 学年 第 {course.get('semester') or '?'} 学期"
        if include_score:
            score_text = course.get("score") or "未显示"
            if change["kind"] == "updated" and change.get("old_score") is not None:
                score_text = f"{change['old_score']} -> {score_text}"
            lines.append(f"{course['name']}：{score_text}（{term}）")
        else:
            lines.append(f"{course['name']}：成绩已更新（{term}）")
    return title, "\n".join(lines)


def send_serverchan(config: dict[str, Any], title: str, content: str) -> None:
    serverchan = config.get("serverchan", {})
    if not isinstance(serverchan, dict) or not serverchan.get("enabled", False):
        return
    sendkey = str(serverchan.get("sendkey", "")).strip()
    if not sendkey:
        raise GradeAlertError("Server酱已启用，但 SendKey 为空")
    body = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
    safe_sendkey = urllib.parse.quote(sendkey, safe="")
    request = urllib.request.Request(
        f"https://sctapi.ftqq.com/{safe_sendkey}.send",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw_result = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as error:
        raise GradeAlertError(f"Server酱请求失败：{error}") from error
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError as error:
        raise GradeAlertError("Server酱返回了无法识别的响应") from error
    if result.get("code") not in {0, 200}:
        message = result.get("message") or result.get("msg") or result
        raise GradeAlertError(f"Server酱拒绝了消息：{message}")


def send_notification(config: dict[str, Any], changes: list[dict[str, Any]]) -> None:
    include_score = bool(config.get("include_score_in_notification", True))
    title, content = build_message(changes, include_score)
    print("\n" + content + "\n")
    send_serverchan(config, title, content)


def state_from_courses(courses: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked_at": now_text(),
        "courses": {course["key"]: course for course in courses},
    }


def check_once(page: Any, config: dict[str, Any], show_courses: bool = False) -> int:
    payload = fetch_score_payload(page)
    courses = normalize_courses(payload)
    current_state = state_from_courses(courses)
    current = current_state["courses"]

    if show_courses:
        for course in courses:
            print(
                f"{course['year'] or '?'}-{course['semester'] or '?'} "
                f"{course['name']} | {course['score'] or '暂无成绩'}"
            )

    if not STATE_PATH.exists():
        write_json(STATE_PATH, current_state)
        print(f"首次查询成功，共读取 {len(courses)} 门课程；已建立基线，不发送提醒。")
        if config.get("notify_on_first_run", False):
            initial = [
                {"kind": "new", "old_score": None, "course": course}
                for course in courses
                if has_score(course)
            ]
            if initial:
                send_notification(config, initial)
        return 0

    previous_state = read_json(STATE_PATH)
    previous = previous_state.get("courses", {})
    if not isinstance(previous, dict):
        raise GradeAlertError("last_scores.json 中的 courses 格式不正确")
    changes = detect_changes(previous, current)
    if changes:
        # Save only after notifications succeed, so a transient push failure can
        # be retried during the next polling cycle.
        send_notification(config, changes)
        write_json(STATE_PATH, current_state)
        return len(changes)

    write_json(STATE_PATH, current_state)
    print(f"[{now_text()}] 查询成功，共 {len(courses)} 门课程，没有发现成绩变化。")
    return 0


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def schema_for_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): type_name(item) for key, item in sorted(value.items())}


def build_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    score = data.get("score") if isinstance(data.get("score"), dict) else {}
    cjxx = score.get("cjxx") if isinstance(score.get("cjxx"), list) else []
    score_lists = score.get("scoreLists") if isinstance(score.get("scoreLists"), list) else []
    return {
        "generated_at": now_text(),
        "note": "仅包含字段名和数据类型，不包含课程名、成绩、教师或登录令牌。",
        "payload_fields": schema_for_mapping(payload),
        "data_fields": schema_for_mapping(data),
        "score_fields": schema_for_mapping(score),
        "cjxx_item_fields": schema_for_mapping(cjxx[0] if cjxx else {}),
        "scoreLists_item_fields": schema_for_mapping(score_lists[0] if score_lists else {}),
    }


def command_login(config: dict[str, Any]) -> None:
    print("浏览器将保持打开。请在页面中完成北大统一身份认证和可能出现的手机令牌验证。")
    print("进入成绩页后点击一次“查询”，确认能看到成绩，再回到终端按 Enter。")
    with browser_page(config, headed=True) as page:
        input("完成后按 Enter 继续验证：")
        payload = fetch_score_payload(page)
        courses = normalize_courses(payload)
        print(f"登录验证成功，接口返回 {len(courses)} 门课程。")


def command_check(config: dict[str, Any], headed: bool, show_courses: bool) -> None:
    with browser_page(config, headed=headed) as page:
        check_once(page, config, show_courses=show_courses)


def command_watch(config: dict[str, Any], headed: bool) -> None:
    interval = int(config.get("poll_seconds", 900))
    print(f"开始监控，每 {interval} 秒查询一次。按 Ctrl+C 停止。")
    with browser_page(config, headed=headed) as page:
        while True:
            try:
                check_once(page, config)
            except LoginRequired:
                raise
            except GradeAlertError as error:
                print(f"[{now_text()}] 本轮查询失败：{error}", file=sys.stderr)
            time.sleep(interval)


def command_diagnose(config: dict[str, Any], headed: bool) -> None:
    with browser_page(config, headed=headed) as page:
        payload = fetch_score_payload(page)
    write_json(DIAGNOSTICS_PATH, build_diagnostics(payload))
    print(f"已生成不含成绩值和登录令牌的诊断文件：{DIAGNOSTICS_PATH}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="北大树洞成绩变化提醒")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="创建本地配置文件")
    subparsers.add_parser("login", help="打开浏览器，手动完成登录和身份验证")

    check_parser = subparsers.add_parser("check", help="查询一次并比较成绩变化")
    check_parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    check_parser.add_argument("--show", action="store_true", help="在终端显示当前课程与成绩")

    watch_parser = subparsers.add_parser("watch", help="持续定时查询")
    watch_parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")

    diagnose_parser = subparsers.add_parser("diagnose", help="生成不含成绩值的接口结构诊断")
    diagnose_parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            init_config()
            return 0
        config = load_config()
        if args.command == "login":
            command_login(config)
        elif args.command == "check":
            command_check(config, headed=args.headed, show_courses=args.show)
        elif args.command == "watch":
            command_watch(config, headed=args.headed)
        elif args.command == "diagnose":
            command_diagnose(config, headed=args.headed)
        return 0
    except KeyboardInterrupt:
        print("\n已停止。")
        return 130
    except LoginRequired as error:
        print(f"需要重新登录：{error}", file=sys.stderr)
        return 2
    except GradeAlertError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
