import queue
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from grade_alert import (  # noqa: E402
    build_diagnostics,
    build_message,
    detect_changes,
    normalize_courses,
    process_score_payload,
    auto_login_treehole,
    SecondFactorRequired,
    TREEHOLE_LOGIN_URL,
    send_serverchan,
)
from local_secrets import delete_credentials, load_credentials, save_credentials  # noqa: E402
from grade_alert_gui import GradeAlertApp, QueueWriter  # noqa: E402


class NormalizeCoursesTests(unittest.TestCase):
    def test_undergraduate_payload(self):
        payload = {
            "success": True,
            "data": {
                "score": {
                    "success": True,
                    "xslb": "bk",
                    "cjxx": [
                        {
                            "kch": "LAW001",
                            "kcmc": "测试课程",
                            "xnd": "2025",
                            "xq": "2",
                            "xf": "3.0",
                            "xqcj": "P",
                            "jd": None,
                            "kclbmc": "专业课",
                            "skjsxm": "测试教师",
                            "jxbh": "CLASS-1",
                        }
                    ],
                }
            },
        }
        courses = normalize_courses(payload)
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0]["score"], "P")
        self.assertEqual(courses[0]["course_id"], "LAW001")
        self.assertFalse(courses[0]["graduate"])

    def test_graduate_payload(self):
        payload = {
            "data": {
                "score": {
                    "success": True,
                    "xslb": "yjs",
                    "scoreLists": [
                        {
                            "kch": "GR001",
                            "kcmc": "研究生课程",
                            "xnd": "2025",
                            "xq": "2",
                            "xf": 2,
                            "cj": 92,
                            "jd": 4,
                            "kclb": "学位课",
                        }
                    ],
                }
            }
        }
        course = normalize_courses(payload)[0]
        self.assertEqual(course["score"], "92")
        self.assertEqual(course["type"], "学位课")
        self.assertTrue(course["graduate"])


class ChangeDetectionTests(unittest.TestCase):
    def test_detects_new_and_updated_scores(self):
        previous = {
            "a": {"key": "a", "name": "课程 A", "score": None},
            "b": {"key": "b", "name": "课程 B", "score": "80"},
        }
        current = {
            "a": {"key": "a", "name": "课程 A", "score": "P"},
            "b": {"key": "b", "name": "课程 B", "score": "85"},
            "c": {"key": "c", "name": "课程 C", "score": None},
        }
        changes = detect_changes(previous, current)
        self.assertEqual([change["kind"] for change in changes], ["updated", "updated"])
        self.assertEqual(changes[1]["old_score"], "80")

    def test_message_can_hide_score(self):
        changes = [
            {
                "kind": "new",
                "old_score": None,
                "course": {
                    "name": "隐私课程",
                    "score": "95",
                    "year": "2025",
                    "semester": "2",
                },
            }
        ]
        _, content = build_message(changes, include_score=False)
        self.assertNotIn("95", content)
        self.assertIn("成绩已更新", content)

    def test_processes_existing_payload_without_fetching(self):
        payload = {
            "data": {
                "score": {
                    "success": True,
                    "xslb": "bk",
                    "cjxx": [
                        {
                            "kch": "LAW002",
                            "kcmc": "已有响应课程",
                            "xnd": "2025",
                            "xq": "2",
                            "xqcj": "P",
                        }
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "last_scores.json"
            with patch("grade_alert.STATE_PATH", state_path):
                result = process_score_payload(payload, {"notify_on_first_run": False})

            self.assertEqual(result, 0)
            self.assertTrue(state_path.exists())


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostics_contains_types_not_values(self):
        payload = {
            "data": {
                "score": {
                    "success": True,
                    "cjxx": [{"kcmc": "秘密课程", "xqcj": "99"}],
                }
            }
        }
        diagnostics = build_diagnostics(payload)
        serialized = str(diagnostics)
        self.assertIn("kcmc", serialized)
        self.assertNotIn("秘密课程", serialized)
        self.assertNotIn("99", serialized)


class ServerChanTests(unittest.TestCase):
    def test_serverchan_uses_sendkey_and_form_fields(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"code": 0, "message": "SUCCESS"}'

        config = {
            "serverchan": {
                "enabled": True,
                "sendkey": "SCT_TEST_KEY",
            }
        }
        with patch("grade_alert.urllib.request.urlopen", return_value=FakeResponse()) as mocked:
            send_serverchan(config, "测试标题", "测试正文")

        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://sctapi.ftqq.com/SCT_TEST_KEY.send")
        form = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(form["title"], ["测试标题"])
        self.assertEqual(form["desp"], ["测试正文"])


class LocalSecretTests(unittest.TestCase):
    def test_dpapi_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.dat"
            save_credentials(path, "test-user", "test-password")
            self.assertEqual(
                load_credentials(path),
                {"username": "test-user", "password": "test-password"},
            )
            delete_credentials(path)
            self.assertFalse(path.exists())


class PersistentLogTests(unittest.TestCase):
    def test_queue_writer_appends_local_log(self):
        with tempfile.TemporaryDirectory() as directory:
            messages: queue.Queue[str] = queue.Queue()
            log_path = Path(directory) / "grade_alert.log"
            writer = QueueWriter(messages, log_path)

            writer.write("sanitized test message\n")

            self.assertEqual(messages.get_nowait(), "sanitized test message\n")
            self.assertEqual(
                log_path.read_text(encoding="utf-8"),
                "sanitized test message\n",
            )

    def test_gui_status_log_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            messages: queue.Queue[str] = queue.Queue()
            log_path = Path(directory) / "grade_alert.log"
            app = GradeAlertApp.__new__(GradeAlertApp)
            app.messages = messages
            app.writer = QueueWriter(messages, log_path)

            with patch("grade_alert_gui.now_text", return_value="2026-07-04T12:00:00+08:00"):
                app.log("持续监控已开始")

            persisted = log_path.read_text(encoding="utf-8")
            self.assertIn("持续监控已开始", persisted)
            self.assertEqual(messages.get_nowait(), persisted)


class AutoLoginTests(unittest.TestCase):
    class FakeLocator:
        def __init__(self, page, selector, visible=True):
            self.page = page
            self.selector = selector
            self.visible = visible
            self.value = ""
            self.checked = False

        def count(self):
            return 1

        def is_visible(self):
            return self.visible

        def fill(self, value):
            self.value = value

        def click(self):
            if self.selector == "#logon_button" and self.page.login_succeeds:
                self.page.url = "https://treehole.pku.edu.cn/web/webscore"

        def is_checked(self):
            return self.checked

        def check(self, force=False):
            if not self.visible:
                raise RuntimeError("hidden checkbox cannot be clicked")
            self.checked = True

        def evaluate(self, script):
            self.checked = True

        def inner_text(self):
            return ""

    class FakePage:
        def __init__(
            self,
            login_succeeds=True,
            otp_visible=True,
            navigation_evaluate_failures=0,
            login_redirect_aborts=0,
        ):
            self.url = "https://treehole.pku.edu.cn/web/webscore"
            self.login_succeeds = login_succeeds
            self.navigation_evaluate_failures = navigation_evaluate_failures
            self.login_redirect_aborts = login_redirect_aborts
            self.locators = {}
            for selector in (
                "#user_name",
                "#password",
                "#logon_button",
                "#msg",
            ):
                self.locators[selector] = AutoLoginTests.FakeLocator(self, selector)
            self.locators["#remTrust_check"] = AutoLoginTests.FakeLocator(
                self, "#remTrust_check", visible=False
            )
            self.locators["#otp_code"] = AutoLoginTests.FakeLocator(
                self, "#otp_code", visible=otp_visible
            )
            self.locators["#sms_code"] = AutoLoginTests.FakeLocator(
                self, "#sms_code", visible=False
            )
            self.locators["#valid_code"] = AutoLoginTests.FakeLocator(
                self, "#valid_code", visible=False
            )

        def evaluate(self, script, value=None):
            if script.strip().startswith("() => localStorage.getItem"):
                if self.navigation_evaluate_failures:
                    self.navigation_evaluate_failures -= 1
                    raise RuntimeError(
                        "Execution context was destroyed, most likely because of a navigation"
                    )
                return "Web_PKUHOLE_2.0.0_WEB_UUID_test"
            return {
                "kind": "response",
                "status": 200,
                "body": {"data": {"score": {"success": True, "xslb": "bk", "cjxx": []}}},
            }

        def goto(self, url, **kwargs):
            if url.startswith(TREEHOLE_LOGIN_URL):
                self.url = "https://iaaa.pku.edu.cn/iaaa/oauth.jsp?appID=PKU%20Helper"
                if self.login_redirect_aborts:
                    self.login_redirect_aborts -= 1
                    raise RuntimeError(
                        "Page.goto: net::ERR_ABORTED at redirect_iaaa_login"
                    )
            else:
                self.url = url

        def wait_for_selector(self, selector, **kwargs):
            return None

        def locator(self, selector):
            return self.locators[selector]

        def wait_for_timeout(self, timeout):
            return None

        def wait_for_url(self, pattern, **kwargs):
            return None

    def test_auto_login_fills_credentials_token_and_trust(self):
        page = self.FakePage(login_succeeds=True, otp_visible=True)
        payload = auto_login_treehole(
            page,
            "student-id",
            "password-value",
            phone_token="123456",
            trust_device=True,
        )
        self.assertIn("score", payload["data"])
        self.assertEqual(page.locators["#user_name"].value, "student-id")
        self.assertEqual(page.locators["#password"].value, "password-value")
        self.assertEqual(page.locators["#otp_code"].value, "123456")
        self.assertTrue(page.locators["#remTrust_check"].checked)

    def test_auto_login_reports_missing_second_factor(self):
        page = self.FakePage(login_succeeds=False, otp_visible=True)
        with self.assertRaises(SecondFactorRequired):
            auto_login_treehole(page, "student-id", "password-value")

    def test_auto_login_retries_uuid_read_after_navigation(self):
        page = self.FakePage(
            login_succeeds=True,
            otp_visible=False,
            navigation_evaluate_failures=1,
        )

        payload = auto_login_treehole(page, "student-id", "password-value")

        self.assertIn("score", payload["data"])
        self.assertEqual(page.navigation_evaluate_failures, 0)

    def test_auto_login_accepts_aborted_request_after_iaaa_redirect(self):
        page = self.FakePage(
            login_succeeds=True,
            otp_visible=False,
            login_redirect_aborts=1,
        )

        payload = auto_login_treehole(page, "student-id", "password-value")

        self.assertIn("score", payload["data"])
        self.assertEqual(page.login_redirect_aborts, 0)


if __name__ == "__main__":
    unittest.main()
