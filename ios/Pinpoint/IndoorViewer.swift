import SwiftUI
import WebKit
import Network

/// Native controls drive the same Matterport scene component used by the dispatcher.
@MainActor
final class IndoorGuide: NSObject, ObservableObject, WKScriptMessageHandler, WKNavigationDelegate {
    @Published var ready = false
    @Published var busy = false
    @Published var waypoint = 0
    @Published var total = 0
    @Published var floor = 1
    @Published var mode = "dollhouse"
    @Published var onRoute = true
    @Published var error: String?
    @Published var positionLabel = "Reception entrance"
    let webView: WKWebView
    private var server: ViewerServer?
    private var started = false
    private var generation = UUID()
    private var originPort: Int?
    var atDestination: Bool { ready && !busy && error == nil && onRoute && total > 0 && waypoint == total - 1 }

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        // Weak handler avoids the WebKit -> controller -> WebKit retain cycle.
        configuration.userContentController.add(WeakViewerHandler(self), name: "responder")
        webView.navigationDelegate = self
        webView.scrollView.isScrollEnabled = false
        webView.isOpaque = false
        webView.backgroundColor = .secondarySystemBackground
        #if DEBUG
        webView.isInspectable = true
        #endif
    }

    func start() {
        guard !started else { return }
        started = true
        guard let root = Bundle.main.resourceURL?.appendingPathComponent("ResponderViewer"),
              FileManager.default.fileExists(atPath: root.appendingPathComponent("config.json").path) else {
            error = "The building viewer is missing. Rebuild the app to restore it."
            return
        }
        let generation = self.generation
        do {
            server = try ViewerServer(root: root) { [weak self] result in
                Task { @MainActor in
                    guard let self, self.started, self.generation == generation else { return }
                    switch result {
                    case .success(let url):
                        self.originPort = url.port
                        self.webView.load(URLRequest(url: url))
                    case .failure: self.error = "The local viewer could not start. Tap Retry."
                    }
                }
            }
        } catch { self.error = "The local viewer could not start. Tap Retry." }
    }

    func reset() {
        started = false; generation = UUID(); originPort = nil
        ready = false; busy = false; error = nil; waypoint = 0; total = 0
        mode = "dollhouse"; onRoute = true; floor = 1; positionLabel = "Reception entrance"
        server?.stop(); server = nil
        webView.stopLoading()
        webView.loadHTMLString("", baseURL: nil)
    }

    func retry() {
        reset()
        start()
    }

    func command(_ action: String, reduceMotion: Bool) {
        guard ready && !busy, ["dollhouse", "inside", "next", "previous"].contains(action) else { return }
        busy = true; error = nil
        let generation = self.generation
        webView.evaluateJavaScript("void window.responder.command('\(action)', \(reduceMotion ? "true" : "false"));") { [weak self] _, error in
            guard let self, self.started, self.generation == generation else { return }
            if error != nil { self.busy = false; self.error = "The viewer could not respond. Tap Retry." }
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard started, message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.port == originPort,
              message.frameInfo.securityOrigin.host == "localhost",
              let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        if type == "error" || type == "actionError" {
            error = body["message"] as? String ?? "The viewer could not load. Tap Retry."
            if type == "error" { ready = false }
            busy = false; return
        }
        guard type == "state" else { return }
        ready = body["ready"] as? Bool ?? false
        busy = body["busy"] as? Bool ?? false
        waypoint = body["index"] as? Int ?? 0
        total = body["total"] as? Int ?? 0
        floor = body["floor"] as? Int ?? 1
        mode = body["mode"] as? String ?? "dollhouse"
        onRoute = body["onRoute"] as? Bool ?? true
        positionLabel = body["label"] as? String ?? ""
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard started, (error as NSError).code != NSURLErrorCancelled else { return }
        ready = false; busy = false
        self.error = "The building viewer could not load. Tap Retry."
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard started else { return }
        ready = false; busy = false; error = "The 3D viewer paused. Tap Retry to reload the scan."
    }
}

private final class WeakViewerHandler: NSObject, WKScriptMessageHandler {
    weak var target: IndoorGuide?
    init(_ target: IndoorGuide) { self.target = target }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(userContentController, didReceive: message)
    }
}

struct IndoorViewer: UIViewRepresentable {
    @ObservedObject var guide: IndoorGuide
    func makeUIView(context: Context) -> WKWebView { guide.start(); return guide.webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

/// Read-only asset server bound to loopback, so the SDK sees its authorized localhost origin.
/// It exposes only the bundled viewer folder, never the dispatcher API or user files.
private final class ViewerServer: @unchecked Sendable {
    private let listener: NWListener
    private let root: URL
    private let queue = DispatchQueue(label: "pinpoint.viewer.assets")
    private var connections: [UUID: NWConnection] = [:]
    init(root: URL, completion: @escaping @Sendable (Result<URL, Error>) -> Void) throws {
        self.root = root.resolvingSymlinksInPath().standardizedFileURL
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        listener.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if let port = self?.listener.port,
                   let url = URL(string: "http://localhost:\(port.rawValue)/index.html") { completion(.success(url)) }
            case .failed(let error): completion(.failure(error))
            default: break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            let id = UUID(); self.connections[id] = connection
            connection.start(queue: self.queue)
            self.receive(connection, id: id, accumulated: Data())
            self.queue.asyncAfter(deadline: .now() + 15) { [weak self, weak connection] in
                connection?.cancel(); self?.connections.removeValue(forKey: id)
            }
        }
        listener.start(queue: queue)
    }
    func stop() {
        listener.cancel()
        queue.async { [self] in connections.values.forEach { $0.cancel() }; connections.removeAll() }
    }
    private func receive(_ connection: NWConnection, id: UUID, accumulated: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16384) { [weak self] data, _, complete, error in
            guard let self else { connection.cancel(); return }
            var buffer = accumulated
            if let data { buffer.append(data) }
            if buffer.count > 32768 { self.send(connection, id: id, status: "413 Payload Too Large"); return }
            if let text = String(data: buffer, encoding: .utf8), text.contains("\r\n\r\n") { self.respond(connection, id: id, request: text); return }
            if complete || error != nil { connection.cancel(); self.connections.removeValue(forKey: id); return }
            self.receive(connection, id: id, accumulated: buffer)
        }
    }
    private func respond(_ connection: NWConnection, id: UUID, request: String) {
        let parts = request.components(separatedBy: "\r\n").first?.split(separator: " ") ?? []
        guard parts.count == 3, parts[0] == "GET" || parts[0] == "HEAD" else { send(connection, id: id, status: "405 Method Not Allowed"); return }
        let rawPath = String(parts[1]).components(separatedBy: "?")[0]
        guard let path = rawPath.removingPercentEncoding, path.hasPrefix("/"),
              !path.split(separator: "/").contains(where: { $0 == ".." || $0.hasPrefix(".") }) else { send(connection, id: id, status: "403 Forbidden"); return }
        let file = root.appendingPathComponent(String(path.dropFirst())).standardizedFileURL.resolvingSymlinksInPath()
        guard file.path.hasPrefix(root.path + "/"), let data = try? Data(contentsOf: file) else { send(connection, id: id, status: "404 Not Found"); return }
        let types = ["html":"text/html", "js":"text/javascript", "css":"text/css", "json":"application/json", "wasm":"application/wasm", "svg":"image/svg+xml", "png":"image/png", "jpg":"image/jpeg", "jpeg":"image/jpeg", "woff":"font/woff", "woff2":"font/woff2", "ttf":"font/ttf", "glb":"model/gltf-binary"]
        send(connection, id: id, status: "200 OK", data: data, type: types[file.pathExtension] ?? "application/octet-stream", head: parts[0] == "HEAD")
    }
    private func send(_ connection: NWConnection, id: UUID, status: String, data: Data = Data(), type: String = "text/plain", head: Bool = false) {
        var result = Data("HTTP/1.1 \(status)\r\nContent-Type: \(type)\r\nContent-Length: \(data.count)\r\nCache-Control: no-cache\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n".utf8)
        if !head { result.append(data) }
        connection.send(content: result, completion: .contentProcessed { [weak self] _ in connection.cancel(); self?.connections.removeValue(forKey: id) })
    }
    deinit { listener.cancel() }
}
