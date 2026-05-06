// Headless WKWebView screenshot tool.
// Usage: swift snap.swift <html> <out.png> <w> <h> <postLoadJS>
//        postLoadJS runs ~700ms after DOMContentLoaded so XHR mocks settle.

import Cocoa
import WebKit

guard CommandLine.arguments.count >= 5 else {
    FileHandle.standardError.write("usage: snap.swift <html> <out.png> <w> <h> [postLoadJS]\n".data(using: .utf8)!)
    exit(2)
}
let htmlPath = CommandLine.arguments[1]
let outPath  = CommandLine.arguments[2]
let width    = Int(CommandLine.arguments[3]) ?? 414
let height   = Int(CommandLine.arguments[4]) ?? 900
let postJS   = CommandLine.arguments.count > 5 ? CommandLine.arguments[5] : ""

let url = URL(fileURLWithPath: htmlPath)

class Snapper: NSObject, WKNavigationDelegate {
    let view: WKWebView
    let outPath: String
    let postJS: String
    init(view: WKWebView, outPath: String, postJS: String) {
        self.view = view; self.outPath = outPath; self.postJS = postJS
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // Wait for fetch mocks + DOM mutations to settle.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) {
            if !self.postJS.isEmpty {
                self.view.evaluateJavaScript(self.postJS) { _, _ in
                    DispatchQueue.main.asyncAfter(deadline: .now() + 4.0) { self.snap() }
                }
            } else {
                self.snap()
            }
        }
    }
    func snap() {
        let cfg = WKSnapshotConfiguration()
        cfg.afterScreenUpdates = true
        view.takeSnapshot(with: cfg) { image, err in
            guard let image = image, err == nil else {
                FileHandle.standardError.write("snapshot failed: \(String(describing: err))\n".data(using: .utf8)!)
                exit(3)
            }
            guard let tiff = image.tiffRepresentation,
                  let rep  = NSBitmapImageRep(data: tiff),
                  let png  = rep.representation(using: .png, properties: [:]) else {
                FileHandle.standardError.write("png encode failed\n".data(using: .utf8)!)
                exit(4)
            }
            do {
                try png.write(to: URL(fileURLWithPath: self.outPath))
                FileHandle.standardOutput.write("  wrote \(self.outPath)\n".data(using: .utf8)!)
                exit(0)
            } catch {
                FileHandle.standardError.write("write failed: \(error)\n".data(using: .utf8)!)
                exit(5)
            }
        }
    }
}

let frame = NSRect(x: 0, y: 0, width: width, height: height)
let cfg = WKWebViewConfiguration()
let view = WKWebView(frame: frame, configuration: cfg)
view.setValue(NSColor.white, forKey: "backgroundColor")
let snapper = Snapper(view: view, outPath: outPath, postJS: postJS)
view.navigationDelegate = snapper
view.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let window = NSWindow(contentRect: frame,
                      styleMask: [.borderless], backing: .buffered, defer: false)
window.contentView = view
window.makeKeyAndOrderFront(nil)

DispatchQueue.main.asyncAfter(deadline: .now() + 25) {
    FileHandle.standardError.write("timeout\n".data(using: .utf8)!)
    exit(1)
}
app.run()
