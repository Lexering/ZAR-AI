"""
Animated desktop for macOS without Plash (works on older Macs too).

Shows the assistant's wallpaper page (http://127.0.0.1:5055/ui/index) in a borderless window on every
screen, just above the desktop icons and below all normal windows, on all Spaces. Clickable.
Started at login by a LaunchAgent (see "Instalar en Mac.command"). Needs pyobjc (Cocoa + WebKit).
"""
import sys
import urllib.request

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSColor,
                    NSScreen, NSWindow, NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorIgnoresCycle, NSWindowCollectionBehaviorStationary)
from Foundation import NSObject, NSTimer, NSURL, NSURLRequest
from Quartz import CGWindowLevelForKey, kCGDesktopIconWindowLevelKey
from WebKit import WKWebView, WKWebViewConfiguration

URL = "http://127.0.0.1:5055/ui/index"
try:
    BORDERLESS = __import__("AppKit").NSWindowStyleMaskBorderless
except AttributeError:          # very old pyobjc / macOS
    BORDERLESS = 0


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
            win.orderFront_(None)
            self.items.append((win, view))
        self.check_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(10.0, self, "check:", None, True)

    def reload(self):
        req = NSURLRequest.requestWithURL_(NSURL.URLWithString_(URL))
        for _, view in self.items:
            view.loadRequest_(req)

    def check_(self, _timer):
        # (re)load when the assistant's server comes up; show the page again after it restarts
        up = server_up()
        if up and not self.loaded:
            self.reload()
            self.loaded = True
        elif not up:
            self.loaded = False


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)      # no Dock icon
    wp = Wallpaper.alloc().init()
    wp.build()
    app.run()


if __name__ == "__main__":
    sys.exit(main())
