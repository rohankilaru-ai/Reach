import AppKit
import ReachCore
import SwiftUI

enum Palette {
    static let accent = Color(red: 0.42, green: 0.86, blue: 0.80)     // sea-glass
    static let accentNS = NSColor(red: 0.42, green: 0.86, blue: 0.80, alpha: 1)
    static func color(_ p: Phase) -> Color {
        switch p {
        case .idle: return Color.white.opacity(0.45)
        case .working: return accent
        case .needsYou: return Color(red: 0.98, green: 0.76, blue: 0.36)
        case .done: return Color(red: 0.55, green: 0.88, blue: 0.52)
        case .failed: return Color(red: 0.98, green: 0.45, blue: 0.42)
        }
    }
}

extension Palette {
    static func status(_ s: HubStatus) -> Color {
        switch s {
        case .idle: return Color.white.opacity(0.5)
        case .listening: return accent
        case .working: return Color(red: 0.55, green: 0.70, blue: 1.0)
        case .done: return color(.done)
        case .failed: return color(.failed)
        case .needsYou: return color(.needsYou)
        }
    }
}

/// The status dot: colour says the state, a soft ripple while listening or working.
struct StatusDot: View {
    var status: HubStatus
    var size: CGFloat = 8
    @State private var ripple = false

    var body: some View {
        ZStack {
            if status.pulses {
                Circle().stroke(Palette.status(status).opacity(0.6), lineWidth: 1.5)
                    .scaleEffect(ripple ? 2.4 : 1).opacity(ripple ? 0 : 0.9)
            }
            Circle().fill(Palette.status(status))
        }
        .frame(width: size, height: size)
        .onAppear { withAnimation(.easeOut(duration: 1.2).repeatForever(autoreverses: false)) { ripple = true } }
        .animation(.easeInOut(duration: 0.25), value: status)
    }
}

/// Five bars driven by the microphone level while the key is held.
struct LevelMeter: View {
    var level: Float
    private let weights: [CGFloat] = [0.55, 0.85, 1.0, 0.8, 0.5]
    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<5, id: \.self) { i in
                Capsule().fill(Palette.accent)
                    .frame(width: 3, height: 4 + 12 * CGFloat(level) * weights[i])
            }
        }
        .frame(height: 16)
        .animation(.spring(response: 0.18, dampingFraction: 0.6), value: level)
    }
}

private struct ContentHeight: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct PanelView: View {
    @ObservedObject var model: OverlayModel
    @FocusState private var focused: Bool
    @State private var breathe = false
    @State private var contentHeight: CGFloat = 0
    var onSnap: () -> Void = {}
    var onDragEnd: () -> Void = {}
    var onSpeak: () -> Void = {}
    var onZoom: () -> Void = {}

    static let hint = "Hold Option-Space, circle what you mean, say what to do."

    private var status: HubStatus { HubStatus.of(phase: model.state.phase, listening: model.listening) }

    var body: some View {
        ZStack(alignment: .top) {
            if model.expanded {
                expanded.transition(.asymmetric(insertion: .scale(scale: 0.92, anchor: .top).combined(with: .opacity),
                                                removal: .opacity))
            } else {
                pill.transition(.opacity)
            }
        }
        .animation(.spring(response: 0.34, dampingFraction: 0.78), value: model.expanded)
        .environment(\.colorScheme, .dark)
    }

    /// The idle island: a compact dark glass capsule that breathes softly. Drag it; double-click snaps it back.
    private var pill: some View {
        HStack(spacing: 8) {
            StatusDot(status: status)
            if model.listening {
                LevelMeter(level: model.level)
            } else {
                Text("Reach").font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.9))
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
        .background(Capsule().fill(.black.opacity(0.9)))
        .overlay(Capsule().stroke(Palette.status(status).opacity(breathe && status == .idle ? 0.28 : 0.10), lineWidth: 1))
        .scaleEffect(breathe && status == .idle ? 1.02 : 1)
        .padding(4)   // room for the breath, so nothing is clipped by the window
        .onAppear { withAnimation(.easeInOut(duration: 2.6).repeatForever(autoreverses: true)) { breathe = true } }
        .overlay(DragHandle(onClick: { n in if n >= 2 { onSnap() } else { model.expandRequested?() } },
                            onDragEnd: onDragEnd))
        .help(Self.hint)
    }

    private var title: String {
        let s = model.state
        if model.listening { return "Listening…" }
        if s.phase == .working { return s.note.map { "Working · \($0)" } ?? "Working…" }
        if let v = s.verdict, s.phase != .idle || s.note != nil { return v.title }
        if s.phase == .needsYou { return "Needs you" + (s.note.map { ": \($0)" } ?? "") }
        if s.phase == .failed { return "Failed" + (s.note.map { ": \($0)" } ?? "") }
        return s.note ?? "Ready"
    }

    private var expanded: some View {
        let s = model.state
        return VStack(alignment: .leading, spacing: 8) {
            // header: status, speaker toggle, connection. Drag to move; double-click to snap back.
            HStack(spacing: 8) {
                StatusDot(status: status, size: 10)
                Text(title)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(Palette.status(status))
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if model.listening { LevelMeter(level: model.level) }
                Button { model.speakReplies.toggle(); if !model.speakReplies, model.speaking { model.onStopSpeaking?() } } label: {
                    Image(systemName: model.speakReplies ? "speaker.wave.2.fill" : "speaker.slash")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(model.speakReplies ? Palette.accent : .white.opacity(0.45))
                        .frame(width: 24, height: 20)
                }
                .buttonStyle(.plain)
                .help(model.speakReplies ? "Spoken replies on for voice asks" : "Spoken replies off")
                Circle().fill(s.connected ? Palette.color(.done).opacity(0.7) : Palette.color(.failed).opacity(0.7))
                    .frame(width: 5, height: 5).help(s.connected ? "connected" : "offline")
            }
            .contentShape(Rectangle())
            .overlay(DragHandle(onClick: { n in if n >= 2 { onSnap() } }, onDragEnd: onDragEnd).allowsHitTesting(true)
                .padding(.trailing, 44))

            TextField(model.listening ? "Listening…" : "Ask Reach…", text: $model.draft)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .foregroundStyle(.white)
                .focused($focused)
                .onSubmit { model.submit() }
                .padding(.vertical, 8).padding(.horizontal, 10)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(.white.opacity(0.07)))

            if let line = model.voiceLine {
                Label(line, systemImage: model.listening ? "mic.fill" : "mic")
                    .font(.system(size: 11, weight: model.listening ? .semibold : .regular))
                    .foregroundStyle(model.listening ? Palette.accent : .white.opacity(0.6))
                    .lineLimit(2)
            }

            if let chip = s.handChip {
                HandChipView(chip: chip) { model.clearSelection() }
            }

            if model.selectionIsCircle || model.zoomTarget != nil || model.selectionNote != nil {
                HStack(spacing: 8) {
                    if let note = model.selectionNote {
                        Label(note, systemImage: "circle.dashed")
                            .font(.system(size: 11)).foregroundStyle(Palette.accent.opacity(0.95)).lineLimit(1)
                    }
                    Spacer(minLength: 0)
                    if model.zoomTarget != nil {
                        Chip(icon: "plus.magnifyingglass", text: "Zoom", action: onZoom).help("Magnify the circled area")
                    }
                    if model.selectionIsCircle {
                        Button { model.clearSelection() } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.plain).foregroundStyle(.white.opacity(0.5)).help("Drop the circle")
                    }
                }
            }

            if !s.answer.isEmpty || !s.evidence.isEmpty {
                // the answer, then its evidence: measured, never clipped, scrolls past the budget
                ScrollView(.vertical) {
                    VStack(alignment: .leading, spacing: 8) {
                        if !s.answer.isEmpty {
                            Text(s.answer)
                                .font(.system(size: 13.5))
                                .lineSpacing(2)
                                .foregroundStyle(.white.opacity(0.94))
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        ForEach(s.evidence, id: \.stableID) { card in EvidenceRow(card: card) }
                    }
                    .padding(.trailing, 4)
                    .background(GeometryReader { g in Color.clear.preference(key: ContentHeight.self, value: g.size.height) })
                }
                .frame(height: min(max(contentHeight, 20), model.answerMax))
                .onPreferenceChange(ContentHeight.self) { contentHeight = $0 }
            }

            // footer: hint (or the receipt), and the speak button for typed asks
            HStack(spacing: 8) {
                if let r = s.receipt {
                    Text(r.line)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.42)).lineLimit(1).truncationMode(.middle)
                } else {
                    Text(Self.hint).font(.system(size: 11)).foregroundStyle(.white.opacity(0.5)).lineLimit(1)
                }
                Spacer(minLength: 0)
                if !s.answer.isEmpty, s.phase != .working {
                    Chip(icon: model.speaking ? "stop.fill" : "play.fill", text: model.speaking ? "Stop" : "Speak") {
                        if model.speaking { model.onStopSpeaking?() } else { onSpeak() }
                    }
                }
            }
            if s.receipt != nil {
                Text(Self.hint).font(.system(size: 10)).foregroundStyle(.white.opacity(0.35)).lineLimit(1)
            }
        }
        .padding(16)
        .frame(width: 440)
        .background(ZStack { VisualEffect(); Color.black.opacity(0.42) })
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Palette.status(status).opacity(0.22)))
        .padding(4)
        .onAppear { focused = true }
    }
}

struct Chip: View {
    var icon: String
    var text: String
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            Label(text, systemImage: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.accent)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Capsule().fill(Palette.accent.opacity(0.14)))
                .overlay(Capsule().stroke(Palette.accent.opacity(0.35), lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }
}

struct EvidenceRow: View {
    var card: EvidenceCard
    var body: some View {
        Button { openSource(card) } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(card.quote.map { "“\($0)”" } ?? "(no quote)")
                    .font(.system(size: 12)).foregroundStyle(.white.opacity(0.85)).lineLimit(4)
                    .fixedSize(horizontal: false, vertical: true)
                Text([card.id, card.title].compactMap { $0 }.joined(separator: " · "))
                    .font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette.accent.opacity(0.8))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.white.opacity(0.06)))
        }
        .buttonStyle(.plain)
        .disabled(card.file == nil)
    }
}

/// Opens the source with `open`. Safari honours `#page=N` on a file URL; without Safari, opens the file plainly.
func openSource(_ card: EvidenceCard) {
    guard let file = card.file else { return }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
    var url = URL(fileURLWithPath: file).absoluteString
    if let page = card.page, FileManager.default.fileExists(atPath: "/Applications/Safari.app") {
        url += "#page=\(page)"
        p.arguments = ["-a", "Safari", url]
    } else {
        p.arguments = [file]
    }
    log("open \(file) page \(card.page.map(String.init) ?? "-")")
    try? p.run()
}

struct VisualEffect: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .hudWindow
        v.blendingMode = .behindWindow
        v.state = .active
        v.appearance = NSAppearance(named: .darkAqua)
        return v
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

/// Borderless, non-activating, floats above other windows, but can take typing.
final class ReachPanel: NSPanel {
    var onEscape: (() -> Void)?

    init(content: NSView) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 420, height: 120),
                   styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar   // above the ready-to-circle layer
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        isMovableByWindowBackground = false  // DragHandle moves it, so clicks in the field and buttons stay clicks
        hidesOnDeactivate = false
        contentView = content
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
    override func cancelOperation(_ sender: Any?) { onEscape?() }
}

/// "Selected by hand" (EXPERIMENTAL pinch selection): first line of the text, a thumbnail if captured, a clear button.
struct HandChipView: View {
    var chip: HandChip
    var onClear: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            if let path = chip.thumbnailPath, let img = NSImage(contentsOfFile: path) {
                Image(nsImage: img).resizable().aspectRatio(contentMode: .fill)
                    .frame(width: 30, height: 30).clipShape(RoundedRectangle(cornerRadius: 5))
            } else {
                Image(systemName: "hand.pinch").font(.system(size: 14)).foregroundStyle(Palette.accent)
                    .frame(width: 30, height: 30)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text("\(chip.title) · experimental").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Palette.accent)
                Text(chip.line).font(.system(size: 12)).foregroundStyle(.white.opacity(0.9)).lineLimit(1)
            }
            Spacer(minLength: 0)
            Button(action: onClear) { Image(systemName: "xmark.circle.fill") }
                .buttonStyle(.plain).foregroundStyle(.white.opacity(0.55)).help("Drop this selection")
        }
        .padding(6)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.accent.opacity(0.10)))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.accent.opacity(0.35), lineWidth: 1))
    }
}

/// Moves the panel with the user's own drag (no synthetic events). A press that barely moves is a click; its
/// `clickCount` tells a double-click (snap to top) from a single one.
struct DragHandle: NSViewRepresentable {
    var onClick: (Int) -> Void = { _ in }
    var onDragEnd: () -> Void = {}
    var onHover: ((Bool) -> Void)?

    func makeNSView(context: Context) -> HandleView { update(HandleView()) }
    func updateNSView(_ v: HandleView, context: Context) { _ = update(v) }
    private func update(_ v: HandleView) -> HandleView {
        v.onClick = onClick; v.onDragEnd = onDragEnd; v.onHover = onHover
        return v
    }

    final class HandleView: NSView {
        var onClick: (Int) -> Void = { _ in }
        var onDragEnd: () -> Void = {}
        var onHover: ((Bool) -> Void)?
        private var start: (mouse: NSPoint, origin: NSPoint)?
        private var moved = false

        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                           owner: self))
        }
        override func mouseEntered(with event: NSEvent) { onHover?(true) }
        override func mouseExited(with event: NSEvent) { onHover?(false) }
        override func mouseDown(with event: NSEvent) {
            start = (NSEvent.mouseLocation, window?.frame.origin ?? .zero)
            moved = false
        }
        override func mouseDragged(with event: NSEvent) {
            guard let start, let window else { return }
            let now = NSEvent.mouseLocation
            let dx = now.x - start.mouse.x, dy = now.y - start.mouse.y
            if !moved, hypot(dx, dy) < 3 { return }
            moved = true
            window.setFrameOrigin(NSPoint(x: start.origin.x + dx, y: start.origin.y + dy))
        }
        override func mouseUp(with event: NSEvent) {
            defer { start = nil }
            if moved { onDragEnd() } else { onClick(event.clickCount) }
        }
    }
}
