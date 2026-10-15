"""Local web server for the camera check: open the page on your phone, upload the photo.

Access rules:
  * the URL carries a random token (shown on the PC as a link + QR code);
  * one device at a time: the first device to open the page claims the session
    with a private cookie, every other device is turned away until the PC
    presses Disconnect (which also changes the link) or the device goes idle;
  * it runs only while the camera check is open.
"""
from __future__ import annotations

import html
import json
import math
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import backup, engine
from ..logs import log
from . import analysis, chart, verify

PAGE = Path(__file__).with_name("page.html")
MAX_UPLOAD = 40 * 1024 * 1024
MAX_JSON = 64 * 1024
KEEP_PHOTOS = 4
KEEP_UPLOADS = 5  # copies kept on disk in <data folder>/camera-uploads
PHOTO_ID = re.compile(r"^[0-9a-f]{12}$")
DEVICE_COOKIE = "mc_device"
IDLE_RELEASE = 10 * 60  # a silent device loses the session after this many seconds
BUSY_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camera check busy</title><body style="font:17px/1.5 system-ui,sans-serif;max-width:32em;margin:15vh auto;padding:0 20px">
<h2>Another device is connected</h2><p>The camera check accepts one device at a time. On the PC, press
<b>Disconnect</b> in the Camera check tab, then scan the new QR code.</p></body>"""
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    ),
}


def lan_address() -> str:
    """The PC's address on the local network (no packet is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class CameraSession:
    """State shared by the web server (phone side) and the desktop window."""

    def __init__(self, display, notify):
        self.display = display
        self.notify = notify  # thread-safe callable(event_tuple) into the desktop window
        self.token = secrets.token_urlsafe(9)
        self.lock = threading.Lock()
        self.photos: OrderedDict[str, dict] = OrderedDict()
        self.generation = 0  # bumps whenever screen settings change; older photos become stale
        self.chart_visible = False
        self.chart_codes: dict[int, float] = {}  # code shown on the chart -> when it was shown
        self.code_screens: dict[int, str] = {}  # code -> key of the screen that showed it
        self.mode = "single"  # or "match": charts on several screens, one photo of them all
        self.match_displays: list = []
        self.apply_status: dict | None = None
        self.device: dict | None = None  # the one device allowed in: secret cookie, address, kind, last seen
        self.keep_uploads = False  # opt-in: keep the last few photos on disk (re-encoded, no metadata)
        self.server: ThreadingHTTPServer | None = None
        self.address = ""
        self.url = ""

    # --- lifecycle -----------------------------------------------------------

    def start(self, host: str | None = None, port: int = 8765) -> str:
        """Listen on the PC's local-network address only (not on every adapter, e.g. VPNs)."""
        self.address = host or lan_address()
        handler = _make_handler(self)
        for candidate in range(port, port + 20):
            try:
                self.server = ThreadingHTTPServer((self.address, candidate), handler)
                break
            except OSError:
                continue
        else:
            raise OSError(f"no free port between {port} and {port + 19}")
        self.server.daemon_threads = True
        self._update_url()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.url

    def _update_url(self) -> None:
        self.url = f"http://{self.address}:{self.server.server_address[1]}/{self.token}/"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # --- called by the desktop window --------------------------------------

    def screen_changed(self) -> None:
        with self.lock:
            self.generation += 1

    def retarget(self, display) -> None:
        """The desktop window moved to another screen: the camera check follows it.

        Photos and chart codes of the old screen are dropped: a photo of the old screen's chart
        must not pass as a measurement of the new one. The connected phone stays connected.
        """
        with self.lock:
            self.display = display
            self.photos.clear()
            self.chart_codes.clear()
            self.code_screens.clear()
            self.generation += 1
            self.apply_status = None

    def set_mode(self, mode: str, displays=None) -> None:
        """single: one screen, measured on its own. match: every screen in one photo, compared."""
        with self.lock:
            self.mode = mode
            self.match_displays = list(displays or []) if mode == "match" else []
            self.photos.clear()
            self.chart_codes.clear()
            self.code_screens.clear()
            self.generation += 1
            self.apply_status = None

    def set_chart_visible(self, visible: bool) -> None:
        with self.lock:
            self.chart_visible = visible

    def new_chart_code(self, screen_key: str | None = None) -> int:
        """A fresh random code for the next showing of the chart; photos must show one of these."""
        code = secrets.randbits(chart.CODE_BITS) or 1
        with self.lock:
            self.chart_codes[code] = time.time()
            self.code_screens[code] = screen_key or self.display.key
        return code

    def device_info(self) -> dict | None:
        """Who is connected right now (None if nobody, or the device went idle)."""
        with self.lock:
            self._expire_idle()
            if self.device is None:
                return None
            return {k: self.device[k] for k in ("kind", "address", "since")}

    def release_device(self, new_link: bool = True) -> str:
        """Kick the connected device. With new_link the old URL/QR stops working too."""
        with self.lock:
            self.device = None
            if new_link:
                self.token = secrets.token_urlsafe(9)
                if self.server is not None:
                    self._update_url()
        return self.url

    def _expire_idle(self) -> None:
        if self.device is not None and time.time() - self.device["seen"] > IDLE_RELEASE:
            self.device = None

    def admit(self, cookie: str | None, address: str, agent: str, claim: bool) -> tuple[bool, str | None]:
        """Single-device rule. Returns (allowed, cookie to set if this request just claimed the session)."""
        with self.lock:
            self._expire_idle()
            if self.device is not None:
                if cookie and secrets.compare_digest(cookie.encode(), self.device["secret"].encode()):
                    self.device["seen"] = time.time()
                    return True, None
                return False, None
            if not claim:
                return False, None
            secret = secrets.token_urlsafe(24)
            self.device = {
                "secret": secret,
                "address": address,
                "kind": _device_kind(agent),
                "since": time.time(),
                "seen": time.time(),
            }
            return True, secret

    def finished_apply(self, state: str, message: str) -> None:
        with self.lock:
            self.apply_status = {"state": state, "message": message}
            if state == "kept":
                self.generation += 1

    # --- called by the web handlers -----------------------------------------

    def status(self) -> dict:
        with self.lock:
            return {
                "display": self.display.name,
                "mode": self.mode,
                "screens": [{"key": d.key, "name": d.name, "index": d.index} for d in self.match_displays],
                "chart": self.chart_visible,
                "apply": self.apply_status,
                "generation": self.generation,
            }

    def _issued(self) -> dict[int, float]:
        with self.lock:
            return dict(self.chart_codes)

    def add_photo(self, data: bytes) -> dict:
        if self.mode == "match":
            return self._add_match_photo(data)
        with self.lock:  # pin the screen now, in case the window moves while we work
            display, generation, issued = self.display, self.generation, dict(self.chart_codes)
        image = analysis.load_photo(data)
        state = engine.probe(display)  # the settings the photo was taken with
        check = verify.verify(image, analysis.detect_corners(image), display.size, issued)
        if self.keep_uploads:
            _keep_upload(image, check.to_json())
        photo_id = secrets.token_hex(6)
        with self.lock:
            if display is not self.display:
                raise ValueError("The PC switched to another screen during the upload. Take a new photo.")
            self.photos[photo_id] = {
                "image": image,
                "state": state,
                "generation": generation,
                "corners": check.corners,
                "verified": False,  # set by analyse(), with the corners the user confirmed
                "result": None,
                "preview": analysis.jpeg(image),
            }
            while len(self.photos) > KEEP_PHOTOS:
                self.photos.popitem(last=False)
        self.notify(("photo", photo_id, check.to_json()))
        return {
            "id": photo_id,
            "corners": check.corners,
            "width": image.width,
            "height": image.height,
            "verification": check.to_json(),
        }

    def record(self, photo_id: str) -> dict:
        with self.lock:
            record = self.photos.get(photo_id) if PHOTO_ID.match(photo_id or "") else None
        if record is None:
            raise ValueError("That photo has expired. Upload it again.")
        return record

    def analyse(self, photo_id: str, corners, wb: dict) -> dict:
        record = self.record(photo_id)
        if record.get("mode") == "match":
            raise ValueError("This photo shows several screens. Measure it as a match.")
        check = verify.verify(record["image"], corners, self.display.size, self._issued())
        if not check.ok:
            with self.lock:
                record["verified"] = False
                record["result"] = None
            result = {"ok": False, "verification": check.to_json(), "warnings": [check.reason]}
            self.notify(("analysis", photo_id, result))
            return result
        corners = check.corners  # may have been turned if the photo was rotated
        result = analysis.analyse(
            record["image"],
            corners,
            self.display.size,
            wb_mode=wb["mode"],
            wb_kelvin=wb["kelvin"],
            reference=wb.get("reference"),
        )
        result["verification"] = check.to_json()
        result["corners"] = corners
        if record["state"].night_light:
            result.setdefault("warnings", []).append(
                "Night Light is on and changes the screen color by itself. Turn it off while calibrating."
            )
        if self.display.hdr_enabled:
            result.setdefault("warnings", []).append(
                "HDR / auto color management is on for this screen; GPU corrections may not take effect."
            )
        with self.lock:
            record["result"] = result
            record["corners"] = corners
            record["verified"] = True
        self.notify(("analysis", photo_id, result))
        return result

    def request_apply(self, photo_id: str, options: dict) -> dict:
        record = self.record(photo_id)
        if record.get("mode") == "match":
            return self._apply_match(record, options)
        result = record["result"]
        if not record.get("verified"):
            raise ValueError("This photo hasn't passed the chart check, so nothing will be applied from it.")
        if not result or not result.get("ok"):
            raise ValueError("analyse the photo first")
        if record["generation"] != self.generation:
            raise ValueError("The screen settings changed after this photo was taken. Take a new photo first.")
        calibration = analysis.correction(result, record["state"], **options)
        with self.lock:
            self.apply_status = {"state": "waiting", "message": "Look at your PC: keep or revert the change."}
        self.notify(("apply", calibration))
        return {"queued": True, "title": calibration.title}


    # --- match mode: every screen in one photo ----------------------------------------------

    def _identify(self, image, corners, displays, issued, owners):
        """Which screen's chart is this? The code says; the layout of that screen confirms it."""
        first = None
        for size in dict.fromkeys(d.size for d in displays):
            check = verify.verify(image, corners, size, issued)
            first = first or check
            if check.code is None:
                continue
            owner = next((d for d in displays if d.key == owners.get(check.code)), None)
            if owner is not None and owner.size != size:
                check = verify.verify(image, corners, owner.size, issued)
            return owner, check
        return None, first

    def _add_match_photo(self, data: bytes) -> dict:
        with self.lock:
            displays, generation = list(self.match_displays), self.generation
            issued, owners = dict(self.chart_codes), dict(self.code_screens)
        image = analysis.load_photo(data)
        states = {d.key: engine.probe(d) for d in displays}
        charts = []
        for corners in analysis.detect_charts(image, len(displays)):
            owner, check = self._identify(image, corners, displays, issued, owners)
            charts.append({"key": owner.key if owner else None, "name": owner.name if owner else "Unknown screen",
                           "index": owner.index if owner else 0, "corners": check.corners,
                           "verification": check.to_json()})
        if self.keep_uploads:
            _keep_upload(image, {"charts": charts})
        photo_id = secrets.token_hex(6)
        with self.lock:
            self.photos[photo_id] = {"mode": "match", "image": image, "states": states, "generation": generation,
                                     "charts": charts, "verified": False, "result": None,
                                     "preview": analysis.jpeg(image)}
            while len(self.photos) > KEEP_PHOTOS:
                self.photos.popitem(last=False)
        found = [c for c in charts if c["verification"]["ok"]]
        summary = {"ok": len(found) == len(displays) and len(displays) > 1,
                   "code": ", ".join(c["verification"]["code"] for c in found if c["verification"]["code"])}
        self.notify(("photo", photo_id, summary))
        return {"id": photo_id, "mode": "match", "width": image.width, "height": image.height, "charts": charts}

    def analyse_match(self, photo_id: str, charts: list[dict], reference: str) -> dict:
        record = self.record(photo_id)
        if record.get("mode") != "match":
            raise ValueError("This photo was taken for one screen. Switch to matching and take a new one.")
        with self.lock:
            displays, issued, owners = list(self.match_displays), dict(self.chart_codes), dict(self.code_screens)
        by_key = {d.key: d for d in displays}
        screens, results, problems = [], {}, []
        for item in charts:
            display = by_key.get(item["key"])
            if display is None:
                problems.append("A chart in the photo doesn't belong to any screen being matched.")
                continue
            check = verify.verify(record["image"], item["corners"], display.size, issued)
            if check.ok and owners.get(check.code) != display.key:
                problems.append(f"The chart marked as {display.name} actually belongs to another screen.")
                continue
            entry = {"key": display.key, "name": display.name, "index": display.index,
                     "corners": check.corners, "verification": check.to_json()}
            if not check.ok:
                problems.append(f"{display.name}: {check.reason}")
            else:
                result = analysis.analyse(record["image"], check.corners, display.size)
                results[display.key] = result
                entry.update(gammas=result.get("gammas"), reliable=result.get("reliable"),
                             warnings=result.get("warnings", []), ok=result.get("ok", False))
            screens.append(entry)
        missing = [d.name for d in displays if d.key not in {s["key"] for s in screens}]
        if missing:
            problems.append(f"Not in the photo: {', '.join(missing)}. Get every screen into one shot.")
        if reference not in results:
            reference = next(iter(results), "")
        ok = not problems and len(results) == len(displays) and all(r.get("ok") for r in results.values())
        comparisons = {key: analysis.compare(results[reference], result)
                       for key, result in results.items() if key != reference} if ok else {}
        warnings = list(problems)
        for entry in screens:
            warnings += [f"{entry['name']}: {text}" for text in entry.get("warnings", [])]
            if entry["key"] in comparisons:
                entry["comparison"] = comparisons[entry["key"]]
                warnings += [f"{entry['name']}: {text}" for text in comparisons[entry["key"]]["notes"]]
        response = {"ok": ok, "mode": "match", "reference": reference, "screens": screens,
                    "warnings": list(dict.fromkeys(warnings))}
        with self.lock:
            record.update(results=results, comparisons=comparisons, reference=reference, verified=ok,
                          result=response)
        self.notify(("analysis", photo_id, response))
        return response

    def _apply_match(self, record: dict, options: dict) -> dict:
        if not record.get("verified"):
            raise ValueError("This photo hasn't passed the chart check for every screen, so nothing will be applied.")
        if record["generation"] != self.generation:
            raise ValueError("The screen settings changed after this photo was taken. Take a new photo first.")
        with self.lock:
            displays = {d.key: d for d in self.match_displays}
        fixes = analysis.match_corrections(
            record["results"], record["comparisons"], record["states"], record["reference"],
            names={k: d.name for k, d in displays.items()}, **options)
        pairs = [(displays[key], calibration) for key, calibration in fixes.items() if key in displays]
        if not pairs:
            raise ValueError("Nothing to change: the screens already match.")
        with self.lock:
            self.apply_status = {"state": "waiting", "message": "Look at your PC: keep or go back."}
        self.notify(("apply_many", pairs))
        return {"queued": True, "title": f"changes for {len(pairs)} screen{'s' if len(pairs) > 1 else ''}"}


def _keep_upload(image, check: dict) -> None:
    """Keep the last few photos (and what the check said) on the PC, to look into odd results later.

    The photo is re-encoded from its pixels, so its metadata (GPS position, phone details) is dropped.
    """
    folder = backup.DATA_DIR / "camera-uploads"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        image.save(folder / f"{stamp}.jpg", "JPEG", quality=92)
        (folder / f"{stamp}.json").write_text(json.dumps(check, indent=2), encoding="utf-8")
        stamps = sorted({p.stem for p in folder.iterdir()})
        for old in stamps[:-KEEP_UPLOADS]:
            for path in folder.glob(f"{old}.*"):
                path.unlink(missing_ok=True)
    except OSError:
        log.warning("could not keep a copy of the upload", exc_info=True)


def _device_kind(agent: str) -> str:
    agent = agent or ""
    for needle, kind in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android device"),
                         ("Windows", "Windows PC"), ("Macintosh", "Mac"), ("Linux", "Linux device")):
        if needle in agent:
            return kind
    return "device"


# --- input validation ----------------------------------------------------------


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return float(value)


def _corners(value):
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(p, list) and len(p) == 2 for p in value):
        raise ValueError("corners must be four [x, y] points")
    return [[_number(x, -0.5, 1.5, "corner x"), _number(y, -0.5, 1.5, "corner y")] for x, y in value]


def _white_balance(value) -> dict:
    value = value if isinstance(value, dict) else {}
    mode = value.get("mode", "auto")
    if mode not in ("auto", "locked", "reference"):
        raise ValueError("unknown white balance mode")
    wb = {"mode": mode, "kelvin": _number(value.get("kelvin", 6500), 2000, 12000, "kelvin")}
    if mode == "reference":
        box = value.get("reference")
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("draw a box over the reference white first")
        wb["reference"] = [_number(v, 0, 1, "reference") for v in box]
    return wb


def _apply_options(value) -> dict:
    value = value if isinstance(value, dict) else {}
    via = value.get("via", "gpu")
    if via not in ("gpu", "monitor"):
        raise ValueError("via must be gpu or monitor")
    return {
        "target_gamma": _number(value.get("target_gamma", 2.2), 1.8, 2.6, "target gamma"),
        "fix_gamma": bool(value.get("fix_gamma", True)),
        "fix_white": bool(value.get("fix_white", False)),
        "via": via,
    }


def _match_charts(value) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("charts must be a list")
    charts = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or len(item["key"]) > 80:
            raise ValueError("each chart needs a screen key")
        charts.append({"key": item["key"], "corners": _corners(item.get("corners"))})
    return charts


def _match_options(value) -> dict:
    value = value if isinstance(value, dict) else {}
    return {
        "target_gamma": _number(value.get("target_gamma", 2.2), 1.8, 2.6, "target gamma"),
        "fix_gamma": bool(value.get("fix_gamma", True)),
        "match_white": bool(value.get("match_white", True)),
        "match_brightness": bool(value.get("match_brightness", True)),
    }


# --- HTTP ----------------------------------------------------------------------


def _make_handler(session: CameraSession):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MonitorCalibrator"
        sys_version = ""
        new_device_cookie: str | None = None

        def log_message(self, *_args):  # keep the console quiet
            pass

        def _route(self) -> str | None:
            parts = self.path.split("?", 1)[0].split("/")
            if len(parts) < 2 or not secrets.compare_digest(parts[1].encode(), session.token.encode()):
                return None
            return "/".join(parts[2:])

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(key, value)
            if self.new_device_cookie:
                self.send_header(
                    "Set-Cookie", f"{DEVICE_COOKIE}={self.new_device_cookie}; Path=/; HttpOnly; SameSite=Strict"
                )
            self.end_headers()
            self.wfile.write(body)

        def _admit(self, claim: bool) -> bool:
            """One device at a time: the page claims the session, everything else must come from that device."""
            try:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get(DEVICE_COOKIE)
            except CookieError:
                morsel = None
            allowed, self.new_device_cookie = session.admit(
                morsel.value if morsel else None, self.client_address[0], self.headers.get("User-Agent", ""), claim
            )
            return allowed

        def _turn_away(self, route: str):
            if route == "":
                return self._send(409, BUSY_PAGE.encode(), "text/html; charset=utf-8")
            return self._json(409, {"error": "Another device is using the camera check, or this one was "
                                             "disconnected on the PC. Scan the QR code on the PC again."})

        def _json(self, status: int, data: dict):
            self._send(status, json.dumps(data).encode(), "application/json")

        def _body(self, limit: int) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise ValueError("missing Content-Length") from None
            if length <= 0:
                raise ValueError("empty request")
            if length > limit:
                raise OverflowError(f"too large (max {limit // (1024 * 1024) or 1} MB)")
            return self.rfile.read(length)

        def _drain(self, limit: int = MAX_UPLOAD + MAX_JSON):
            """Discard an unread request body before answering early (refused device, too large…).

            Otherwise the client is still uploading when we close, Windows resets the connection,
            and the phone shows a network error instead of our message.
            """
            try:
                remaining = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if remaining > limit:
                return  # absurd sizes just get cut off
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 16))
                if not chunk:
                    break
                remaining -= len(chunk)

        def _json_body(self) -> dict:
            try:
                data = json.loads(self._body(MAX_JSON))
            except json.JSONDecodeError:
                raise ValueError("invalid JSON") from None
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def do_GET(self):
            self.new_device_cookie = None
            route = self._route()
            if route is None:
                return self._send(404, b"Not found", "text/plain")
            if route == "" and not self.path.split("?", 1)[0].endswith("/"):
                return self._send(302, b"", "text/plain", {"Location": f"/{session.token}/"})
            if not self._admit(claim=route == ""):
                return self._turn_away(route)
            if route == "":
                page = PAGE.read_text(encoding="utf-8").replace("{{DISPLAY}}", html.escape(session.display.name))
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if route == "status":
                return self._json(200, session.status())
            kind, _, photo_id = route.partition("/")
            if kind in ("photo", "overlay"):
                try:
                    record = session.record(photo_id)
                    body = record["preview"] if kind == "photo" else analysis.overlay(
                        record["image"], record["corners"], session.display.size
                    )
                except ValueError as exc:
                    return self._json(404, {"error": str(exc)})
                return self._send(200, body, "image/jpeg")
            return self._send(404, b"Not found", "text/plain")

        def do_POST(self):
            self.new_device_cookie = None
            route = self._route()
            if route is None:
                self._drain()
                return self._send(404, b"Not found", "text/plain")
            if not self._admit(claim=False):
                self._drain()
                return self._turn_away(route)
            try:
                if route == "upload":
                    return self._json(200, session.add_photo(self._body(MAX_UPLOAD)))
                data = self._json_body()
                if route == "analyse":
                    if "charts" in data:
                        result = session.analyse_match(str(data.get("id", "")), _match_charts(data.get("charts")),
                                                       str(data.get("reference", ""))[:80])
                    else:
                        result = session.analyse(str(data.get("id", "")), _corners(data.get("corners")),
                                                 _white_balance(data.get("wb")))
                    return self._json(200, result)
                if route == "apply":
                    photo_id = str(data.get("id", ""))
                    matching = session.record(photo_id).get("mode") == "match"
                    options = _match_options(data.get("options")) if matching else _apply_options(data.get("options"))
                    return self._json(200, session.request_apply(photo_id, options))
                if route == "chart":
                    session.notify(("chart", bool(data.get("show"))))
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "not found"})
            except OverflowError as exc:
                self._drain()
                return self._json(413, {"error": str(exc)})
            except ValueError as exc:  # includes PhotoError: problems the user can fix
                return self._json(400, {"error": str(exc)})
            except Exception:  # never leak internals to the page
                log.exception("camera request %s failed", route)
                return self._json(500, {"error": "Something went wrong on the PC. Its log has the details."})

    return Handler
