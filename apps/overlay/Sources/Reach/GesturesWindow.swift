import AppKit
import ReachCore
import SwiftUI

/// "Hand gestures" window (EXPERIMENTAL): the saved gestures from `.reach/gestures.json` and a form to record a new
/// one. Recording and deleting are requests to the server (`hands_record` / `hands_forget`); Hands mode does the work
/// and reports progress on the hands.jsonl feed, shown here as the status line.
@MainActor
final class GesturesWindow {
    private var window: NSWindow?
    private var timer: Timer?
    private let list: GestureList

    init(model: OverlayModel) { list = GestureList(model: model) }

    func open() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 440, height: 460), styleMask: [.titled, .closable],
                             backing: .buffered, defer: false)
            w.title = "Hand gestures (experimental)"
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: GesturesView(list: list, model: list.model))
            w.center()
            window = w
        }
        list.reload()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if self.window?.isVisible != true { self.timer?.invalidate(); self.timer = nil; return }
                self.list.reload()
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        log("gestures: window open")
    }
}

@MainActor
final class GestureList: ObservableObject {
    let model: OverlayModel
    @Published var saved: [SavedGesture] = []
    private var stamp: Date?

    init(model: OverlayModel) { self.model = model }

    private var url: URL { model.files.reachDir.appendingPathComponent("gestures.json") }

    /// Re-reads the file only when its modification date changed.
    func reload() {
        let m = (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
        guard m != stamp || m == nil else { return }
        stamp = m
        let next = HandGestures.load(try? Data(contentsOf: url))
        if next != saved { saved = next }
    }
}

private enum Does: String, CaseIterable, Identifiable {
    case top = "Go to top", bottom = "Go to bottom", keys = "Press keys…", command = "Voice command", ask = "Ask Reach…"
    var id: String { rawValue }
    var kind: GestureAction.Kind {
        switch self { case .top: .top; case .bottom: .bottom; case .keys: .keys; case .command: .command; case .ask: .ask }
    }
}

private struct GesturesView: View {
    @ObservedObject var list: GestureList
    @ObservedObject var model: OverlayModel
    @State private var name = ""
    @State private var does = Does.top
    @State private var keys = ""
    @State private var command = HandGestures.commands.first ?? ""
    @State private var prompt = ""

    private var action: GestureAction {
        let v: String
        switch does {
        case .top, .bottom: v = ""
        case .keys: v = keys.trimmingCharacters(in: .whitespaces)
        case .command: v = command
        case .ask: v = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return GestureAction(kind: does.kind, value: v)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Experimental: hand gestures are recognised by Hands mode from the camera.")
                .font(.caption).foregroundStyle(.secondary)
            GroupBox("Saved") {
                if list.saved.isEmpty {
                    Text("No gestures yet.").foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(list.saved, id: \.name) { g in
                                HStack {
                                    Text(g.line).lineLimit(1)
                                    Spacer()
                                    Button { model.send(.handsForget(name: g.name)) } label: {
                                        Image(systemName: "xmark.circle.fill")
                                    }
                                    .buttonStyle(.plain).foregroundStyle(.secondary).help("Delete \(g.name)")
                                }
                            }
                        }
                    }
                    .frame(maxHeight: 140)
                }
            }
            GroupBox("New gesture") {
                Form {
                    TextField("Name", text: $name)
                    Picker("Does", selection: $does) {
                        ForEach(Does.allCases) { Text($0.rawValue).tag($0) }
                    }
                    switch does {
                    case .keys: TextField("Keys", text: $keys, prompt: Text("cmd+shift+t"))
                    case .command:
                        Picker("Command", selection: $command) {
                            ForEach(HandGestures.commands, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) }
                        }
                    case .ask: TextField("Prompt", text: $prompt)
                    case .top, .bottom: EmptyView()
                    }
                }
                HStack {
                    Text(HandGestures.helper).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button("Record") {
                        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
                        log("gestures: record \(n) (\(action.summary))")
                        model.send(.handsRecord(name: n, action: action))
                    }
                    .disabled(!HandGestures.canRecord(name: name, action: action))
                    .keyboardShortcut(.defaultAction)
                }
            }
            Text(model.state.gesture.map { "Status: \($0.state)" }
                 ?? (model.handsOn ? "Status: waiting for Hands mode…" : "Hands mode is off (⌃⌥H turns it on)."))
                .font(.system(size: 11, design: .monospaced)).lineLimit(1)
        }
        .padding(16)
        .frame(width: 440)
    }
}
