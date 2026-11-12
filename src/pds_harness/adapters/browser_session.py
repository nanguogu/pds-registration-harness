from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pds_harness.domain.errors import PDSAPIError


@dataclass(frozen=True)
class BrowserSessionConfig:
    profile_dir: Path
    login_url: str = "https://pds.pd-sts.com/ycs-v2-back/"
    channel: str = "chrome"
    headless: bool = False
    credentials_env_path: Path | None = None


class PDSBrowserSession:
    """A visible, user-controlled PDS login session.

    The harness never types or stores credentials. It only reads `pro_token`
    from the page's sessionStorage after the user has logged in manually.
    """

    def __init__(self, config: BrowserSessionConfig) -> None:
        self.config = config
        self._playwright = None
        self._context = None
        self._page = None

    def open(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed; install the 'browser' extra"
            ) from exc

        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.profile_dir),
                channel=self.config.channel,
                headless=self.config.headless,
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(self.config.login_url, wait_until="domcontentloaded")
            if not self.is_authenticated():
                self._attempt_auto_login()
            if self.is_authenticated():
                self.minimize()
        except Exception:
            self.close()
            raise

    def authorization_token(self) -> str:
        if self._page is None:
            raise PDSAPIError("PDS browser session is not open")
        token = self._page.evaluate("() => sessionStorage.getItem('pro_token') || ''")
        if not isinstance(token, str) or not token.strip():
            raise PDSAPIError("PDS login is incomplete or the session has expired")
        token = token.strip()
        if any(character in token for character in "\r\n"):
            raise PDSAPIError("PDS session token has an invalid format")
        return token

    def is_authenticated(self) -> bool:
        try:
            self.authorization_token()
        except PDSAPIError:
            return False
        return True

    def minimize(self) -> None:
        """Minimize the dedicated Chrome window after authentication."""

        if self._context is None or self._page is None or self.config.headless:
            return
        try:
            cdp = self._context.new_cdp_session(self._page)
            window = cdp.send("Browser.getWindowForTarget")
            cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window["windowId"],
                    "bounds": {"windowState": "minimized"},
                },
            )
            cdp.detach()
        except Exception:
            # Window management is a convenience and must never invalidate an
            # otherwise healthy authenticated PDS session.
            return

    def _attempt_auto_login(self) -> None:
        if self._page is None or self.config.credentials_env_path is None:
            return
        credentials = _read_credentials(self.config.credentials_env_path)
        if credentials is None:
            return
        username, password = credentials
        username_input = self._page.locator(
            'input[placeholder*="用户名"], input[placeholder*="账号"], input[type="text"]'
        ).first
        password_input = self._page.locator('input[type="password"]').first
        login_button = self._page.locator(
            '.submit_field:has-text("登录"), button:has-text("登录"), '
            '.el-button--primary:has-text("登录")'
        ).first
        if not (
            username_input.count()
            and password_input.count()
            and login_button.count()
        ):
            return
        username_input.fill(username)
        password_input.fill(password)
        login_button.click()
        try:
            self._page.wait_for_function(
                "() => !!sessionStorage.getItem('pro_token')", timeout=30_000
            )
        except Exception:
            # CAPTCHA, changed selectors, or rejected credentials fall back to
            # the existing visible manual-login flow without exposing secrets.
            return

    def close(self) -> None:
        def already_closed(exc: Exception) -> bool:
            return type(exc).__name__ == "TargetClosedError" or "closed" in str(
                exc
            ).lower()

        error: Exception | None = None
        try:
            if self._context is not None:
                try:
                    self._context.close()
                except Exception as exc:
                    if not already_closed(exc):
                        error = exc
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception as exc:
                    if not already_closed(exc) and error is None:
                        error = exc
        finally:
            self._context = None
            self._playwright = None
            self._page = None
        if error is not None:
            raise error

    def __enter__(self) -> "PDSBrowserSession":
        self.open()
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def _read_credentials(path: Path) -> tuple[str, str] | None:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    username = values.get("用户名") or values.get("PDS_USERNAME")
    password = values.get("密码") or values.get("PDS_PASSWORD")
    if not username or not password:
        return None
    return username, password

