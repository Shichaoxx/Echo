import AppKit
import WebKit

// The native shell owns one supervisor. The supervisor owns the local web server.
final class EchoApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let landing = NSView()
    private let statusLabel = NSTextField(wrappingLabelWithString: "选择 Echo 工作空间，继续你的面试准备。")
    private let detailLabel = NSTextField(wrappingLabelWithString: "录音、录屏与复盘，在一个安静的地方。")
    private let spinner = NSProgressIndicator()
    private var retryButton: NSButton!
    private var chooseButton: NSButton!
    private var supervisor: Process?
    private var outputPipe: Pipe?
    private var outputBuffer = Data()
    private var serverURL: URL?
    private var projectURL: URL?
    private var scopedURL: URL?
    private var startupTimeout: Timer?
    private var stopTimer: Timer?
    private var afterStop: ((Bool) -> Void)?
    private var expectedStop = false
    private var startupError: String?
    private var quitting = false
    private var savingDownloads: [ObjectIdentifier: URL] = [:]
    private let bookmarkKey = "EchoProjectBookmark"

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        makeMenu()
        makeWindow()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let index = CommandLine.arguments.firstIndex(of: "--project"),
           CommandLine.arguments.indices.contains(index + 1) {
            selectProject(URL(fileURLWithPath: CommandLine.arguments[index + 1], isDirectory: true))
        } else if let saved = restoreProject() {
            selectProject(saved)
        } else {
            showLanding("欢迎使用 Echo", detail: "首次使用，请选择包含 Echo 程序和 Python 环境的项目目录。")
        }
    }

    private func makeMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu(title: "Echo")
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "关于 Echo", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 Echo", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 Echo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let fileItem = NSMenuItem()
        menu.addItem(fileItem)
        let fileMenu = NSMenu(title: "文件")
        fileItem.submenu = fileMenu
        fileMenu.addItem(withTitle: "选择工作空间…", action: #selector(chooseProject), keyEquivalent: "o")
        fileMenu.addItem(withTitle: "重新载入", action: #selector(reload), keyEquivalent: "r")
        fileMenu.addItem(.separator())
        fileMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        let editItem = NSMenuItem()
        menu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editItem.submenu = editMenu
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let windowItem = NSMenuItem()
        menu.addItem(windowItem)
        let windowMenu = NSMenu(title: "窗口")
        windowItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "缩放", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        NSApp.windowsMenu = windowMenu
        NSApp.mainMenu = menu
    }

    private func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Echo"
        window.titlebarAppearsTransparent = true
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.setFrameAutosaveName("EchoMainWindow")
        window.isReleasedWhenClosed = false
        let root = NSView()
        window.contentView = root

        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.isHidden = true
        webView.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            webView.topAnchor.constraint(equalTo: root.safeAreaLayoutGuide.topAnchor),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor)
        ])

        landing.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(landing)
        NSLayoutConstraint.activate([
            landing.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            landing.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            landing.topAnchor.constraint(equalTo: root.topAnchor),
            landing.bottomAnchor.constraint(equalTo: root.bottomAnchor)
        ])
        let brand = NSTextField(labelWithString: "Echo")
        brand.font = .systemFont(ofSize: 56, weight: .semibold)
        let subtitle = NSTextField(labelWithString: "本地面试资料库")
        subtitle.font = .systemFont(ofSize: 15, weight: .medium)
        subtitle.textColor = .secondaryLabelColor
        statusLabel.font = .systemFont(ofSize: 20, weight: .medium)
        statusLabel.alignment = .center
        detailLabel.font = .systemFont(ofSize: 13)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.alignment = .center
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        chooseButton = NSButton(title: "选择工作空间…", target: self, action: #selector(chooseProject))
        chooseButton.bezelStyle = .rounded
        chooseButton.controlSize = .large
        retryButton = NSButton(title: "重试", target: self, action: #selector(retry))
        retryButton.bezelStyle = .rounded
        retryButton.controlSize = .large
        retryButton.isHidden = true
        let buttons = NSStackView(views: [chooseButton, retryButton])
        buttons.orientation = .horizontal
        buttons.spacing = 10
        let stack = NSStackView(views: [brand, subtitle, spinner, statusLabel, detailLabel, buttons])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 18
        stack.setCustomSpacing(5, after: brand)
        stack.setCustomSpacing(32, after: subtitle)
        stack.setCustomSpacing(8, after: statusLabel)
        stack.setCustomSpacing(26, after: detailLabel)
        stack.translatesAutoresizingMaskIntoConstraints = false
        landing.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: landing.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: landing.centerYAnchor, constant: -12),
            stack.widthAnchor.constraint(equalToConstant: 560),
            statusLabel.widthAnchor.constraint(equalTo: stack.widthAnchor),
            detailLabel.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
    }

    @objc private func showAbout() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "开发版"
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Echo",
            .applicationVersion: version,
            .credits: NSAttributedString(string: "本地面试资料库\n桌面预览版 · 使用现有 Echo 项目环境")
        ])
    }

    @objc private func chooseProject() {
        guard !quitting, afterStop == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "选择 Echo 工作空间"
        panel.message = "选择包含 app.py、echo 文件夹和 .venv Python 环境的 Echo 项目目录。录音与结果目录可在 Echo 的设置中配置。"
        panel.prompt = "使用此工作空间"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.directoryURL = projectURL
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            self?.selectProject(url)
        }
    }

    private func validateProject(_ url: URL) -> Bool {
        let fm = FileManager.default
        var isDirectory: ObjCBool = false
        return fm.fileExists(atPath: url.appendingPathComponent("app.py").path)
            && fm.fileExists(atPath: url.appendingPathComponent("echo").path, isDirectory: &isDirectory)
            && isDirectory.boolValue
            && fm.isExecutableFile(atPath: url.appendingPathComponent(".venv/bin/python").path)
    }

    private func selectProject(_ url: URL) {
        guard validateProject(url) else {
            showLanding("这个目录还不能启动 Echo", detail: "请选择包含 app.py、echo 文件夹和可用 .venv/bin/python 的项目目录。")
            return
        }
        showLanding("正在准备工作空间", detail: "安全关闭旧服务后，将启动所选项目。", busy: true)
        stopSupervisor { [weak self] stopped in
            guard let self = self, !self.quitting else { return }
            guard stopped else {
                self.showLanding("旧服务仍在退出", detail: "请稍后重试，或退出 Echo 后重新打开。")
                return
            }
            self.scopedURL?.stopAccessingSecurityScopedResource()
            self.scopedURL = url.startAccessingSecurityScopedResource() ? url : nil
            self.projectURL = url
            self.saveProject(url)
            self.startSupervisor()
        }
    }

    private func saveProject(_ url: URL) {
        do {
            let bookmark = try url.bookmarkData(options: [.withSecurityScope], includingResourceValuesForKeys: nil, relativeTo: nil)
            UserDefaults.standard.set(bookmark, forKey: bookmarkKey)
        } catch {
            // A non-sandboxed development build can still run the selected folder.
            // Do not persist a brittle absolute-path fallback.
        }
    }

    private func restoreProject() -> URL? {
        guard let bookmark = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: bookmark, options: [.withSecurityScope, .withoutUI],
                                 relativeTo: nil, bookmarkDataIsStale: &stale) else { return nil }
        if stale { saveProject(url) }
        return url
    }

    @objc private func retry() {
        guard let url = projectURL, !quitting else { chooseProject(); return }
        selectProject(url)
    }

    @objc private func reload() {
        if serverURL != nil, supervisor?.isRunning == true {
            webView.reload()
        } else {
            retry()
        }
    }

    private func showLanding(_ message: String, detail: String, busy: Bool = false) {
        landing.isHidden = false
        webView.isHidden = true
        statusLabel.stringValue = message
        detailLabel.stringValue = detail
        retryButton.isHidden = projectURL == nil || busy
        chooseButton.isEnabled = !busy
        if busy { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
    }

    private func startSupervisor() {
        guard let project = projectURL, supervisor == nil, !quitting else { return }
        guard let script = Bundle.main.url(forResource: "supervisor", withExtension: "py") else {
            showLanding("应用缺少启动组件", detail: "请重新构建或安装 Echo，缺失 supervisor.py。")
            return
        }
        serverURL = nil
        outputBuffer.removeAll()
        expectedStop = false
        startupError = nil
        showLanding("正在启动 Echo", detail: "首次启动可能需要一点时间，面试资料会保留在本机。", busy: true)
        let process = Process()
        let pipe = Pipe()
        process.executableURL = project.appendingPathComponent(".venv/bin/python")
        process.arguments = [script.path, "--project", project.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        process.currentDirectoryURL = project
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        outputPipe = pipe
        supervisor = process
        pipe.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async {
                guard let self = self, let process = process, self.supervisor === process else { return }
                self.receiveOutput(data)
            }
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self = self, self.supervisor === finished else { return }
                let wasExpected = self.expectedStop
                self.finishSupervisor()
                if !wasExpected && !self.quitting {
                    if let message = self.startupError {
                        self.showLanding("Echo 启动失败", detail: message)
                    } else {
                        self.showLanding("Echo 服务已停止", detail: "可以重试启动。若仍失败，请查看数据目录中的 desktop.log。")
                    }
                }
            }
        }
        do {
            try process.run()
            startupTimeout = Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { [weak self] _ in
                guard let self = self, self.serverURL == nil else { return }
                self.stopSupervisor { _ in
                    self.showLanding("启动等待超时", detail: "请确认项目 Python 环境和依赖已安装，然后重试。详细原因请查看数据目录中的 desktop.log。")
                }
            }
        } catch {
            finishSupervisor()
            showLanding("无法启动 Echo", detail: "无法运行项目的 Python 环境。请检查 .venv，或重新选择工作空间。")
        }
    }

    private func receiveOutput(_ data: Data) {
        outputBuffer.append(data)
        // Only bounded JSON events are accepted, never render arbitrary stdout.
        guard outputBuffer.count < 1_048_576 else { outputBuffer.removeAll(); return }
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            guard let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let kind = event["event"] as? String else { continue }
            if kind == "ready", !expectedStop,
               let value = event["url"] as? String, let url = URL(string: value),
               url.scheme == "http", url.host == "127.0.0.1", let port = url.port,
               (1...65535).contains(port), url.user == nil, url.password == nil {
                startupTimeout?.invalidate()
                startupTimeout = nil
                serverURL = url
                webView.load(URLRequest(url: url))
            } else if kind == "error", !expectedStop {
                startupTimeout?.invalidate()
                startupTimeout = nil
                let message = String((event["message"] as? String ?? "请检查项目环境后重试。").prefix(800))
                startupError = message
                showLanding("Echo 启动失败", detail: message)
            }
        }
    }

    private func finishSupervisor() {
        startupTimeout?.invalidate()
        startupTimeout = nil
        stopTimer?.invalidate()
        stopTimer = nil
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        supervisor = nil
        serverURL = nil
        let completion = afterStop
        afterStop = nil
        completion?(true)
    }

    private func stopSupervisor(completion: @escaping (Bool) -> Void) {
        startupTimeout?.invalidate()
        startupTimeout = nil
        guard let process = supervisor, process.isRunning else {
            finishSupervisor()
            completion(true)
            return
        }
        guard afterStop == nil else { completion(false); return }
        webView.stopLoading()
        expectedStop = true
        serverURL = nil
        afterStop = completion
        process.terminate()
        stopTimer = Timer.scheduledTimer(withTimeInterval: 6, repeats: false) { [weak self] _ in
            guard let self = self else { return }
            let callback = self.afterStop
            self.afterStop = nil
            self.stopTimer = nil
            callback?(self.supervisor?.isRunning != true)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard supervisor?.isRunning == true else { return .terminateNow }
        if quitting { return .terminateLater }
        quitting = true
        // Replace a pending workspace switch with the application shutdown callback.
        afterStop = nil
        stopTimer?.invalidate()
        stopTimer = nil
        showLanding("正在退出 Echo", detail: "正在结束本地服务。", busy: true)
        stopSupervisor { _ in sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        scopedURL?.stopAccessingSecurityScopedResource()
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url = url, let server = serverURL else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == server.port
            && url.user == nil && url.password == nil
    }

    private func sourceIsLocal(_ action: WKNavigationAction) -> Bool {
        guard let port = serverURL?.port else { return false }
        let origin = action.sourceFrame.securityOrigin
        return origin.protocol == "http" && origin.host == "127.0.0.1" && origin.port == port
    }

    private func isInlineFrame(_ url: URL?) -> Bool {
        guard let url = url else { return false }
        return url.absoluteString == "about:srcdoc" || url.absoluteString == "about:blank"
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        if navigationAction.targetFrame?.isMainFrame == false,
           isInlineFrame(url), sourceIsLocal(navigationAction) {
            // Streamlit HTML components inherit the local page's origin in srcdoc.
            decisionHandler(.allow)
        } else if navigationAction.shouldPerformDownload,
           isLocal(url) || (url?.scheme == "blob" && sourceIsLocal(navigationAction)) {
            decisionHandler(.download)
        } else if isLocal(url) {
            if navigationAction.targetFrame == nil {
                decisionHandler(.cancel)
                if let url = url { webView.load(URLRequest(url: url)) }
            } else {
                decisionHandler(.allow)
            }
        } else {
            decisionHandler(.cancel)
            if navigationAction.navigationType == .linkActivated, sourceIsLocal(navigationAction),
               let url = url, ["http", "https"].contains(url.scheme ?? "") {
                NSWorkspace.shared.open(url)
            }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if !navigationResponse.isForMainFrame,
           isInlineFrame(navigationResponse.response.url), isLocal(webView.url) {
            decisionHandler(.allow)
            return
        }
        guard isLocal(navigationResponse.response.url) else { decisionHandler(.cancel); return }
        let disposition = (navigationResponse.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition")?.lowercased() ?? ""
        decisionHandler(navigationResponse.canShowMIMEType && !disposition.hasPrefix("attachment") ? .allow : .download)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard isLocal(webView.url) else { return }
        spinner.stopAnimation(nil)
        landing.isHidden = true
        webView.isHidden = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled, !expectedStop else { return }
        showLanding("页面未能载入", detail: "本地服务暂时不可用，可以重试启动。")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        showLanding("页面进程已停止", detail: "请按 ⌘R 重新载入，或点击重试。")
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        // New windows are handled in the navigation policy; never create a second web view.
        return nil
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.title = "保存 Echo 文档"
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] result in
            guard result == .OK, let destination = panel.url else { completionHandler(nil); return }
            // WebKit requires a nonexistent destination. Download beside the selected
            // path, then replace it only after a complete download and user confirmation.
            let temporary = destination.deletingLastPathComponent()
                .appendingPathComponent(".echo-download-" + UUID().uuidString)
            self?.savingDownloads[ObjectIdentifier(download)] = destination
            self?.downloadTemporary[ObjectIdentifier(download)] = temporary
            completionHandler(temporary)
        }
    }

    private var downloadTemporary: [ObjectIdentifier: URL] = [:]

    func downloadDidFinish(_ download: WKDownload) {
        let key = ObjectIdentifier(download)
        guard let destination = savingDownloads.removeValue(forKey: key),
              let temporary = downloadTemporary.removeValue(forKey: key) else { return }
        do {
            if FileManager.default.fileExists(atPath: destination.path) {
                _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary)
            } else {
                try FileManager.default.moveItem(at: temporary, to: destination)
            }
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            showDownloadError()
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let key = ObjectIdentifier(download)
        savingDownloads.removeValue(forKey: key)
        if let temporary = downloadTemporary.removeValue(forKey: key) { try? FileManager.default.removeItem(at: temporary) }
        if (error as NSError).code != NSURLErrorCancelled { showDownloadError() }
    }

    private func showDownloadError() {
        let alert = NSAlert()
        alert.messageText = "文档未能保存"
        alert.informativeText = "请检查目标文件夹的写入权限，然后重新下载。"
        alert.beginSheetModal(for: window)
    }
}

let application = NSApplication.shared
let delegate = EchoApp()
application.delegate = delegate
application.run()
