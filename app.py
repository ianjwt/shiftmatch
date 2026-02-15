#!/usr/bin/env python3
"""
ShiftMatch / QuickMatch — Speed date your way to work.
Flask backend with PSFC crawler, match scoring, and daily email signup.
"""

import os
import re
import json
import uuid
import time
import random
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=None)
app.secret_key = os.urandom(24).hex()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KNOWN_COMMITTEES = [
    "Receiving", "Stocking", "Checkout", "Produce", "Maintenance",
    "Food Processing", "Office", "Childcare", "Orientation", "Inventory",
    "Shopping", "Cashier", "FTOP",
]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

raw_html_store = {}  # token -> {"html": str, "expires": float}


# ---------------------------------------------------------------------------
# PSFCCrawler
# ---------------------------------------------------------------------------
class ShiftMatchCrawler:
    BASE = "https://members.foodcoop.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

    def login(self, username: str, password: str) -> dict:
        """Login to PSFC member portal. Returns {success, message, debug}."""
        login_page_url = f"{self.BASE}/services/login/"
        debug_info = []
        try:
            # Step 1: GET the login page to obtain CSRF token + cookies
            response = self.session.get(login_page_url, timeout=15)
            debug_info.append(f"GET {login_page_url} -> {response.status_code}")
            soup = BeautifulSoup(response.text, "html.parser")

            # Step 2: Find the login form (id="loginform")
            login_form = soup.find("form", id="loginform") or soup.find("form")
            if not login_form:
                debug_info.append("No <form> found on page")
                return {"success": False, "message": "Could not find login form", "debug": debug_info}

            action = login_form.get("action", "/services/login/")
            if not action.startswith("http"):
                action = self.BASE + action
            debug_info.append(f"Form action: {action}")

            # Step 3: Extract CSRF token from hidden field
            csrf_input = login_form.find("input", {"name": "csrfmiddlewaretoken"})
            csrf_value = csrf_input.get("value", "") if csrf_input else ""
            debug_info.append(f"CSRF token: {'found' if csrf_value else 'MISSING'}")

            # Step 4: Extract 'next' field if present
            next_input = login_form.find("input", {"name": "next"})
            next_value = next_input.get("value", "") if next_input else ""

            # Step 5: Build exact payload matching PSFC's Django form
            # Fields: csrfmiddlewaretoken, username, password, next, submit
            login_data = {
                "csrfmiddlewaretoken": csrf_value,
                "username": username,
                "password": password,
                "next": next_value or "/services/",
                "submit": "Log In",
            }
            debug_info.append(f"Fields: {list(login_data.keys())}")

            # Step 6: POST with Referer header (Django CSRF requires it)
            response = self.session.post(
                action,
                data=login_data,
                headers={"Referer": login_page_url},
                allow_redirects=True,
                timeout=15,
            )
            debug_info.append(f"POST -> {response.status_code}, URL: {response.url}")
            debug_info.append(f"Cookies: {list(self.session.cookies.keys())}")

            page = response.text.lower()
            if "logout" in page or "sign out" in page or "log out" in page:
                return {"success": True, "message": "Login successful", "debug": debug_info}
            if "invalid" in page or "incorrect" in page or "error" in page:
                # Try to extract error message from page
                err_el = BeautifulSoup(response.text, "html.parser").find(class_="errorlist")
                err_msg = err_el.get_text(strip=True) if err_el else "incorrect credentials"
                debug_info.append(f"Error on page: {err_msg}")
                return {"success": False, "message": f"Login failed — {err_msg}", "debug": debug_info}
            if "/login/" in response.url:
                debug_info.append("Still on login page after POST")
                return {"success": False, "message": "Login failed — redirected back to login", "debug": debug_info}
            # Ended up somewhere else — likely success
            return {"success": True, "message": "Login successful", "debug": debug_info}

        except requests.RequestException as exc:
            debug_info.append(f"Exception: {exc}")
            return {"success": False, "message": f"Connection error: {exc}", "debug": debug_info}

    def verify_logged_in(self) -> dict:
        """Check we can access /services/home (confirms session is valid)."""
        try:
            resp = self.session.get(f"{self.BASE}/services/home", timeout=15)
            if "/login/" in resp.url:
                return {"success": False, "message": "Session expired — redirected to login"}
            return {"success": True, "message": f"Home OK (status {resp.status_code})"}
        except requests.RequestException as exc:
            return {"success": False, "message": f"Home check failed: {exc}"}

    def get_shifts(self) -> dict:
        """Fetch and parse shifts page. Returns {success, html, parsed_shifts, message, debug}."""
        debug_info = []
        try:
            # Verify session first
            home_check = self.verify_logged_in()
            debug_info.append(f"Home check: {home_check['message']}")
            if not home_check["success"]:
                return {
                    "success": False, "html": "", "parsed_shifts": [],
                    "message": home_check["message"], "debug": debug_info,
                }

            # Build shifts URL with today's date
            today = datetime.now().strftime("%Y-%m-%d")
            url = f"{self.BASE}/services/shifts/0/0/0/{today}/"
            debug_info.append(f"Fetching: {url}")

            resp = self.session.get(url, timeout=15)
            debug_info.append(f"Status: {resp.status_code}, URL: {resp.url}")

            if "/login/" in resp.url:
                return {
                    "success": False, "html": "", "parsed_shifts": [],
                    "message": "Redirected to login — session not valid",
                    "debug": debug_info,
                }

            if resp.status_code != 200:
                return {
                    "success": False, "html": "", "parsed_shifts": [],
                    "message": f"HTTP {resp.status_code}", "debug": debug_info,
                }

            html = resp.text
            debug_info.append(f"HTML size: {len(html)} chars")

            # Save debug copy of the shifts page
            debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shifts_page_debug.html")
            with open(debug_path, "w") as f:
                f.write(html)
            debug_info.append(f"Saved debug HTML to {debug_path}")

            soup = BeautifulSoup(html, "html.parser")

            # Log page structure for debugging
            tables = soup.find_all("table")
            divs_with_class = soup.find_all("div", class_=True)
            debug_info.append(f"Tables: {len(tables)}, Divs with class: {len(divs_with_class)}")

            # Log select/filter dropdowns
            selects = soup.find_all("select")
            for sel in selects:
                opts = [o.get_text(strip=True) for o in sel.find_all("option")][:10]
                debug_info.append(f"Select name={sel.get('name')} id={sel.get('id')}: {opts}")

            # Parse shifts
            parsed = self._parse_shifts(tables, soup)
            debug_info.append(f"Parsed {len(parsed)} shifts from tables")

            # If no shifts from tables, try column-based layout
            if not parsed:
                parsed = self._parse_column_layout(soup)
                debug_info.append(f"Parsed {len(parsed)} shifts from column layout")

            return {
                "success": True, "html": html,
                "parsed_shifts": parsed, "message": "OK",
                "debug": debug_info,
            }

        except requests.RequestException as exc:
            debug_info.append(f"Exception: {exc}")
            return {
                "success": False, "html": "", "parsed_shifts": [],
                "message": f"Connection error: {exc}", "debug": debug_info,
            }

    # -- Multi-phase heuristic parser ---------------------------------------
    def _parse_shifts(self, tables, soup) -> list:
        shifts = []
        counter = 0
        for table_idx, table in enumerate(tables):
            rows = table.find_all("tr")
            if not rows:
                continue
            header_map = self._detect_headers(rows[0])
            current_day = None

            for row_idx, row in enumerate(rows[1:], start=1):
                cells = row.find_all(["td", "th"])
                texts = [c.get_text(strip=True) for c in cells]

                # Day-section detection: single-cell row with a day name
                if len(cells) == 1 and self._is_day_name(texts[0]):
                    current_day = self._normalize_day(texts[0])
                    continue

                if len(cells) < 2:
                    continue

                shift = self._extract_shift(cells, texts, header_map, current_day, table_idx, row_idx)
                if shift:
                    counter += 1
                    shift["id"] = f"shift_{counter:03d}"
                    shifts.append(shift)
        return shifts

    def _detect_headers(self, header_row) -> dict:
        mapping = {}
        cells = header_row.find_all(["td", "th"])
        for idx, cell in enumerate(cells):
            txt = cell.get_text(strip=True).lower()
            if any(k in txt for k in ("day", "date")):
                mapping["day"] = idx
            elif any(k in txt for k in ("time", "hour", "when")):
                mapping["time"] = idx
            elif any(k in txt for k in ("committee", "squad", "dept", "area", "job")):
                mapping["committee"] = idx
            elif any(k in txt for k in ("slot", "open", "avail", "remain")):
                mapping["slots"] = idx
            elif any(k in txt for k in ("desc", "detail", "note", "info")):
                mapping["description"] = idx
        return mapping

    def _extract_shift(self, cells, texts, hmap, current_day, tbl, row):
        day = current_day
        time_raw = ""
        committee = ""
        description = ""
        signup_url = ""
        slots = ""

        if hmap:
            day = day or self._safe_idx(texts, hmap.get("day"))
            time_raw = self._safe_idx(texts, hmap.get("time")) or ""
            committee = self._safe_idx(texts, hmap.get("committee")) or ""
            slots = self._safe_idx(texts, hmap.get("slots")) or ""
            description = self._safe_idx(texts, hmap.get("description")) or ""

        for idx, txt in enumerate(texts):
            if not day and self._is_day_name(txt):
                day = self._normalize_day(txt)
            if not time_raw and re.search(r"\d{1,2}:\d{2}\s*[APap][Mm]", txt):
                time_raw = txt
            if not committee:
                cm = self._fuzzy_committee(txt)
                if cm:
                    committee = cm
            if not slots and re.match(r"^\d+$", txt.strip()):
                slots = txt.strip()

        for cell in cells:
            link = cell.find("a", href=True)
            if link:
                href = link["href"]
                if "signup" in href.lower() or "shift" in href.lower() or href.startswith("http"):
                    signup_url = href if href.startswith("http") else f"{self.BASE}{href}"
                    break

        if not description:
            description = " | ".join(t for t in texts if t and t != day and t != time_raw)

        if not (time_raw or committee):
            return None

        return {
            "day": day or "Unknown",
            "time_raw": time_raw,
            "time_slot": self._classify_time(time_raw),
            "committee": committee or "General",
            "description": description[:200],
            "signup_url": signup_url,
            "slots": slots or str(random.randint(1, 6)),
            "source_table": tbl,
            "source_row": row,
        }

    @staticmethod
    def _safe_idx(lst, idx):
        if idx is not None and 0 <= idx < len(lst):
            return lst[idx]
        return None

    @staticmethod
    def _is_day_name(text: str) -> bool:
        return bool(re.match(r"^(mon|tue|wed|thu|fri|sat|sun)", text.strip().lower()))

    @staticmethod
    def _normalize_day(text: str) -> str:
        t = text.strip().lower()[:3]
        for d in DAY_NAMES:
            if d.lower().startswith(t):
                return d
        return text.strip().title()

    @staticmethod
    def _classify_time(time_raw: str) -> str:
        m = re.search(r"(\d{1,2}):(\d{2})\s*([APap][Mm])", time_raw)
        if not m:
            return "Morning"
        hour = int(m.group(1))
        ampm = m.group(3).upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        if hour < 12:
            return "Morning"
        if hour < 17:
            return "Afternoon"
        if hour < 21:
            return "Evening"
        return "Overnight"

    @staticmethod
    def _fuzzy_committee(text: str):
        t = text.strip().lower()
        for c in KNOWN_COMMITTEES:
            if c.lower() in t or t in c.lower():
                return c
        return None

    def _parse_column_layout(self, soup) -> list:
        """
        Parse PSFC's actual shift calendar layout:
          div.grid-container
            div.col                     (one per day)
              p > b "Sat 2/14/2026"     (day header)
              a.shift href="/services/shift_claim/ID/"
                b "6:30pm"              (time)
                "Bathroom 🚽"           (committee name)
        """
        shifts = []
        counter = 0

        grid = soup.find("div", class_="grid-container")
        if not grid:
            return shifts

        columns = grid.find_all("div", class_="col", recursive=False)

        for col in columns:
            # Extract day + date from header: <p><b>Sat&nbsp;2/14/2026</b>...
            header_b = col.find("p")
            if not header_b:
                continue
            header_text = header_b.get_text(strip=True)
            # Parse "Sat 2/14/2026 B week" -> day="Saturday", date="2/14/2026"
            day_match = re.match(
                r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*(\d{1,2}/\d{1,2}/\d{4})",
                header_text,
            )
            if day_match:
                day_abbr = day_match.group(1)
                date_str = day_match.group(2)
                day = self._normalize_day(day_abbr)
            else:
                day = "Unknown"
                date_str = ""

            # Each shift is an <a class="shift"> inside the column
            shift_links = col.find_all("a", class_="shift")
            for link in shift_links:
                # Skip shifts that are unavailable or already have a worker
                classes = link.get("class", [])
                is_unavail = "unavail" in classes
                has_worker = "worker" in classes
                is_carrot = "carrot" in classes

                # Extract time from <b> tag
                time_el = link.find("b")
                time_raw = time_el.get_text(strip=True) if time_el else ""

                # Extract committee: the text content minus the time
                full_text = link.get_text(strip=True)
                # Remove time, carrot emoji, and extra whitespace
                committee_text = full_text
                if time_raw:
                    committee_text = committee_text.replace(time_raw, "")
                committee_text = committee_text.replace("🥕", "").strip()
                # Strip all emoji (Unicode ranges for emoji)
                committee_clean = re.sub(
                    r"[\U0001F300-\U0001FAFF\U00002702-\U000027B0\U0000FE00-\U0000FE0F\u200d\u2600-\u26FF\u2700-\u27BF]+",
                    "", committee_text,
                ).strip()
                # Also strip leading ** (training required marker)
                committee_clean = re.sub(r"^\*+\s*", "", committee_clean).strip()

                # Signup URL
                href = link.get("href", "").strip()
                signup_url = ""
                if href:
                    signup_url = href if href.startswith("http") else f"{self.BASE}{href}"

                # Determine availability
                if is_unavail:
                    status = "unavailable"
                    slots = "0"
                elif has_worker:
                    status = "filled"
                    slots = "0"
                else:
                    status = "available"
                    slots = "1"

                # Build description (committee, date, and time shown separately)
                desc_parts = []
                if is_carrot:
                    desc_parts.append("Carrot shift (extra credit)")
                if is_unavail:
                    desc_parts.append("Currently unavailable")
                if has_worker:
                    desc_parts.append("Worker assigned")

                counter += 1
                shifts.append({
                    "id": f"shift_{counter:03d}",
                    "day": day,
                    "date": date_str,
                    "time_raw": time_raw,
                    "time_slot": self._classify_time(time_raw),
                    "committee": committee_clean or "General",
                    "description": " — ".join(desc_parts),
                    "signup_url": signup_url,
                    "slots": slots,
                    "status": status,
                    "is_carrot": is_carrot,
                    "source_table": "grid-container",
                    "source_row": counter,
                })

        return shifts


# ---------------------------------------------------------------------------
# Match Scorer
# ---------------------------------------------------------------------------
class ShiftMatcher:
    """Scores shifts against user preferences using the QuickMatch algorithm."""

    def __init__(self, preferences: dict):
        self.days = [d.lower() for d in preferences.get("days", [])]
        self.times = [t.lower() for t in preferences.get("times", [])]
        self.committees = preferences.get("committees", [])  # ordered by rank (index 0 = top)
        self.excluded = [c.lower() for c in preferences.get("excludedCommittees", [])]

    def score(self, shift: dict) -> dict:
        base = 100
        breakdown = {}

        # --- Committee rank scoring ---
        committee = shift.get("committee", "")
        comm_lower = [c.lower() for c in self.committees]
        if comm_lower and committee.lower() in comm_lower:
            rank = comm_lower.index(committee.lower())
            if rank == 0:
                base += 10
                breakdown["committee"] = f"Top choice: {committee} (+10%)"
            else:
                penalty = rank * 5
                base -= penalty
                breakdown["committee"] = f"Rank #{rank+1}: {committee} (-{penalty}%)"
        elif comm_lower:
            base -= 25
            breakdown["committee"] = f"{committee} not in your preferences (-25%)"
        else:
            breakdown["committee"] = "No committee preference set"

        # --- Day matching ---
        day = shift.get("day", "").lower()
        if self.days:
            if day in self.days:
                breakdown["day"] = f"{shift.get('day', '')} is a preferred day"
            else:
                base -= 20
                breakdown["day"] = f"{shift.get('day', '')} is not preferred (-20%)"
        else:
            breakdown["day"] = "No day preference set"

        # --- Time matching ---
        time_slot = shift.get("time_slot", "").lower()
        if self.times:
            if time_slot in self.times:
                breakdown["time"] = f"{shift.get('time_slot', '')} is a preferred time"
            else:
                base -= 15
                breakdown["time"] = f"{shift.get('time_slot', '')} is not preferred (-15%)"
        else:
            breakdown["time"] = "No time preference set"

        # --- Slots bonus/penalty ---
        try:
            slot_n = int(shift.get("slots", 0))
            if slot_n > 3:
                base += 5
                breakdown["slots"] = f"{slot_n} slots available (+5%)"
            elif slot_n == 1:
                base -= 5
                breakdown["slots"] = "Only 1 slot left (-5%)"
            else:
                breakdown["slots"] = f"{slot_n} slots available"
        except (ValueError, TypeError):
            breakdown["slots"] = "Slots unknown"

        # --- Late evening penalty ---
        m = re.search(r"(\d{1,2}):(\d{2})\s*([APap][Mm])", shift.get("time_raw", ""))
        if m:
            hour = int(m.group(1))
            ampm = m.group(3).upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            if hour >= 21:
                base -= 10
                breakdown["late"] = "Late evening shift (-10%)"

        score = max(0, min(120, base))
        return {"shift": shift, "score": score, "breakdown": breakdown}

    def rank(self, shifts: list) -> list:
        filtered = [s for s in shifts if s.get("committee", "").lower() not in self.excluded]
        scored = [self.score(s) for s in filtered]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def top(self, shifts: list, n: int = 5) -> list:
        return self.rank(shifts)[:n]


# ---------------------------------------------------------------------------
# Mock Data Generator
# ---------------------------------------------------------------------------
def generate_mock_shifts(n: int = 30) -> list:
    committees = ["Receiving", "Stocking", "Checkout", "Produce", "Maintenance"]
    times_map = {
        "Morning": [("6:00AM", "8:45AM"), ("7:00AM", "9:45AM"), ("8:00AM", "10:45AM"), ("9:00AM", "11:00AM")],
        "Afternoon": [("12:00PM", "2:45PM"), ("1:00PM", "3:45PM"), ("2:00PM", "4:45PM")],
        "Evening": [("5:00PM", "7:45PM"), ("6:00PM", "8:45PM"), ("7:00PM", "9:45PM")],
        "Overnight": [("9:30PM", "12:15AM"), ("10:00PM", "12:45AM")],
    }
    descs = [
        "Unload deliveries and stock shelves in the walk-in cooler area.",
        "Assist members at checkout lanes and handle returns.",
        "Stock dry goods, dairy, and frozen sections.",
        "Sort and display produce, rotate older stock.",
        "General maintenance: cleaning, minor repairs, recycling.",
        "Process incoming shipments and verify invoices.",
        "Bag groceries and assist elderly/disabled members.",
        "Restock bulk bins and ensure proper labeling.",
        "Floor cleaning, bathroom maintenance, trash removal.",
        "Help with inventory counts and shelf organization.",
    ]
    shifts = []
    for i in range(n):
        day = random.choice(DAY_NAMES)
        slot = random.choice(list(times_map.keys()))
        start, end = random.choice(times_map[slot])
        committee = random.choice(committees)
        slot_count = random.choice([1, 2, 3, 4, 5, 6, 8])
        shifts.append({
            "id": f"shift_{i+1:03d}",
            "day": day,
            "time_raw": f"{start} - {end}",
            "time_slot": slot,
            "committee": committee,
            "description": random.choice(descs),
            "signup_url": "https://members.foodcoop.com/services/shifts/",
            "slots": str(slot_count),
            "source_table": 0,
            "source_row": i,
        })
    return shifts


# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(app_dir, "shiftmatch.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    member = data.get("member_number", "").strip()
    password = data.get("password", "").strip()
    preferences = data.get("preferences", {})

    if not member or not password:
        return jsonify({"success": False, "message": "Member number and password are required."})

    crawler = ShiftMatchCrawler()
    result = crawler.login(member, password)
    debug = result.get("debug", [])

    if result["success"]:
        shift_result = crawler.get_shifts()
        shift_debug = shift_result.get("debug", [])
        debug.extend(shift_debug)

        if shift_result["success"] and shift_result["parsed_shifts"]:
            parsed = shift_result["parsed_shifts"]
            # Store raw HTML with expiry
            token = str(uuid.uuid4())
            raw_html_store[token] = {"html": shift_result["html"], "expires": time.time() + 600}
            # Score shifts
            matcher = ShiftMatcher(preferences)
            scored = matcher.rank(parsed)
            return jsonify({
                "success": True,
                "message": result["message"],
                "scored_shifts": scored,
                "raw_token": token,
                "source": "live",
                "debug": debug,
            })
        else:
            # Fallback to mock data
            mock = generate_mock_shifts(30)
            matcher = ShiftMatcher(preferences)
            scored = matcher.rank(mock)
            return jsonify({
                "success": True,
                "message": result["message"] + " (Using sample data — could not parse live shifts.)",
                "scored_shifts": scored,
                "raw_token": None,
                "source": "mock",
                "debug": debug,
            })
    else:
        return jsonify({"success": False, "message": result["message"], "debug": debug})


@app.route("/api/shifts", methods=["POST"])
def api_shifts():
    """Re-score shifts with updated preferences (no re-crawl)."""
    data = request.get_json(force=True)
    shifts = data.get("shifts", [])
    preferences = data.get("preferences", {})
    matcher = ShiftMatcher(preferences)
    scored = matcher.rank(shifts)
    return jsonify({"scored_shifts": scored})


@app.route("/api/mock-shifts", methods=["POST"])
def api_mock():
    """Return scored mock shifts for testing without credentials."""
    data = request.get_json(force=True)
    preferences = data.get("preferences", {})
    mock = generate_mock_shifts(30)
    matcher = ShiftMatcher(preferences)
    scored = matcher.rank(mock)
    return jsonify({"scored_shifts": scored, "source": "mock"})


@app.route("/api/signup-daily-email", methods=["POST"])
def api_signup_email():
    """Add user to daily email list."""
    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    member = data.get("member_number", "").strip()
    password = data.get("password", "").strip()
    preferences = data.get("preferences", {})

    if not email:
        return jsonify({"success": False, "message": "Email address required."})

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_config.json")
    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)

    if "users" not in config:
        config["users"] = []

    # Upsert user
    found = False
    for u in config["users"]:
        if u.get("email") == email:
            u.update({"member_number": member, "password": password, "preferences": preferences})
            found = True
            break
    if not found:
        config["users"].append({
            "email": email,
            "member_number": member,
            "password": password,
            "preferences": preferences,
        })

    if "smtp" not in config:
        config["smtp"] = {"host": "smtp.gmail.com", "port": 587, "username": "", "password": ""}
    if "schedule_time" not in config:
        config["schedule_time"] = "20:01"

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return jsonify({"success": True, "message": f"Daily emails will be sent to {email} at 8:01 PM."})


@app.route("/api/raw/<token>")
def api_raw(token):
    entry = raw_html_store.get(token)
    if not entry or time.time() > entry["expires"]:
        return "Expired or not found", 404
    return entry["html"], 200, {"Content-Type": "text/html"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--crawl":
        if len(sys.argv) < 4:
            print("Usage: python app.py --crawl <member_number> <password>")
            sys.exit(1)
        crawler = ShiftMatchCrawler()
        res = crawler.login(sys.argv[2], sys.argv[3])
        print(res["message"])
        if res["success"]:
            shifts = crawler.get_shifts()
            print(f"Found {len(shifts['parsed_shifts'])} shifts")
            for s in shifts["parsed_shifts"][:5]:
                print(f"  {s['day']} {s['time_raw']} — {s['committee']}")
    else:
        port = int(os.environ.get("PORT", 5050))
        print(f"\n  ShiftMatch running on http://localhost:{port}\n")
        app.run(host="0.0.0.0", debug=True, port=port)
