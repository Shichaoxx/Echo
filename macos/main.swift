import AppKit
import WebKit

// Each window owns one ephemeral loopback service. All durable data lives outside the bundle.
@MainActor
final class Desktop: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    private let files = FileManager.default
    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1190, height: 800),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable],
                                  backing: .buffered, defer: false)
    private let web: WKWebView = {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        return WKWebView(frame: .zero, configuration: configuration)
    }()
    private let status = NSTextField(labelWithString: "")
    private var service: Process?
    private var lifetime: Pipe?
    private var origin: URL?
    private var demo = CommandLine.arguments.contains("--demo")
    private var leaving = false
    private var downloads: [ObjectIdentifier: (staging: URL, destination: URL)] = [:]
    private var support: URL {
        files.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    }
    private var resources: URL { Bundle.main.resourceURL! }

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        window.delegate = self
        window.minSize = NSSize(width: 420, height: 380)
        window.setFrameAutosaveName("Echo1.MainWindow")
        if !window.setFrameUsingName("Echo1.MainWindow") { window.center() }
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setAccessibilityLabel("Echo 纪要")
        window.contentView = web
        status.translatesAutoresizingMaskIntoConstraints = false
        status.textColor = .secondaryLabelColor
        web.addSubview(status)
        NSLayoutConstraint.activate([status.centerXAnchor.constraint(equalTo: web.centerXAnchor),
                                     status.centerYAnchor.constraint(equalTo: web.centerYAnchor)])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        start()
    }

    private func makeMenu() {
        let bar = NSMenu()
        func menu(_ title: String) -> NSMenu {
            let item = NSMenuItem()
            let child = NSMenu(title: title)
            item.submenu = child
            bar.addItem(item)
            return child
        }
        func add(_ label: String, _ action: Selector, _ key: String, _ menu: NSMenu, _ target: AnyObject? = nil) {
            let item = menu.addItem(withTitle: label, action: action, keyEquivalent: key)
            item.target = target
        }
        let app = menu("Echo")
        add("关于 Echo", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), "", app, NSApp)
        app.addItem(.separator())
        add("隐藏 Echo", #selector(NSApplication.hide(_:)), "h", app, NSApp)
        add("退出 Echo", #selector(NSApplication.terminate(_:)), "q", app, NSApp)
        let library = menu("资料库")
        add("打开个人资料库", #selector(openPersonal), "", library, self)
        add("打开示例资料库", #selector(openDemo), "", library, self)
        library.addItem(.separator())
        add("选择转写模型…", #selector(chooseModel), "", library, self)
        let edit = menu("编辑")
        for (label, action, key) in [("撤销","undo:","z"), ("剪切","cut:","x"),
                                      ("拷贝","copy:","c"), ("粘贴","paste:","v"), ("全选","selectAll:","a")] {
            add(label, NSSelectorFromString(action), key, edit)
        }
        let windows = menu("窗口")
        add("最小化", #selector(NSWindow.performMiniaturize(_:)), "m", windows)
        add("关闭", #selector(NSWindow.performClose(_:)), "w", windows)
        NSApp.windowsMenu = windows
        NSApp.mainMenu = bar
    }

    @objc private func openPersonal() { changeLibrary(false) }
    @objc private func openDemo() { changeLibrary(true) }
    private func changeLibrary(_ next: Bool) {
        guard demo != next else { return }
        confirmLeaving { [weak self] in self?.demo = next; self?.start() }
    }

    private func hasModel(_ folder: URL) -> Bool {
        files.fileExists(atPath: folder.appendingPathComponent("config.json").path) &&
        ["weights.safetensors","weights.npz"].contains { files.fileExists(atPath: folder.appendingPathComponent($0).path) }
    }
    private func modelFolder() -> URL? {
        let preferred = UserDefaults.standard.string(forKey: "ModelDirectory").map { URL(fileURLWithPath: $0) }
        return [preferred, resources.appendingPathComponent("model-turbo"),
                support.appendingPathComponent("Echo 1.x/model-turbo"),
                support.appendingPathComponent("Echo/model-turbo")].compactMap { $0 }.first(where: hasModel)
    }
    @objc private func chooseModel() {
        let panel = NSOpenPanel()
        panel.title = "选择 Whisper Turbo 模型文件夹"
        panel.message = "选择同时包含 config.json 和 weights.safetensors 或 weights.npz 的文件夹。"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.directoryURL = modelFolder()?.deletingLastPathComponent()
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        guard hasModel(folder) else { message("模型文件不完整", panel.message); return }
        confirmLeaving { [weak self] in
            UserDefaults.standard.set(folder.path, forKey: "ModelDirectory")
            self?.start()
        }
    }

    private func start() {
        stop()
        web.stopLoading()
        // Remove the preceding workspace immediately, including any playing media.
        web.loadHTMLString("", baseURL: nil)
        status.stringValue = "正在打开本机资料库…"
        status.isHidden = false
        window.title = demo ? "Echo · 示例资料库" : "Echo"
        let env = ProcessInfo.processInfo.environment
        let data = env[demo ? "ECHO_DEMO_DIR" : "ECHO_DATA_DIR"].map { URL(fileURLWithPath: $0) }
            ?? support.appendingPathComponent(demo ? "Echo 1.x/demo" : "Echo 1.x/library")
        let task = Process(), output = Pipe(), input = Pipe()
        task.executableURL = resources.appendingPathComponent("runtime/bin/python3")
        task.currentDirectoryURL = resources
        var arguments = ["-BEs", "-m", "server", "--port", "0", "--data-dir", data.path, "--exit-on-stdin-eof"]
        if demo { arguments.append("--seed-examples") }
        if let folder = modelFolder() { arguments += ["--model-dir", folder.path] }
        task.arguments = arguments
        var clean = env.filter { key, _ in !key.hasPrefix("PYTHON") && !key.hasPrefix("DYLD_") &&
                                          !key.hasPrefix("ECHO_") && key != "VIRTUAL_ENV" }
        clean["PATH"] = resources.appendingPathComponent("media-tools").path + ":/usr/bin:/bin"
        clean["PYTHONNOUSERSITE"] = "1"
        clean["PYTHONDONTWRITEBYTECODE"] = "1"
        task.environment = clean
        task.standardInput = input
        task.standardOutput = output
        task.standardError = FileHandle.standardError
        service = task
        lifetime = input
        task.terminationHandler = { [weak self] child in
            DispatchQueue.main.async {
                guard let self, self.service === child else { return }
                self.failed("本机服务已退出（状态码 " + String(child.terminationStatus) + "）。")
            }
        }
        do { try task.run() } catch { failed(error.localizedDescription); return }
        DispatchQueue.global(qos: .userInitiated).async {
            var line = Data()
            let handle = output.fileHandleForReading
            while line.count < 8192 && !line.contains(10) {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                line.append(chunk)
            }
            let info = try? JSONSerialization.jsonObject(with: Data(line.prefix(while: { $0 != 10 }))) as? [String: String]
            DispatchQueue.main.async {
                guard self.service === task else { return }
                guard let raw = info?["url"], let url = URL(string: raw), url.scheme == "http",
                      url.host == "127.0.0.1", url.port != nil else { self.failed("无法读取本机服务的启动地址。"); return }
                self.origin = url
                self.web.load(URLRequest(url: url))
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in
            guard let self, self.service === task, self.origin == nil else { return }
            self.failed("本机服务启动超时。")
        }
    }

    private func stop() {
        let previous = service
        service = nil
        origin = nil
        try? lifetime?.fileHandleForWriting.close()
        lifetime = nil
        if previous?.isRunning == true { previous?.terminate() }
    }
    private func failed(_ detail: String) {
        stop()
        status.stringValue = "本机资料库暂不可用"
        status.isHidden = false
        let alert = NSAlert()
        alert.messageText = "无法打开 Echo"
        alert.informativeText = detail
        alert.addButton(withTitle: "重新打开")
        alert.addButton(withTitle: "退出")
        if alert.runModal() == .alertFirstButtonReturn { start() } else { NSApp.terminate(nil) }
    }
    private func message(_ title: String, _ text: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        alert.runModal()
    }
    private func confirmLeaving(_ proceed: @escaping () -> Void, cancel: @escaping () -> Void = {}) {
        guard !leaving else { cancel(); return }
        guard origin != nil else { proceed(); return }
        leaving = true
        web.evaluateJavaScript("window.dispatchEvent(new Event('beforeunload', {cancelable:true}))") { result, _ in
            self.leaving = false
            if (result as? Bool) == false {
                let alert = NSAlert()
                alert.messageText = "放弃尚未保存的修订？"
                alert.addButton(withTitle: "继续编辑")
                alert.addButton(withTitle: "放弃修订")
                if alert.runModal() != .alertSecondButtonReturn { cancel(); return }
            }
            proceed()
        }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard origin != nil else { return .terminateNow }
        guard !leaving else { return .terminateCancel }
        confirmLeaving({ sender.reply(toApplicationShouldTerminate: true) },
                       cancel: { sender.reply(toApplicationShouldTerminate: false) })
        return .terminateLater
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    func applicationWillTerminate(_ notification: Notification) {
        stop()
        for item in downloads.values { try? files.removeItem(at: item.staging.deletingLastPathComponent()) }
    }

    private func local(_ url: URL) -> Bool {
        origin != nil && url.scheme == origin?.scheme && url.host == origin?.host && url.port == origin?.port
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if action.shouldPerformDownload && url.scheme == "blob",
           let inside = URL(string: String(url.absoluteString.dropFirst(5))), local(inside) {
            decisionHandler(.download)
        } else if local(url) || url.absoluteString == "about:blank" {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let url = webView.url, local(url) { status.isHidden = true; window.makeFirstResponder(webView) }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if let origin { webView.load(URLRequest(url: origin)) }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "取消")
        alert.addButton(withTitle: "确定")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertSecondButtonReturn) }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let destination = panel.url else { completionHandler(nil); return }
            do {
                // WebKit requires a nonexistent path. Only replace an existing export after all bytes arrive.
                let directory = self.files.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                try self.files.createDirectory(at: directory, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
                let staging = directory.appendingPathComponent("export")
                self.downloads[ObjectIdentifier(download)] = (staging, destination)
                completionHandler(staging)
            } catch { completionHandler(nil); self.message("无法导出文件", error.localizedDescription) }
        }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let item = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        defer { try? files.removeItem(at: item.staging.deletingLastPathComponent()) }
        do { try Data(contentsOf: item.staging).write(to: item.destination, options: .atomic) }
        catch { message("无法保存文件", error.localizedDescription) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let item = downloads.removeValue(forKey: ObjectIdentifier(download)) {
            try? files.removeItem(at: item.staging.deletingLastPathComponent())
        }
        if (error as NSError).code != NSURLErrorCancelled { message("导出失败", error.localizedDescription) }
    }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let desktop = Desktop()
    app.delegate = desktop
    app.setActivationPolicy(.regular)
    withExtendedLifetime(desktop) { app.run() }
}
