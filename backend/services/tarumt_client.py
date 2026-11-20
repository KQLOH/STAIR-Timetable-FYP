from __future__ import annotations

from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode, urlunsplit

import html
import re
import requests

from bs4 import BeautifulSoup


class TARUMTLoginError(Exception):
    """Raised when TAR UMT authentication fails."""


class TARUMTNavigationError(Exception):
    """Raised when timetable page cannot be discovered."""
    reason = "NAVIGATION_UNCONFIRMED"


class TARUMTClient:

    BASE_URL = "https://web.tarc.edu.my/portal/"

    LOGIN_URL = urljoin(
        BASE_URL,
        "login.jsp"
    )

    TIMETABLE_SESSION_URL = urljoin(
        BASE_URL,
        "courseReg/viewSession.jsp"
    )

    def __init__(self):

        self.stage = "LOGIN_PAGE"
        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/151.0.0.0 "
                    "Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": (
                    "en-US,en;q=0.9"
                ),
            }
        )

        self.logged_in = False

        self.student_name = None

        self.last_timetable_url = None

    # -----------------------------------------------------
    # HTTP helper
    # -----------------------------------------------------

    def _trusted_url(self, url: str) -> str:
        reason, category = 'INVALID_TARGET', 'MALFORMED_URL'
        safe = False
        try:
            if isinstance(url, str) and not any(ord(c) < 32 or ord(c) == 127 for c in url):
                target, origin = urlsplit(url), urlsplit(self.BASE_URL)
                if target.scheme != 'https':
                    reason, category = 'HTTPS_REQUIRED', 'INSECURE_SCHEME'
                elif target.hostname != origin.hostname or target.port not in (None, 443):
                    reason, category = 'ORIGIN_NOT_ALLOWED', 'UNTRUSTED_ORIGIN'
                elif target.username is not None or target.password is not None:
                    category = 'USERINFO'
                elif url.startswith(' '):
                    category = 'LEADING_SPACE'
                else:
                    safe = True
            elif isinstance(url, str):
                category = 'CONTROL_CHARACTER'
        except ValueError:
            pass
        if not safe:
            error = TARUMTNavigationError('Institutional request refused: an approved same-origin HTTPS address is required.')
            error.reason, error.category = reason, category
            raise error
        # Match Requests' preparation of path/query spaces and Unicode, after
        # checking the raw authority and controls. Never normalize an unsafe
        # origin into an allowed one. Fragments are not sent in HTTP requests.
        return requests.utils.requote_uri(urlunsplit(target._replace(fragment='')))

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        options = dict(kwargs)
        options['timeout'], options['allow_redirects'] = options.get('timeout', 30), False
        for _ in range(9):
            try:
                url = self._trusted_url(url)
            except TARUMTNavigationError as error:
                error.step, error.form = 'REQUEST_DESTINATION_VALIDATION', 'ABSOLUTE'
                raise
            self.step = 'HTTP_EXECUTION'
            response = (self.session.post if method == 'POST' else self.session.get)(url, **options)
            try:
                response.url = self._trusted_url(response.url)
            except TARUMTNavigationError as error:
                error.step, error.form = 'RESPONSE_DESTINATION_VALIDATION', 'ABSOLUTE'
                response.close()
                raise
            response.raise_for_status()
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get('Location')
            if not location:
                response.close()
                error = TARUMTNavigationError('Institutional redirect could not be confirmed.')
                error.reason, error.step = 'REDIRECT_MISSING', 'REDIRECT_RESOLUTION'
                raise error
            form = 'UNKNOWN'
            try:
                # urljoin/urlsplit remove some controls; reject them before
                # resolution so they can never disappear through normalization.
                if any(ord(c) < 32 or ord(c) == 127 for c in location):
                    error = TARUMTNavigationError('Institutional redirect address was invalid.')
                    error.reason, error.category = 'INVALID_TARGET', 'CONTROL_CHARACTER'
                    raise error
                parts = urlsplit(location)
                form = ('PROTOCOL_RELATIVE' if location.startswith('//') else 'ABSOLUTE' if parts.scheme else
                        'QUERY_ONLY' if location.startswith('?') else 'FRAGMENT_ONLY' if location.startswith('#') else 'RELATIVE')
                next_url = urljoin(response.url, location)
            except (ValueError, TARUMTNavigationError) as failure:
                error = failure if isinstance(failure, TARUMTNavigationError) else TARUMTNavigationError('Institutional redirect address was invalid.')
                error.reason = 'INVALID_TARGET'
                error.category = getattr(error, 'category', 'MALFORMED_URL')
                error.step, error.form = 'REDIRECT_RESOLUTION', form
                raise error from None
            finally:
                response.close()
            try:
                next_url = self._trusted_url(next_url)
            except TARUMTNavigationError as error:
                error.step, error.form = 'REDIRECT_VALIDATION', form
                raise
            if response.status_code == 303 or (method == 'POST' and response.status_code in (301, 302)):
                method = 'GET'
                options.pop('data', None)
                options.pop('json', None)
            url = next_url
        error = TARUMTNavigationError('Institutional redirect limit reached. Please reconnect explicitly.')
        error.reason, error.step = 'REDIRECT_LIMIT', 'REDIRECT_VALIDATION'
        raise error

    def _get(self, url: str, **kwargs) -> requests.Response:
        return self._request('GET',url,**kwargs)

    # -----------------------------------------------------
    # LOGIN
    # -----------------------------------------------------

    def login(
        self,
        student_id: str,
        password: str
    ) -> None:

        self.stage = "LOGIN_PAGE"
        self.logged_in = False
        self.student_name = None
        self.last_timetable_url = None
        student_id = student_id.strip()

        if not student_id:
            raise TARUMTLoginError(
                "Student ID is required."
            )

        if not password:
            raise TARUMTLoginError(
                "Password is required."
            )

        # ---------------------------------------------
        # Step 1:
        # GET official TAR UMT login page
        # ---------------------------------------------

        login_page = self._get(
            self.LOGIN_URL
        )

        soup = BeautifulSoup(
            login_page.text,
            "lxml"
        )

        # ---------------------------------------------
        # Step 2:
        # Find the form that actually contains
        # a password field.
        #
        # This avoids hardcoding TAR UMT field names.
        # ---------------------------------------------

        login_form = None

        password_input = None

        for form in soup.find_all("form"):

            pwd = form.find(
                "input",
                {
                    "type": re.compile(
                        "^password$",
                        re.I
                    )
                }
            )

            if pwd:
                login_form = form
                password_input = pwd
                break

        if login_form is None:
            raise TARUMTLoginError(
                "Could not locate the TAR UMT login form."
            )

        # ---------------------------------------------
        # Determine form POST target
        # ---------------------------------------------

        form_action = (
            login_form.get("action")
            or login_page.url
        )

        self.stage = 'LOGIN_SUBMISSION'
        form = 'UNKNOWN'
        try:
            if any(ord(c) < 32 or ord(c) == 127 for c in form_action):
                error = TARUMTNavigationError('Institutional form address was invalid.')
                error.category = 'CONTROL_CHARACTER'
                raise error
            parts = urlsplit(form_action)
            form = 'PROTOCOL_RELATIVE' if form_action.startswith('//') else 'ABSOLUTE' if parts.scheme else 'RELATIVE'
            login_action_url = urljoin(login_page.url, form_action)
        except (ValueError, TARUMTNavigationError) as failure:
            error = failure if isinstance(failure, TARUMTNavigationError) else TARUMTNavigationError('Institutional form address was invalid.')
            error.reason, error.step, error.form = 'INVALID_TARGET', 'FORM_ACTION_RESOLUTION', form
            error.category = getattr(error, 'category', 'MALFORMED_URL')
            raise error from None
        try:
            login_action_url = self._trusted_url(login_action_url)
        except TARUMTNavigationError as error:
            error.step, error.form = 'FORM_ACTION_VALIDATION', form
            raise

        # ---------------------------------------------
        # Preserve all hidden fields.
        #
        # Important if TAR UMT includes hidden
        # server-side form values.
        # ---------------------------------------------

        payload = {}

        for hidden_input in login_form.find_all(
            "input",
            type="hidden"
        ):

            name = hidden_input.get("name")

            if not name:
                continue

            payload[name] = (
                hidden_input.get("value")
                or ""
            )

        # ---------------------------------------------
        # Find username input.
        #
        # We deliberately search inside the same form
        # that contains the password field.
        # ---------------------------------------------

        username_input = None

        for input_element in login_form.find_all(
            "input"
        ):

            input_type = (
                input_element.get("type")
                or "text"
            ).lower()

            name = input_element.get("name")

            if not name:
                continue

            if input_type in (
                "text",
                "email"
            ):
                username_input = input_element
                break

        if username_input is None:
            raise TARUMTLoginError(
                "Could not locate the TAR UMT Student ID field."
            )

        username_name = (
            username_input.get("name")
        )

        password_name = (
            password_input.get("name")
        )

        if not password_name:
            raise TARUMTLoginError(
                "Password field has no form name."
            )

        payload[username_name] = student_id

        payload[password_name] = password

        # ---------------------------------------------
        # Preserve named submit button value
        # if TAR UMT uses one.
        # ---------------------------------------------

        submit_input = login_form.find(
            "input",
            {
                "type": re.compile(
                    "^submit$",
                    re.I
                )
            }
        )

        if submit_input:

            submit_name = (
                submit_input.get("name")
            )

            if submit_name:

                payload[submit_name] = (
                    submit_input.get("value")
                    or ""
                )

        # ---------------------------------------------
        # POST credentials directly to official portal.
        #
        # requests.Session keeps JSESSION/session cookies
        # in memory automatically.
        # ---------------------------------------------

        self.stage = "LOGIN_SUBMISSION"
        response = self._request(
            'POST', login_action_url,
            data=payload,
            headers={
                "Referer": login_page.url
            },
            timeout=30
        )

        response.raise_for_status()

        response_text = response.text

        # ---------------------------------------------
        # Detect obvious failed authentication.
        # ---------------------------------------------

        lower_text = (
            response_text.lower()
        )

        failure_signals = [
            "invalid login",
            "invalid password",
            "invalid login id",
            "entered an invalid",
        ]

        if any(
            signal in lower_text
            for signal in failure_signals
        ):

            raise TARUMTLoginError(
                "TAR UMT rejected the Student ID or password."
            )

        # ---------------------------------------------
        # Detect successful authenticated portal.
        #
        # Your authenticated response contains logout.jsp
        # and the user menu.
        # ---------------------------------------------

        success_signals = [
            "logout.jsp",
            "student intranet",
            "navbar-user",
            "user-info",
        ]

        success_count = sum(
            signal in lower_text
            for signal in success_signals
        )

        if success_count < 2 or not self._authenticated_page(response_text):

            # One extra authenticated request is useful
            # because some login processors redirect.
            self.stage = "LOGIN_CONFIRMATION"
            test_page = self._get(
                self.TIMETABLE_SESSION_URL
            )

            if not self._authenticated_page(test_page.text):
                raise TARUMTLoginError(
                    "Login did not create a confirmed authenticated TAR UMT session."
                )

            response_text = (
                test_page.text
            )

        self.logged_in = True
        self.stage = "AUTHENTICATED"

        self.student_name = (
            self._extract_student_name(
                response_text
            )
        )

    # -----------------------------------------------------
    # Student name
    # -----------------------------------------------------

    def _extract_student_name(
        self,
        html_content: str
    ) -> str | None:

        soup = BeautifulSoup(
            html_content,
            "lxml"
        )

        user_info = soup.select_one(
            ".user-info"
        )

        if not user_info:
            return None

        text_parts = [
            part.strip()
            for part in user_info.stripped_strings
        ]

        text_parts = [
            part
            for part in text_parts
            if part.lower()
            != "welcome,"
        ]

        if not text_parts:
            return None

        return " ".join(
            text_parts
        )

    # -----------------------------------------------------
    # Discover latest timetable URL
    # -----------------------------------------------------

    @staticmethod
    def _authenticated_page(content: str) -> bool:
        # A login/challenge form must never become an authenticated session.
        soup = BeautifulSoup(content, "lxml")
        if soup.find("input", type=re.compile("^password$", re.I)):
            return False
        lower = content.lower()
        return "logout.jsp" in lower or "user-info" in lower

    def _timetable_url(self, base: str, target: str) -> str | None:
        url = self._trusted_url(urljoin(base, html.unescape(target)))
        parts = urlsplit(url)
        if parts.path != urlsplit(self.TIMETABLE_SESSION_URL).path.replace("viewSession.jsp", "viewTimetable.jsp"):
            return None
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        params = dict(pairs)
        required = ("fsid", "fsession", "fbrncd")
        # Never invent identifiers/branch or choose among conflicting values.
        if any(sum(k == key for k, _ in pairs) != 1 or not params.get(key, "").strip() for key in required):
            return None
        if not re.fullmatch(r"\d{6}", params["fsession"]):
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))

    def discover_timetable_url(self) -> str:
        if not self.logged_in:
            raise TARUMTLoginError("TAR UMT session is not authenticated.")
        self.stage = "SESSION_PAGE"
        response = self._get(self.TIMETABLE_SESSION_URL)
        self.stage = "SESSION_NAVIGATION"
        if not self._authenticated_page(response.text):
            self.logged_in = False
            raise TARUMTLoginError("TAR UMT authenticated session was not found.")
        soup = BeautifulSoup(response.text, "lxml")
        candidates = []
        # Navigation may already have reached the actual timetable.
        if urlsplit(response.url).path.endswith("/viewTimetable.jsp"):
            candidates.append(response.url)
        # Preserve the portal's distinct operations: session opening does not
        # use a selected week. Only the getWeek fallback uses that source value.
        # Prefer getTimetable even when a getWeek call occurs earlier in HTML.
        content = html.unescape(response.text)
        for function in ("getTimetable", "getWeek"):
            pattern = rf"{function}\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"](\d{{6}})['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
            for match in re.finditer(pattern, content, re.I):
                params = dict(zip(("fsid", "fsession", "fbrncd"), (value.strip() for value in match.groups())))
                if function == "getWeek":
                    week = soup.select_one("#fweek option[selected]")
                    if week and week.get("value"):
                        params["fweek"] = week["value"].strip()
                candidates.append("viewTimetable.jsp?" + urlencode(params))
        for link in soup.find_all("a", href=True):
            if "viewTimetable.jsp" in link["href"]:
                candidates.append(link["href"])
        for form in soup.find_all("form"):
            action = form.get("action", "")
            # Do not turn a POST form into a guessed GET operation.
            if "viewTimetable.jsp" not in action or form.get("method", "get").lower() != "get":
                continue
            parts = urlsplit(urljoin(response.url, action))
            params = parse_qsl(parts.query, keep_blank_values=True)
            for field in form.find_all(["input", "select"]):
                if not field.get("name") or field.has_attr("disabled"):
                    continue
                if field.name == "select":
                    selected = field.find("option", selected=True) or field.find("option")
                    if selected is None:
                        continue
                    value = selected.get("value", selected.get_text())
                else:
                    if field.get("type", "text").lower() not in ("hidden", "text"):
                        continue
                    value = field.get("value", "")
                params.append((field["name"], value))
            candidates.append(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)))
        for candidate in candidates:
            url = self._timetable_url(response.url, candidate)
            if url:
                self.last_timetable_url = url
                return url
        raise TARUMTNavigationError("No complete, source-supported timetable link was found in the authenticated institutional page.")

    # -----------------------------------------------------
    # Fetch live timetable HTML
    # -----------------------------------------------------

    def fetch_latest_timetable_html(
        self
    ) -> str:

        timetable_url = (
            self.discover_timetable_url()
        )


        self.stage = "TIMETABLE_FETCH"
        response = self._get(
            timetable_url
        )
        self.stage = "TIMETABLE_CONTENT"

        html_content = (
            response.text
            or ""
        )




        # =====================================================
        # Never silently pass an empty response to parser
        # =====================================================

        if not html_content.strip():

            raise TARUMTNavigationError(
                "TAR UMT returned an empty timetable response. "
                f"Final URL: {response.url}"
            )

        lower_text = (
            html_content.lower()
        )

        # =====================================================
        # Check authentication
        # =====================================================

        if not self._authenticated_page(html_content):

            self.logged_in = False

            raise TARUMTLoginError(
                "The TAR UMT authenticated session "
                "was not present on the timetable response."
            )

        # =====================================================
        # Confirm timetable content
        # =====================================================

        has_week_table = (
            "semester timetable by week"
            in lower_text
        )

        has_course_table = (
            "semester timetable by course"
            in lower_text
        )

        if not (
            has_week_table
            or
            has_course_table
        ):

            raise TARUMTNavigationError(
                "A TAR UMT page was returned, but it "
                "does not appear to contain timetable data. "
                f"Final URL: {response.url}"
            )

        self.last_timetable_url = (
            response.url
        )

        return html_content

    # -----------------------------------------------------
    # Logout / close
    # -----------------------------------------------------

    def close(self):

        try:

            if self.logged_in:

                logout_url = urljoin(
                    self.BASE_URL,
                    "logout.jsp"
                )

                self._get(
                    logout_url,
                    timeout=10
                )

        except (requests.RequestException, TARUMTNavigationError):
            pass

        finally:

            try:
                self.session.cookies.clear()
            finally:
                self.session.close()
                self.logged_in = False
                self.student_name = None
                self.last_timetable_url = None