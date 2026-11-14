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

/// A ring that breathes while Reach works and rests otherwise.
struct Halo: View {
    var phase: Phase
    @State private var pulse = false

    var body: some View {
        ZStack {
            Circle().stroke(Palette.color(phase).opacity(0.25), lineWidth: 6)
                .scaleEffect(pulse && phase == .working ? 1.35 : 1.0)
                .opacity(pulse && phase == .working ? 0 : 1)
            Circle().stroke(Palette.color(phase), lineWidth: 2.5)
            Circle().fill(Palette.color(phase).opacity(0.35)).padding(5)
        }
        .frame(width: 18, height: 18)
        .onAppear { withAnimation(.easeOut(duration: 1.4).repeatForever(autoreverses: false)) { pulse = true } }
    }
}

struct PanelView: View {
    @ObservedObject var model: OverlayModel
    @FocusState private var focused: Bool
    @State private var hovering = false
    var onSnap: () -> Void = {}
    var onDragEnd: () -> Void = {}

    var body: some View {
        Group {
            if model.expanded { expanded } else { pill }
        }
        .animation(.easeInOut(duration: 0.28), value: model.expanded)
    }

    /// The idle island: status dot + "Reach", the hotkey hint on hover. Drag it anywhere; double-click snaps it back.
    private var pill: some View {
        HStack(spacing: 8) {
            Circle().fill(Palette.color(model.state.phase)).frame(width: 8, height: 8)
            Text("Reach").font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.9))
            if hovering {
                Text("⌥Space").font(.system(size: 10, design: .monospaced)).foregroundStyle(.white.opacity(0.5))
                    .transition(.opacity)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 7)
        .background(Capsule().fill(.black.opacity(0.88)))
        .overlay(Capsule().stroke(.white.opacity(0.08)))
        .overlay(DragHandle(onClick: { n in if n >= 2 { onSnap() } else { model.expandRequested?() } },
                            onDragEnd: onDragEnd,
                            onHover: { h in withAnimation(.easeOut(duration: 0.15)) { hovering = h } }))
    }

    private var expanded: some View {
        let s = model.state
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Halo(phase: s.phase)
                Text(s.stateLine)
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .foregroundStyle(Palette.color(s.phase))
                    .lineLimit(1)
                Spacer()
                Text(s.connected ? "live" : "offline")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.35))
            }
            // drag the header to move; double-click snaps back to the notch
            .overlay(DragHandle(onClick: { n in if n >= 2 { onSnap() } }, onDragEnd: onDragEnd))
            .help("Drag to move · double-click to snap to the top")

            TextField(model.listening ? "Listening…" : "Ask Reach…", text: $model.draft)
                .textFieldStyle(.plain)
                .font(.system(size: 15))
                .foregroundStyle(.white)
                .focused($focused)
                .onSubmit { model.submit() }
                .padding(.vertical, 8).padding(.horizontal, 10)
                .background(RoundedRectangle(cornerRadius: 9).fill(.white.opacity(0.06)))

            if let line = model.voiceLine {
                Label(line, systemImage: model.listening ? "mic.fill" : "mic")
                    .font(.system(size: 11, weight: model.listening ? .semibold : .regular))
                    .foregroundStyle(model.listening ? Palette.accent : .white.opacity(0.6))
                    .lineLimit(2)
            }

            HStack(spacing: 8) {
                Text("Tap Option-Space to type, hold to talk, or circle something.")
                    .font(.system(size: 11)).foregroundStyle(.white.opacity(0.5)).lineLimit(1)
                Spacer(minLength: 0)
                Button { model.circleRequested?() } label: {
                    Image(systemName: "lasso").font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.accent)
                        .frame(width: 26, height: 22)
                        .background(RoundedRectangle(cornerRadius: 6).fill(Palette.accent.opacity(0.12)))
                }
                .buttonStyle(.plain)
                .help("Circle something on screen (Esc cancels)")
            }

            if let chip = s.handChip {
                HandChipView(chip: chip) { model.clearSelection() }
            }

            if let note = model.selectionNote {
                Label(note, systemImage: "viewfinder")
                    .font(.system(size: 11)).foregroundStyle(Palette.accent.opacity(0.9)).lineLimit(2)
            }

            if !s.answer.isEmpty {
                ScrollView {
                    Text(s.answer)
                        .font(.system(size: 13))
                        .foregroundStyle(.white.opacity(0.92))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
            }

            if !s.evidence.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(s.evidence, id: \.stableID) { card in
                        EvidenceRow(card: card)
                    }
                }
            }

            if let r = s.receipt {
                Text(r.line)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.5))
            }

            if !s.decisions.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(s.decisions.enumerated()), id: \.offset) { _, d in
                        Text("◦ drex \(d.line)")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(Palette.accent.opacity(0.6))
                            .lineLimit(1)
                    }
                }
            }
        }
        .padding(14)
        .frame(width: 420)
        .background(
            ZStack {
                VisualEffect()
                Color.black.opacity(0.35)
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(.white.opacity(0.08)))
        .onAppear { focused = true }
        .transition(.opacity.combined(with: .scale(scale: 0.96, anchor: .top)))
    }
}

struct EvidenceRow: View {
    var card: EvidenceCard
    var body: some View {
        Button { openSource(card) } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(card.quote.map { "“\($0)”" } ?? "(no quote)")
                    .font(.system(size: 12)).foregroundStyle(.white.opacity(0.85)).lineLimit(3)
                Text([card.id, card.title].compactMap { $0 }.joined(separator: " · "))
                    .font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette.accent.opacity(0.8))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 8).fill(.white.opacity(0.05)))
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
        level = .floating
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
