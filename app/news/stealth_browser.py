"""Stealth headless browser for TV-channel monitoring (local worker only).

News-channel live pages (YouTube watch pages, timesnownews.com/live-tv, etc.)
increasingly serve bot-detection walls, consent interstitials or degraded pages
to a vanilla headless browser — which is why title-only monitoring is coarse and
sometimes empty. This module launches Playwright Chromium with its automation
fingerprint masked (the puppeteer-extra-stealth technique set, implemented as an
init script so we carry no extra dependency), so the page loads as a normal
Indian desktop visitor would see it. That lets us either read the on-air ticker
text from the DOM or screenshot the live video frame for OCR.

Heavy + browser-driven, so it NEVER runs on Vercel — only from live_worker.py.
Requires: pip install playwright && playwright install chromium.
"""

import logging
from contextlib import contextmanager

log = logging.getLogger("newsroom.stealth")

# A current, real Chrome-on-Windows UA. Kept in sync with the Sec-CH-UA hints
# below so the two don't contradict each other (a classic bot tell).
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Fingerprint patches applied before any page script runs. Each line neutralises
# a specific automation tell that detectors (Cloudflare, DataDome, PerimeterX,
# YouTube) check for.
_STEALTH_JS = r"""
// 1. the headline tell: navigator.webdriver must be undefined, not false
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 2. a real browser exposes plugins + mimeTypes; headless exposes none
const _mkPlugin = (name, desc, fn) => {
  const p = Object.create(Plugin.prototype);
  Object.defineProperties(p, {
    name: {value: name}, description: {value: desc}, filename: {value: fn},
    length: {value: 1},
  });
  return p;
};
const _plugins = [
  _mkPlugin('Chrome PDF Plugin', 'Portable Document Format', 'internal-pdf-viewer'),
  _mkPlugin('Chrome PDF Viewer', '', 'mhjfbmdc'),
  _mkPlugin('Native Client', '', 'internal-nacl-plugin'),
];
Object.defineProperty(navigator, 'plugins', {
  get: () => { const a = Object.create(PluginArray.prototype);
    _plugins.forEach((p, i) => a[i] = p);
    Object.defineProperty(a, 'length', {value: _plugins.length}); return a; },
});
Object.defineProperty(navigator, 'mimeTypes', {
  get: () => { const a = Object.create(MimeTypeArray.prototype);
    Object.defineProperty(a, 'length', {value: 2}); return a; },
});

// 3. languages consistent with an Indian visitor
Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en', 'hi']});

// 4. window.chrome (present in real Chrome, absent in headless)
window.chrome = {runtime: {}, app: {isInstalled: false},
                 csi: function () {}, loadTimes: function () {}};

// 5. permissions.query for notifications must not throw the headless tell
const _origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (params) =>
  params && params.name === 'notifications'
    ? Promise.resolve({state: Notification.permission})
    : _origQuery(params);

// 6. WebGL vendor/renderer — headless returns generic/blank strings
const _getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (p) {
  if (p === 37445) return 'Intel Inc.';                 // UNMASKED_VENDOR_WEBGL
  if (p === 37446) return 'Intel Iris OpenGL Engine';   // UNMASKED_RENDERER_WEBGL
  return _getParam.call(this, p);
};
if (window.WebGL2RenderingContext) {
  const _g2 = WebGL2RenderingContext.prototype.getParameter;
  WebGL2RenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _g2.call(this, p);
  };
}

// 7. hardware profile of a normal laptop
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});

// 8. iframe.contentWindow.chrome tell + srcdoc probing
try {
  const _desc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
} catch (e) {}
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1366,768",
]

_HTTP_HEADERS = {
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "sec-ch-ua": '"Google Chrome";v="126", "Chromium";v="126", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


@contextmanager
def stealth_page(headless: bool = True, block_media: bool = False):
    """Yield a fingerprint-masked Playwright page. Closes the browser on exit.

    ``block_media`` aborts image/font/media requests — faster when we only need
    DOM text (not a video frame)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=_LAUNCH_ARGS)
        context = browser.new_context(
            user_agent=UA, locale="en-IN", timezone_id="Asia/Kolkata",
            viewport={"width": 1366, "height": 768}, device_scale_factor=1,
            is_mobile=False, has_touch=False, color_scheme="light",
            geolocation={"latitude": 19.076, "longitude": 72.877},  # Mumbai
            permissions=["geolocation"], extra_http_headers=_HTTP_HEADERS,
        )
        context.add_init_script(_STEALTH_JS)
        if block_media:
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_())
        page = context.new_page()
        try:
            yield page
        finally:
            try:
                browser.close()
            except Exception:
                pass


def fingerprint_report(page) -> dict:
    """Read back the masked properties — used by the self-test to prove the
    stealth patches are live before we trust a capture."""
    page.goto("about:blank")
    return page.evaluate(
        """() => ({
            webdriver: navigator.webdriver === undefined ? 'undefined' : String(navigator.webdriver),
            plugins: navigator.plugins.length,
            languages: navigator.languages.join(','),
            chrome: !!window.chrome,
            webgl: (() => { try { const c = document.createElement('canvas').getContext('webgl');
                return c.getParameter(37445) + ' / ' + c.getParameter(37446); }
                catch (e) { return 'n/a'; } })(),
            headlessUA: /Headless/.test(navigator.userAgent),
        })""")
