"""
Animated desktop for macOS without Plash (works on older Macs too).

Shows the assistant's wallpaper page (http://127.0.0.1:5055/ui/index) in a borderless window on every
screen, just above the desktop icons and below all normal windows, on all Spaces. Clickable.
While the assistant is still starting it shows a simple "starting" screen in the app's colours.
Started at login by a LaunchAgent (see "Instalar en Mac.command"). Needs pyobjc (Cocoa + WebKit).
"""
import os
import sys
import time
import urllib.request


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


try:
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSColor,
                        NSScreen, NSWindow, NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorIgnoresCycle, NSWindowCollectionBehaviorStationary)
    from Foundation import NSObject, NSTimer, NSURL, NSURLRequest
    from Quartz import CGWindowLevelForKey, kCGDesktopIconWindowLevelKey
    from WebKit import WKWebView, WKWebViewConfiguration
except Exception as e:                      # missing pyobjc: say so in logs/wallpaper.log and stop
    log(f"pyobjc not available: {e}")
    sys.exit(78)                            # launchd: don't hammer restarts

URL = "http://127.0.0.1:5055/ui/index"
try:
    BORDERLESS = __import__("AppKit").NSWindowStyleMaskBorderless
except AttributeError:          # very old pyobjc / macOS
    BORDERLESS = 0

# colours of the "starting" screen, by app folder (…/Application Support/<App>/app)
_APP = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) or "AI"
_THEMES = {"Lumi": ("#0f1a10", "#2c3d22", "#c9a86a"), "ZAR": ("#1a0f1a", "#3d2238", "#e7b7a3")}
BG, BG2, ACCENT = _THEMES.get(_APP, ("#0d1117", "#1f2937", "#9ca3af"))
LOADING = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:radial-gradient(circle at 50% 45%,{BG2},{BG} 70%);overflow:hidden;
font-family:-apple-system,Helvetica,sans-serif;color:{ACCENT}}}
.c{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}}
.r{{width:120px;height:120px;margin:0 auto 28px;border-radius:50%;border:2px solid {ACCENT}55;
border-top-color:{ACCENT};animation:s 2.4s linear infinite}}
h1{{font-weight:200;letter-spacing:.5em;margin:0 0 10px;font-size:40px}} p{{opacity:.6;letter-spacing:.2em;font-size:13px}}
@keyframes s{{to{{transform:rotate(360deg)}}}}</style></head><body><div class="c"><div class="r"></div>
<h1>{_APP.upper()}</h1><p>INICIANDO…</p></div></body></html>"""


def server_up() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:5055/state", timeout=1).read()
        return True
    except Exception:
        return False


class Wallpaper(NSObject):
    def init(self):
        self = objc.super(Wallpaper, self).init()
        if self is None:
            return None
        self.items = []
        self.loaded = False
        return self

    def build(self):
        level = CGWindowLevelForKey(kCGDesktopIconWindowLevelKey) + 1
        behaviour = (NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
                     | NSWindowCollectionBehaviorIgnoresCycle)
        for screen in NSScreen.screens():
            frame = screen.frame()
            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                frame, BORDERLESS, NSBackingStoreBuffered, False)
            win.setLevel_(level)
            win.setCollectionBehavior_(behaviour)
            win.setBackgroundColor_(NSColor.blackColor())
            win.setHasShadow_(False)
            win.setReleasedWhenClosed_(False)
            view = WKWebView.alloc().initWithFrame_configuration_(
                ((0, 0), (frame.size.width, frame.size.height)), WKWebViewConfiguration.alloc().init())
            win.setContentView_(view)
            win.setFrame_display_(frame, True)
            win.orderFront_(None)
            view.loadHTMLString_baseURL_(LOADING, None)
            self.items.append((win, view))
        log(f"{_APP} wallpaper: {len(self.items)} screen(s)")
        self.check_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(5.0, self, "check:", None, True)

    def reload(self):
        req = NSURLRequest.requestWithURL_(NSURL.URLWithString_(URL))
        for _, view in self.items:
            view.loadRequest_(req)

    def check_(self, _timer):
        # load the real page when the assistant's server comes up; "starting" screen again if it stops
        up = server_up()
        if up and not self.loaded:
            log("assistant is up: loading the animated desktop")
            self.reload()
            self.loaded = True
        elif not up and self.loaded:
            log("assistant stopped: waiting")
            for _, view in self.items:
                view.loadHTMLString_baseURL_(LOADING, None)
            self.loaded = False


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)      # no Dock icon
    wp = Wallpaper.alloc().init()
    wp.build()
    app.run()


if __name__ == "__main__":
    sys.exit(main())
