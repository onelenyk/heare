import AppKit
import AVFoundation
import ServiceManagement
import WebKit

/// A window around one local page. Links that lead off this machine open
/// in the person's browser; the window itself only shows heare.
class PageWindow: NSObject, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    let window: NSWindow
    let web: WKWebView
    private var target: URL?
    private var tries = 0

    init(title: String, size: NSSize, configuration: WKWebViewConfiguration = WKWebViewConfiguration()) {
        web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: configuration)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = title
        window.isReleasedWhenClosed = false
        window.contentView = web
        window.center()
        window.delegate = self
        web.navigationDelegate = self
        web.uiDelegate = self
    }

    func front() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    /// Load a page that may not be up yet: the keeper and the brain take a
    /// few seconds to open their ports after the app starts them.
    func load(_ url: URL) {
        target = url
        tries = 0
        web.load(URLRequest(url: url))
    }

    func waiting(_ text: String) {
        web.loadHTMLString("""
            <meta charset="utf-8"><body style="font:15px -apple-system;display:grid;place-content:center;
            height:90vh;color:#888;background:Canvas">\(text)</body>
            """, baseURL: nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        guard let target, tries < 60 else { return }
        tries += 1
        if tries == 1 { waiting("heare прокидається…") }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.web.load(URLRequest(url: target))
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let scheme = url.scheme, scheme.hasPrefix("http"),
           url.host != "127.0.0.1" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // target=_blank and window.open: the person's browser, not a bare window.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // The page on this machine may listen through the window (its own web
    // channel); macOS still asks the person once, for heare.app.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(origin.host == "127.0.0.1" ? .grant : .deny)
    }
}

/// The installer, which stays as the settings. The page asks the shell for
/// what a page cannot do: the microphone, and starting at login.
final class InstallerWindow: PageWindow, WKScriptMessageHandler {
    var onDone: (() -> Void)?
    private let controller = WKUserContentController()

    init() {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        super.init(title: "heare — налаштування", size: NSSize(width: 640, height: 760),
                   configuration: configuration)
        controller.add(self, name: "heare")
    }

    func show(_ config: Config) {
        // What only the shell knows, told to the page before it draws: the
        // login switch shows the truth, not a guess.
        controller.removeAllUserScripts()
        let said = "window.heareShell = {login: \(Login.on)};"
        controller.addUserScript(WKUserScript(source: said, injectionTime: .atDocumentStart,
                                              forMainFrameOnly: true))
        load(config.setupBase)
        front()
    }

    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any] else { return }
        if body["ask"] as? String == "microphone" {
            AVCaptureDevice.requestAccess(for: .audio) { _ in }
        }
        if let on = body["login"] as? Bool { Login.set(on) }
        if body["done"] as? Bool == true {
            window.close()
            onDone?()
        }
    }
}

/// Starting with the Mac: one switch, read from and written to the system.
enum Login {
    static var on: Bool { SMAppService.mainApp.status == .enabled }

    static func set(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            NSLog("heare: login item: %@", error.localizedDescription)
        }
    }
}

/// The brain's page, in a window of the app's own.
///
/// The brain lets a browser in only with a key of its own: a window pairs
/// with a one-time code and keeps the key in an HttpOnly cookie. A browser
/// would have to be handed that code (typed by the person) or the master
/// token in its URL (left in history, and the master in a page) — so the
/// app is the window. As the person, it can read the master token; it uses
/// it for one thing only, minting a code, and pairs like any other window.
/// The key lives in this web view's own store, so a second open reuses it.
final class HeareWindow: PageWindow {
    private static let cookie = "heare_session"
    private var pairing = false

    init() {
        super.init(title: "heare", size: NSSize(width: 1100, height: 780))
    }

    func show(_ config: Config) {
        front()
        if web.url?.host == "127.0.0.1" && web.url?.port == config.apiPort { return }
        guard !pairing else { return }
        pairing = true
        waiting("heare прокидається…")
        withKey(config, tries: 0) { [weak self] in
            self?.pairing = false
            self?.load(config.brainBase)
        }
    }

    private var store: WKHTTPCookieStore { web.configuration.websiteDataStore.httpCookieStore }

    private func withKey(_ config: Config, tries: Int, then: @escaping () -> Void) {
        store.getAllCookies { cookies in
            guard let held = cookies.first(where: { $0.name == Self.cookie && $0.domain == "127.0.0.1" }) else {
                self.pair(config, tries: tries, then: then)
                return
            }
            Local.call(config.brainBase.appendingPathComponent("api/state"),
                       headers: ["Cookie": "\(Self.cookie)=\(held.value)"]) { answer in
                if answer.status == 200 { then() } else { self.pair(config, tries: tries, then: then) }
            }
        }
    }

    private func pair(_ config: Config, tries: Int, then: @escaping () -> Void) {
        // No master here: the page shows its own pairing form.
        guard let master = config.masterToken else {
            if tries < 30 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.withKey(config, tries: tries + 1, then: then) }
            } else { then() }
            return
        }
        let base = config.brainBase
        Local.call(base.appendingPathComponent("api/pair-code"), method: "POST",
                   headers: ["X-Heare-Token": master]) { answer in
            guard let code = (answer.json as? [String: Any])?["code"] as? String else {
                // The brain is still starting: its port is not open yet.
                if tries < 30 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.withKey(config, tries: tries + 1, then: then) }
                } else { then() }
                return
            }
            let pairURL = base.appendingPathComponent("api/pair")
            Local.call(pairURL, method: "POST",
                       body: ["code": code, "kind": "window", "name": "heare-app"]) { answer in
                let fields = (answer.response?.allHeaderFields as? [String: String]) ?? [:]
                let cookies = HTTPCookie.cookies(withResponseHeaderFields: fields, for: pairURL)
                    .filter { $0.name == Self.cookie }
                guard !cookies.isEmpty else { then(); return }
                let group = DispatchGroup()
                for cookie in cookies {
                    group.enter()
                    self.store.setCookie(cookie) { group.leave() }
                }
                group.notify(queue: .main, execute: then)
            }
        }
    }
}
