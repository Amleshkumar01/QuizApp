"""
Verify LNCTU AccSoft student credentials against the college ERP portal.

Passwords are never stored. Student name/email are scraped with BeautifulSoup
from StudentPersonalDetails.aspx (primary) and ParentDesk pages.
"""
from __future__ import annotations

import http.cookiejar
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; PlacementIQ-CollegeAuth/2.1)"
_LOGIN_FIELD_USER = "ctl00$cph1$txtStuUser"
_LOGIN_FIELD_PASS = "ctl00$cph1$txtStuPsw"
_LOGIN_BUTTON = "ctl00$cph1$btnStuLogin"
_ERROR_LABEL_ID = "ctl00_cph1_lblErrMsgStu"

# Exact AccSoft label IDs for student name (do NOT use generic lblName — matches Consent Form).
_NAME_ELEMENT_IDS = (
    "ctl00_cph1_lblStuName",
    "ctl00_cph1_lblPName",
    "ctl00_cph1_lblStudentName",
    "ctl00_cph1_lblStuFullName",
    "ctl00_cph1_lblSName",
    "ctl00_cph1_lblStudName",
    "ctl00_cph1_lblStuInfo",
    "ctl00_cph1_lblStudentInfo",
    "ctl00_cph1_lblWelComeName",
)

_HIDDEN_NAME_KEYS = (
    "hdnstuname",
    "hdnstudentname",
    "hdnpname",
    "hdnname",
    "hdnstudent",
)

_EMAIL_ELEMENT_IDS = (
    "ctl00_cph1_lblStuEmail",
    "ctl00_cph1_lblEmail",
    "ctl00_cph1_lblEmailId",
    "ctl00_cph1_lblStudentEmail",
)

_STUDENT_NAME_ROW_LABELS = (
    "student's name",
    "student name",
    "name of student",
    "ward name",
    "candidate name",
)

_STUDENT_NAME_INPUT_IDS = (
    "ctl00_cph1_txtStuName",
    "ctl00_cph1_txtStudentName",
    "ctl00_cph1_txtName",
    "ctl00_cph1_txtStudName",
)

_STUDENT_EMAIL_ROW_LABELS = (
    "student's email",
    "student email",
    "email id",
    "e-mail",
    "email",
)

_STUDENT_EMAIL_INPUT_IDS = (
    "ctl00_cph1_txtStuEmail",
    "ctl00_cph1_txtEmail",
    "ctl00_cph1_txtEmailId",
    "ctl00_cph1_txtStudentEmail",
)

_BAD_ELEMENT_ID_PARTS = (
    "consent",
    "form",
    "menu",
    "link",
    "btn",
    "button",
    "report",
    "fee",
    "payment",
    "attendance",
    "error",
    "msg",
    "captcha",
    "login",
    "signup",
    "forgot",
)

_UI_WORDS = {
    "consent",
    "form",
    "payment",
    "fee",
    "report",
    "attendance",
    "download",
    "click",
    "view",
    "submit",
    "login",
    "logout",
    "profile",
    "desk",
    "menu",
    "home",
    "back",
    "next",
    "student",
    "parent",
    "university",
    "institute",
    "accsoft",
    "sign",
    "register",
    "forgot",
    "password",
    "update",
    "status",
    "pending",
    "approved",
    "online",
    "portal",
    "welcome",
    "dashboard",
    "details",
    "information",
    "master",
    "slip",
    "receipt",
    "notice",
    "circular",
    "scholar",
    "no",
    "enrollment",
    "class",
    "section",
    "login",
    "personal",
    "details",
    "abcid",
}

_SKIP_URL_PARTS = (
    "consent",
    "forget",
    "signup",
    "register",
    "studentlogin",
)


@dataclass(frozen=True)
class CollegeAuthResult:
    success: bool
    enrollment_id: str = ""
    display_name: str = ""
    email: str = ""
    error_message: str = ""


def is_valid_student_name(value: str, enrollment_id: str = "") -> bool:
    """Public helper — reject menu labels like 'Consent Form'."""
    return _looks_like_person_name(value, enrollment_id)


def verify_college_login(enrollment_id: str, password: str) -> CollegeAuthResult:
    enrollment_id = (enrollment_id or "").strip()
    password = password or ""

    if not enrollment_id or not password:
        return CollegeAuthResult(
            success=False,
            error_message="Invalid Student Login ID or Password",
        )

    login_url = settings.COLLEGE_LOGIN_URL
    success_marker = settings.COLLEGE_SUCCESS_URL_MARKER
    timeout = settings.COLLEGE_AUTH_TIMEOUT

    try:
        session = _build_opener()
        login_html = _fetch_text(session, login_url, timeout=timeout)
        form_fields = _extract_hidden_fields(login_html)
        if not form_fields.get("__VIEWSTATE"):
            logger.warning(f"College login: missing VIEWSTATE for {enrollment_id}")
            return CollegeAuthResult(
                success=False,
                error_message="College login service is unavailable. Please try again later.",
            )

        post_data = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            **form_fields,
            "ctl00$cph1$rdbtnlType": "2",
            "ctl00$cph1$hdnSID": "",
            "ctl00$cph1$hdnSNO": "",
            "ctl00$cph1$hdnRDURL": "",
            _LOGIN_FIELD_USER: enrollment_id,
            _LOGIN_FIELD_PASS: password,
            _LOGIN_BUTTON: "Login »",
        }

        response_html, final_url = _post_form(session, login_url, post_data, timeout=timeout)
    except urllib.error.URLError as e:
        logger.error(f"College login: URLError for {enrollment_id}: {e}")
        return CollegeAuthResult(
            success=False,
            error_message="Unable to reach college login service. Please try again later.",
        )
    except TimeoutError as e:
        logger.error(f"College login: Timeout for {enrollment_id}: {e}")
        return CollegeAuthResult(
            success=False,
            error_message="College login timed out. Please try again.",
        )

    if _login_failed(response_html, final_url, login_url):
        logger.debug(f"College login: credentials failed for {enrollment_id}")
        return CollegeAuthResult(
            success=False,
            error_message="Invalid Student Login ID or Password",
        )

    if success_marker not in final_url and success_marker not in response_html:
        logger.warning(f"College login: success marker not found for {enrollment_id}")
        return CollegeAuthResult(
            success=False,
            error_message="Invalid Student Login ID or Password",
        )

    display_name = ""
    email = ""

    details_url = getattr(settings, "COLLEGE_STUDENT_DETAILS_URL", "")
    if details_url:
        try:
            details_html, details_final = _fetch_text_with_url(session, details_url, timeout=timeout)
            if "studentpersonaldetails" in details_final.lower():
                display_name, email = _extract_personal_details_bs(details_html, enrollment_id)
                logger.debug(f"College auth: extracted from details for {enrollment_id}: name={bool(display_name)}, email={bool(email)}")
        except (urllib.error.URLError, TimeoutError) as e:
            logger.warning(f"College login: failed to fetch details for {enrollment_id}: {e}")

    if not display_name or not email:
        profile_html = _collect_profile_html(session, response_html, timeout=timeout)
        fallback_name, fallback_email = _extract_profile_bs(profile_html, enrollment_id)
        logger.debug(f"College auth: fallback extraction for {enrollment_id}: name={bool(fallback_name)}, email={bool(fallback_email)}")
        if not display_name:
            display_name = fallback_name
        if not email:
            email = fallback_email

    logger.info(f"College auth: success for {enrollment_id}: name={bool(display_name)}, email={bool(email)}")
    return CollegeAuthResult(
        success=True,
        enrollment_id=enrollment_id,
        display_name=display_name,
        email=email,
    )


def _collect_profile_html(opener, initial_html: str, *, timeout: int) -> str:
    """Prefer login redirect HTML; only fetch safe ParentDesk URLs."""
    chunks = [initial_html]
    seen_urls = set()

    for url in getattr(settings, "COLLEGE_PROFILE_URLS", []):
        if not url or url in seen_urls:
            continue
        url_lower = url.lower()
        if any(part in url_lower for part in _SKIP_URL_PARTS):
            continue
        seen_urls.add(url)
        try:
            page_html, final_url = _fetch_text_with_url(opener, url, timeout=timeout)
            final_lower = final_url.lower()
            if "studentlogin.aspx" in final_lower:
                continue
            if any(part in final_lower for part in _SKIP_URL_PARTS):
                continue
            chunks.append(page_html)
        except (urllib.error.URLError, TimeoutError):
            continue

    return "\n".join(chunks)


def _build_opener():
    ctx = ssl.create_default_context()
    cookie_jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar),
        urllib.request.HTTPSHandler(context=ctx),
    )


def _fetch_text(opener, url: str, *, timeout: int) -> str:
    text, _ = _fetch_text_with_url(opener, url, timeout=timeout)
    return text


def _fetch_text_with_url(opener, url: str, *, timeout: int) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with opener.open(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
        return body, response.geturl()


def _post_form(opener, url: str, data: dict, *, timeout: int) -> tuple[str, str]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
        },
        method="POST",
    )
    with opener.open(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
        return body, response.geturl()


def _soup(page_html: str) -> BeautifulSoup:
    return BeautifulSoup(page_html or "", "html.parser")


def _extract_hidden_fields(page_html: str) -> dict[str, str]:
    soup = _soup(page_html)
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        tag = soup.find("input", attrs={"name": name})
        if tag and tag.get("value") is not None:
            fields[name] = tag["value"]
    return fields


def _login_failed(response_html: str, final_url: str, login_url: str) -> bool:
    soup = _soup(response_html)
    error_el = soup.find(id=_ERROR_LABEL_ID)
    if error_el and _clean_text(error_el.get_text()):
        return True

    login_path = urllib.parse.urlparse(login_url).path or login_url
    if login_path.lower() in final_url.lower() and "parentdesk" not in final_url.lower():
        if soup.find(id="ctl00_cph1_txtStuUser") or soup.find("input", attrs={"name": _LOGIN_FIELD_USER}):
            return True
    return False


def _clean_text(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def _clean_profile_value(raw: str) -> str:
    value = _clean_text(raw)
    if value.lower() in {"", "na", "n/a", "-", "none"}:
        return ""
    return value


def _looks_like_person_name(value: str, enrollment_id: str) -> bool:
    if not value or value == enrollment_id:
        return False
    if value.isdigit() or "@" in value:
        return False
    if len(value) < 3 or len(value) > 80:
        return False
    if not re.match(r"^[A-Za-z][A-Za-z.'\s-]+$", value):
        return False

    words = [w.strip(".'-") for w in value.split() if w.strip(".'-")]
    if not words:
        return False
    if not any(len(w) >= 3 for w in words):
        return False
    for word in words:
        if word.lower() in _UI_WORDS:
            return False
        if not re.match(r"^[A-Za-z][A-Za-z.'-]*$", word):
            return False

    return True


def _element_id_allowed(element_id: str) -> bool:
    lowered = (element_id or "").lower()
    return not any(part in lowered for part in _BAD_ELEMENT_ID_PARTS)


def _element_text_by_id(soup: BeautifulSoup, element_id: str) -> str:
    if not _element_id_allowed(element_id):
        return ""
    tag = soup.find(id=element_id)
    if not tag:
        return ""
    return _clean_profile_value(tag.get_text(" ", strip=True))


def _name_from_info_text(text: str, enrollment_id: str) -> str:
    """Parse 'AMLESH KUMAR - 11111525300' or 'AMLESH KUMAR (11111525300)'."""
    text = _clean_text(text)
    if not text or enrollment_id not in text:
        return ""

    before = re.split(re.escape(enrollment_id), text, maxsplit=1)[0]
    before = re.sub(r"[\s\-–—:|,(\[]+$", "", before).strip()
    if _looks_like_person_name(before, enrollment_id):
        return before
    return ""


def _normalize_label(text: str) -> str:
    return _clean_text(text).lower().rstrip(":*").strip()


def _input_field_value(tag) -> str:
    if not tag:
        return ""
    if tag.name == "input":
        return _clean_profile_value(tag.get("value") or "")
    if tag.name == "textarea":
        return _clean_profile_value(tag.get_text(" ", strip=True) or tag.get("value") or "")
    if tag.name == "select":
        selected = tag.find("option", selected=True) or tag.find("option")
        if selected:
            return _clean_profile_value(selected.get_text(" ", strip=True))
    return _clean_profile_value(tag.get_text(" ", strip=True))


def _value_from_labelled_row(soup: BeautifulSoup, label_hints: tuple[str, ...]) -> str:
    """Read input value from a form row like 'Student's Name :' -> AMLESH KUMAR."""
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = _normalize_label(cells[0].get_text(" ", strip=True))
        if not any(hint == label or hint in label for hint in label_hints):
            continue

        for cell in cells[1:]:
            for tag_name in ("input", "textarea", "select", "span", "label"):
                field = cell.find(tag_name)
                if not field:
                    continue
                value = _input_field_value(field)
                if value:
                    return value

    return ""


def _value_from_input_ids(soup: BeautifulSoup, element_ids: tuple[str, ...]) -> str:
    for element_id in element_ids:
        tag = soup.find("input", id=element_id)
        value = _input_field_value(tag)
        if value:
            return value
    return ""


def _value_from_input_name_hints(soup: BeautifulSoup, hints: tuple[str, ...]) -> str:
    for inp in soup.find_all("input"):
        key = ((inp.get("id") or "") + (inp.get("name") or "")).lower()
        key = key.replace("$", "").replace("_", "")
        if not any(hint in key for hint in hints):
            continue
        value = _input_field_value(inp)
        if value:
            return value
    return ""


def _extract_personal_details_bs(page_html: str, enrollment_id: str) -> tuple[str, str]:
    """
    Scrape from StudentPersonalDetails.aspx — input values, not field labels.
    See: https://accsoft.lnctu.ac.in/AccSoft2/Parents/StudentPersonalDetails.aspx
    """
    soup = _soup(page_html)
    display_name = ""
    email = ""

    name_value = _value_from_input_ids(soup, _STUDENT_NAME_INPUT_IDS)
    if _looks_like_person_name(name_value, enrollment_id):
        display_name = name_value

    if not display_name:
        name_value = _value_from_input_name_hints(soup, ("txtstuname", "txtstudentname", "stuname"))
        if _looks_like_person_name(name_value, enrollment_id):
            display_name = name_value

    if not display_name:
        name_value = _value_from_labelled_row(soup, _STUDENT_NAME_ROW_LABELS)
        if _looks_like_person_name(name_value, enrollment_id):
            display_name = name_value

    if not display_name:
        for element_id in _NAME_ELEMENT_IDS:
            raw = _element_text_by_id(soup, element_id)
            value = _name_from_info_text(raw, enrollment_id) or raw
            if _looks_like_person_name(value, enrollment_id):
                display_name = value
                break

    email_value = _value_from_input_ids(soup, _STUDENT_EMAIL_INPUT_IDS)
    if "@" in email_value:
        email = email_value

    if not email:
        email_value = _value_from_input_name_hints(soup, ("txtemail", "txtstuemail", "emailid"))
        if "@" in email_value:
            email = email_value

    if not email:
        email_value = _value_from_labelled_row(soup, _STUDENT_EMAIL_ROW_LABELS)
        if "@" in email_value:
            email = email_value

    return display_name, email


def _name_near_enrollment_id(page_text: str, enrollment_id: str) -> str:
    patterns = (
        rf"([A-Za-z][A-Za-z.\s]{{2,60}}?)\s*[\(\[\-–—:|]\s*{re.escape(enrollment_id)}",
        rf"{re.escape(enrollment_id)}\s*[\)\]\-–—:|]\s*([A-Za-z][A-Za-z.\s]{{2,60}})",
        rf"Name\s*[:\-]\s*([A-Za-z][A-Za-z.\s]{{2,60}}?)(?:\s*[\(\[\-]|$)",
        rf"(?:Student|Ward|Candidate)\s+(?:Name|Full\s+Name)\s*[:\-]\s*([A-Za-z][A-Za-z.\s]{{2,60}}?)(?:\s|$|[,.])",
        rf"(?:प्रवेश|Admission)\s*(?:संख्या|Number|क्रमांक|ID)?\s*[:\-]?\s*{re.escape(enrollment_id)}\s*(?:का|of|के)?\s*([A-Za-z][A-Za-z.\s]{{2,60}}?)(?:\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, page_text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _clean_profile_value(match.group(1))
        if _looks_like_person_name(value, enrollment_id):
            return value
    return ""


def _extract_profile_bs(page_html: str, enrollment_id: str) -> tuple[str, str]:
    soup = _soup(page_html)
    display_name = ""
    email = ""

    page_text = soup.get_text(" ", strip=True)
    display_name = _name_near_enrollment_id(page_text, enrollment_id)

    for element_id in _NAME_ELEMENT_IDS:
        if display_name:
            break
        raw = _element_text_by_id(soup, element_id)
        value = _name_from_info_text(raw, enrollment_id) or raw
        if _looks_like_person_name(value, enrollment_id):
            display_name = value

    if not display_name:
        for hidden in soup.find_all("input", attrs={"type": "hidden"}):
            key = (hidden.get("id") or hidden.get("name") or "").lower().replace("$", "").replace("_", "")
            if not any(token in key for token in _HIDDEN_NAME_KEYS):
                continue
            value = _clean_profile_value(hidden.get("value") or "")
            if _looks_like_person_name(value, enrollment_id):
                display_name = value
                break

    if not display_name:
        value = _value_from_labelled_row(soup, _STUDENT_NAME_ROW_LABELS)
        if _looks_like_person_name(value, enrollment_id):
            display_name = value

    if not display_name:
        welcome_match = re.search(
            r"Welcome(?:\s+back)?[,:\s]+(?:Mr\.?|Ms\.?|Mrs\.?|Miss\.?|Shri\.?|Smt\.?)?\s*([A-Za-z][A-Za-z.\s]{2,60})",
            page_text,
            flags=re.IGNORECASE,
        )
        if welcome_match:
            value = _clean_profile_value(welcome_match.group(1))
            if _looks_like_person_name(value, enrollment_id):
                display_name = value

    if not display_name:
        for span in soup.find_all("span"):
            text = _clean_profile_value(span.get_text())
            if _looks_like_person_name(text, enrollment_id) and len(text) > 4:
                display_name = text
                break

    for element_id in _EMAIL_ELEMENT_IDS:
        value = _element_text_by_id(soup, element_id)
        if "@" in value:
            email = value
            break

    if not email:
        for tag in soup.find_all(["span", "label", "td", "div"]):
            tag_id = (tag.get("id") or "").lower()
            if not _element_id_allowed(tag_id) or "email" not in tag_id and "mail" not in tag_id:
                continue
            value = _clean_profile_value(tag.get_text(" ", strip=True))
            if "@" in value:
                email = value
                break

    if not email:
        mailto = soup.find("a", href=re.compile(r"^mailto:", re.I))
        if mailto and mailto.get("href"):
            email = _clean_profile_value(mailto["href"].replace("mailto:", ""))

    return display_name, email
