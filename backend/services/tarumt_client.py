from __future__ import annotations

from urllib.parse import urljoin

import html
import re
import requests

from bs4 import BeautifulSoup


class TARUMTLoginError(Exception):
    """Raised when TAR UMT authentication fails."""


class TARUMTNavigationError(Exception):
    """Raised when timetable page cannot be discovered."""


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

    def _get(
        self,
        url: str,
        **kwargs
    ) -> requests.Response:

        response = self.session.get(
            url,
            timeout=30,
            allow_redirects=True,
            **kwargs
        )

        response.raise_for_status()

        return response

    # -----------------------------------------------------
    # LOGIN
    # -----------------------------------------------------

    def login(
        self,
        student_id: str,
        password: str
    ) -> None:

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
            or "loginProcess.jsp"
        )

        login_action_url = urljoin(
            login_page.url,
            form_action
        )

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

        response = self.session.post(
            login_action_url,
            data=payload,
            headers={
                "Referer": login_page.url
            },
            timeout=30,
            allow_redirects=True
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

        if success_count < 2:

            # One extra authenticated request is useful
            # because some login processors redirect.
            test_page = self._get(
                self.TIMETABLE_SESSION_URL
            )

            test_text = (
                test_page.text.lower()
            )

            if (
                "login id" in test_text
                and
                "password" in test_text
            ):

                raise TARUMTLoginError(
                    "Login did not create an authenticated TAR UMT session."
                )

            response_text = (
                test_page.text
            )

        self.logged_in = True

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

    def discover_timetable_url(self) -> str:

        if not self.logged_in:
            raise TARUMTLoginError(
                "TAR UMT session is not authenticated."
            )

        # =====================================================
        # Open TAR UMT My Timetable session page
        # =====================================================

        response = self._get(
            self.TIMETABLE_SESSION_URL
        )

        html_content = response.text


        # =====================================================
        # Verify authenticated session
        # =====================================================

        lower_html = html_content.lower()

        if (
            "logout.jsp" not in lower_html
            and
            "user-info" not in lower_html
        ):
            self.logged_in = False

            raise TARUMTLoginError(
                "TAR UMT authenticated session was not found."
            )

        # =====================================================
        # TAR UMT viewSession.jsp uses:
        #
        # getTimetable(
        #   'EE87EAF9-42BF-4D1A-A3BA-6E694BD60AB0',
        #   '202605',
        #   'KL'
        # )
        #
        # Find ALL accessible timetable buttons.
        # =====================================================

        matches = re.findall(
            r"""
            getTimetable
            \(
                \s*['"]([^'"]+)['"]
                \s*,\s*
                ['"](\d{6})['"]
                \s*,\s*
                ['"]([^'"]+)['"]
                \s*
            \)
            """,
            html_content,
            re.IGNORECASE | re.VERBOSE
        )

        if not matches:

            raise TARUMTNavigationError(
                "TAR UMT login succeeded, but no accessible "
                "timetable session was found in viewSession.jsp."
            )

        # =====================================================
        # TAR UMT lists newest accessible session first.
        #
        # Example:
        # 202605
        # 202601
        # 202505
        #
        # So take the first View Timetable button.
        # =====================================================

        fsid, fsession, fbrncd = matches[0]

        fsid = fsid.strip()
        fsession = fsession.strip()
        fbrncd = fbrncd.strip()

        if not fsid:

            raise TARUMTNavigationError(
                "TAR UMT returned an empty fsid."
            )

        # =====================================================
        # Construct exactly the URL used by TAR UMT JavaScript
        # =====================================================

        from urllib.parse import urlencode

        timetable_url = urljoin(
            response.url,
            "viewTimetable.jsp"
        )

        params = {
            "fsid": fsid,
            "fsession": fsession,
            "fbrncd": fbrncd
        }

        timetable_url += (
            "?"
            + urlencode(params)
        )

        self.last_timetable_url = timetable_url


        return timetable_url

        # =====================================================
        # METHOD 2
        #
        # Search JavaScript / onclick / raw HTML for:
        #
        # viewTimetable.jsp?fsid=XXX
        # &fsession=202605
        # &fbrncd=KL
        # =====================================================

        timetable_match = re.search(
            r"""
            viewTimetable\.jsp
            \?
            [^"'<>]*?
            fsid=([^&"'<>]+)
            &
            fsession=(\d{6})
            (?:&
            fweek=(\d+))?
            &
            fbrncd=([^&"'<>]+)
            """,
            html_content,
            re.IGNORECASE
            | re.VERBOSE
        )

        if timetable_match:

            fsid = (
                timetable_match
                .group(1)
                .strip()
            )

            fsession = (
                timetable_match
                .group(2)
                .strip()
            )

            fweek = (
                timetable_match
                .group(3)
            )

            fbrncd = (
                timetable_match
                .group(4)
                .strip()
            )

            if fsid:

                from urllib.parse import urlencode

                params = {
                    "fsid": fsid,
                    "fsession": fsession,
                    "fbrncd": fbrncd
                }

                if fweek:
                    params["fweek"] = fweek

                timetable_url = urljoin(
                    response.url,
                    "viewTimetable.jsp"
                )

                timetable_url += (
                    "?"
                    + urlencode(params)
                )

                self.last_timetable_url = (
                    timetable_url
                )



                return timetable_url

        # =====================================================
        # METHOD 3
        #
        # Look for TAR UMT's JavaScript:
        #
        # getWeek(
        #   'EE87EAF9-...',
        #   '202605',
        #   'KL'
        # )
        #
        # We already know from your real timetable response
        # that TAR UMT uses this structure.
        # =====================================================

        getweek_match = re.search(
            r"""
            getWeek
            \(
            \s*
            ['"]([^'"]+)['"]
            \s*,\s*
            ['"](\d{6})['"]
            \s*,\s*
            ['"]([^'"]+)['"]
            \s*
            \)
            """,
            html_content,
            re.IGNORECASE
            | re.VERBOSE
        )

        if getweek_match:

            fsid = (
                getweek_match
                .group(1)
                .strip()
            )

            fsession = (
                getweek_match
                .group(2)
                .strip()
            )

            fbrncd = (
                getweek_match
                .group(3)
                .strip()
            )

            if not fsid:

                raise TARUMTNavigationError(
                    "TAR UMT getWeek() was found "
                    "but fsid is empty."
                )

            # ---------------------------------------------
            # Find selected/current week if it exists
            # ---------------------------------------------

            selected_week = (
                soup.select_one(
                    "#fweek option[selected]"
                )
            )

            fweek = None

            if selected_week:

                value = (
                    selected_week
                    .get("value")
                )

                if value:
                    fweek = value.strip()

            from urllib.parse import urlencode

            params = {
                "fsid": fsid,
                "fsession": fsession,
                "fbrncd": fbrncd
            }

            if fweek:
                params["fweek"] = fweek

            timetable_url = urljoin(
                response.url,
                "viewTimetable.jsp"
            )

            timetable_url += (
                "?"
                + urlencode(params)
            )

            self.last_timetable_url = (
                timetable_url
            )



            return timetable_url

        # =====================================================
        # Nothing valid found.
        #
        # IMPORTANT:
        # Never create:
        #
        # viewTimetable.jsp?fsid=
        #
        # anymore.
        # =====================================================

        raise TARUMTNavigationError(
            "TAR UMT login appears successful, but no "
            "complete timetable parameters were found in "
            "viewSession.jsp. Debug response saved as "
            "data/temp/view_session_response.html."
        )

        if not self.logged_in:
            raise TARUMTLoginError(
                "TAR UMT session is not authenticated."
            )

        response = self._get(
            self.TIMETABLE_SESSION_URL
        )

        # Sometimes navigation itself may already
        # redirect to timetable.

        if "viewTimetable.jsp" in response.url:

            self.last_timetable_url = (
                response.url
            )

            return response.url

        soup = BeautifulSoup(
            response.text,
            "lxml"
        )

        # ---------------------------------------------
        # Method 1:
        # Look for normal timetable hyperlink.
        # ---------------------------------------------

        for link in soup.find_all(
            "a",
            href=True
        ):

            href = html.unescape(
                link["href"]
            )

            if "viewTimetable.jsp" in href:

                url = urljoin(
                    response.url,
                    href
                )

                self.last_timetable_url = url

                return url

        # ---------------------------------------------
        # Method 2:
        # Look inside forms.
        # ---------------------------------------------

        for form in soup.find_all("form"):

            action = (
                form.get("action")
                or ""
            )

            if "viewTimetable.jsp" not in action:
                continue

            params = {}

            for field in form.find_all(
                ["input", "select"]
            ):

                name = field.get("name")

                if not name:
                    continue

                if field.name == "select":

                    selected = field.find(
                        "option",
                        selected=True
                    )

                    if not selected:
                        selected = (
                            field.find("option")
                        )

                    if selected:

                        params[name] = (
                            selected.get("value")
                            or ""
                        )

                else:

                    params[name] = (
                        field.get("value")
                        or ""
                    )

            base = urljoin(
                response.url,
                action
            )

            if params:

                query = "&".join(
                    f"{key}={value}"
                    for key, value
                    in params.items()
                )

                base = (
                    f"{base}?{query}"
                )

            self.last_timetable_url = base

            return base

        # ---------------------------------------------
        # Method 3:
        # Search raw HTML / JavaScript.
        # ---------------------------------------------

        raw_match = re.search(
            r"""viewTimetable\.jsp\?[^"'<>\\\s]+""",
            response.text,
            re.IGNORECASE
        )

        if raw_match:

            relative_url = html.unescape(
                raw_match.group(0)
            )

            url = urljoin(
                response.url,
                relative_url
            )

            self.last_timetable_url = url

            return url

        # ---------------------------------------------
        # Method 4:
        # Find fsid / session / branch separately.
        # ---------------------------------------------

        fsid_match = re.search(
            r"""fsid[='":\s]+([A-F0-9-]{20,})""",
            response.text,
            re.IGNORECASE
        )

        session_match = re.search(
            r"""fsession[='":\s]+(\d{6})""",
            response.text,
            re.IGNORECASE
        )

        branch_match = re.search(
            r"""fbrncd[='":\s]+([A-Z]{2,5})""",
            response.text,
            re.IGNORECASE
        )

        if (
            fsid_match
            and
            session_match
        ):

            fsid = (
                fsid_match.group(1)
            )

            semester = (
                session_match.group(1)
            )

            branch = (
                branch_match.group(1)
                if branch_match
                else "KL"
            )

            url = urljoin(
                response.url,
                (
                    "viewTimetable.jsp"
                    f"?fsid={fsid}"
                    f"&fsession={semester}"
                    f"&fbrncd={branch}"
                )
            )

            self.last_timetable_url = url

            return url

        raise TARUMTNavigationError(
            "Authenticated successfully, but the current "
            "timetable URL could not be discovered from "
            "courseReg/viewSession.jsp."
        )

    # -----------------------------------------------------
    # Fetch live timetable HTML
    # -----------------------------------------------------

    def fetch_latest_timetable_html(
        self
    ) -> str:

        timetable_url = (
            self.discover_timetable_url()
        )


        response = self._get(
            timetable_url
        )

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

        if (
            "logout.jsp" not in lower_text
            and
            "user-info" not in lower_text
        ):

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

                self.session.get(
                    logout_url,
                    timeout=10
                )

        except requests.RequestException:
            pass

        finally:

            self.session.close()

            self.logged_in = False