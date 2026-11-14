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
    var onAction: (PlainState.Action) -> Void = { _ in }

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

    static func handsColor(_ t: HandsMode.Tint) -> Color {
        switch t {
        case .grey: return .white.opacity(0.55)
        case .blue: return Color(red: 0.35, green: 0.62, blue: 1.0)
        case .green: return Color(red: 0.3, green: 0.85, blue: 0.5)
        case .amber: return Color(red: 1.0, green: 0.72, blue: 0.25)
        }
    }

    /// The idle island: a compact dark glass capsule that breathes softly. Drag it; double-click snaps it back.
    private var pill: some View {
        HStack(spacing: 8) {
            HStack(spacing: 8) {
                pillLead
                if model.listening {
                    LevelMeter(level: model.level)
                } else if model.state.phase == .working {
                    Text(ProgressLine.of(tool: model.state.tool)).font(.system(size: 12, weight: .medium, design: .rounded))
                        .foregroundStyle(.white.opacity(0.85)).lineLimit(1)
                        .contentTransition(.opacity).animation(.easeInOut(duration: 0.2), value: model.state.tool)
                } else if let c = model.compact {
                    Text(c.line).font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.92)).lineLimit(1)
                } else {
                    Text("Reach").font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.9))
                }
            if model.handsFree {
                // hands-free: a tiny pulsing dot with an ear glyph; dims for a moment when speech was ignored
                HStack(spacing: 3) {
                    Circle().fill(Palette.accent).frame(width: 5, height: 5)
                        .opacity(breathe ? 1 : 0.45)
                    Image(systemName: "ear").font(.system(size: 9, weight: .semibold)).foregroundStyle(.white.opacity(0.75))
                }
                .opacity(model.handsFreeFlicker ? 0.3 : 1)
                .animation(.easeInOut(duration: 0.2), value: model.handsFreeFlicker)
                .help(HandsFree.hint)
            }
            if model.handsOn || model.handsLine != nil {
                // hands mode: a small hand with a calm pulse and "Hands on" (or a short hint / "Hands off")
                let look = HandsMode.look(phase: model.handsPhase)
                HStack(spacing: 4) {
                    Image(systemName: look.tint == .green ? "hand.pinch.fill" : "hand.raised.fill")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(model.handsOn ? Self.handsColor(look.tint) : .white.opacity(0.6))
                        .opacity(model.handsOn && !breathe ? (look.pulse ? 0.35 : 0.7) : 1)
                    Text(model.handsLine ?? (model.handsPhase == nil ? HandsMode.onLabel : look.label))
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.white.opacity(0.75)).lineLimit(1)
                }
                .help(HandsMode.hint)
            }
            if let chip = model.handsFreeChip, model.handsFree {
                Text(chip).font(.system(size: 10, weight: .medium)).foregroundStyle(.white.opacity(0.7))
                    .padding(.horizontal, 6).padding(.vertical, 1).background(Capsule().fill(.white.opacity(0.1)))
            }
            }
            // click (or hover) opens the full answer with Details; double-click snaps back; drag moves
            .overlay(DragHandle(onClick: { n in if n >= 2 { onSnap() } else { model.expandRequested?() } },
                                onDragEnd: onDragEnd))
            if model.state.phase == .working {
                // the tiny stop control exists only while a run is working
                Button { model.abort() } label: {
                    Image(systemName: "stop.fill").font(.system(size: 8, weight: .bold)).foregroundStyle(.white.opacity(0.7))
                        .frame(width: 16, height: 16).background(Circle().fill(.white.opacity(0.12)))
                }
                .buttonStyle(.plain).help("Stop (Esc)")
            } else if let c = model.compact, c.retry {
                Button { model.compact = nil; model.retryLast() } label: {
                    Label("Retry", systemImage: "arrow.clockwise").font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.85))
                        .padding(.horizontal, 7).padding(.vertical, 2).background(Capsule().fill(.white.opacity(0.12)))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
        .background(Capsule().fill(.black.opacity(0.9)))
        .overlay(Capsule().stroke(pillStroke.opacity(breathe && status == .idle ? 0.28 : 0.10), lineWidth: 1))
        .scaleEffect(breathe && status == .idle && model.compact == nil ? 1.02 : 1)
        .padding(4)   // room for the breath, so nothing is clipped by the window
        .onAppear { withAnimation(.easeInOut(duration: 2.6).repeatForever(autoreverses: true)) { breathe = true } }
        .animation(.easeInOut(duration: 0.2), value: model.compact)
        .help(model.compact != nil ? "Click for the full answer and details" : Self.hint)
    }

    private var pillStroke: Color {
        guard let c = model.compact else { return Palette.status(status) }
        switch c.kind {
        case .done: return Palette.status(.done)
        case .needsYou: return Palette.status(.needsYou)
        case .failed: return Palette.status(.failed)
        }
    }

    /// The pill's leading mark: a small spinner while working, the result glyph after a tool run, else the status dot.
    @ViewBuilder private var pillLead: some View {
        if model.state.phase == .working, !model.listening {
            ProgressView().controlSize(.mini).scaleEffect(0.8).frame(width: 12, height: 12).tint(.white)
        } else if let c = model.compact {
            let (icon, color): (String, Color) = {
                switch c.kind {
                case .done: return ("checkmark", Palette.status(.done))
                case .needsYou: return ("exclamationmark", Palette.status(.needsYou))
                case .failed: return ("xmark", Palette.status(.failed))
                }
            }()
            Image(systemName: icon).font(.system(size: 10, weight: .heavy)).foregroundStyle(color).frame(width: 12, height: 12)
        } else {
            StatusDot(status: status)
        }
    }

    /// One plain sentence and at most one action for an end state that is not Done.
    private var plain: PlainState? {
        let s = model.state
        if let v = s.verdict { return PlainState.of(verdict: v) }
        if s.phase == .needsYou { return PlainState.of(note: s.note ?? "") }
        if s.phase == .failed { return PlainState(sentence: "That didn't work.", action: .retry) }
        return nil
    }

    private var title: String {
        let s = model.state
        if model.listening { return "Listening" }
        if s.phase == .working { return ProgressLine.of(tool: s.tool) }
        if let p = plain { return p.sentence }
        if s.phase == .done { return "Done" }
        return "Reach"
    }

    /// The text field shows when typing makes sense: nothing on screen yet, the panel was summoned to type, or a
    /// draft or circle chip is waiting.
    private var showsField: Bool {
        let s = model.state
        return PanelContent(listening: model.listening, hasAnswer: !s.answer.isEmpty, hasPlain: plain != nil,
                            hasEvidence: !s.evidence.isEmpty, hasHandChip: s.handChip != nil,
                            circleAttached: model.selectionIsCircle || model.zoomTarget != nil, typing: model.typing,
                            hasDraft: !model.draft.isEmpty).showsField
    }

    private var expanded: some View {
        let s = model.state
        return VStack(alignment: .leading, spacing: 8) {
            // header: status dot, one line, speaker toggle. Drag to move; double-click to snap back.
            HStack(spacing: 8) {
                StatusDot(status: status, size: 10)
                Text(title)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(status == .idle || status == .done ? .white.opacity(0.92) : Palette.status(status))
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    .contentTransition(.opacity)
                Spacer(minLength: 8)
                if model.listening {
                    LevelMeter(level: model.level)
                } else {
                    Button { model.speakReplies.toggle(); if !model.speakReplies, model.speaking { model.onStopSpeaking?() } } label: {
                        Image(systemName: model.speakReplies ? "speaker.wave.2.fill" : "speaker.slash")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(model.speakReplies ? Palette.accent.opacity(0.9) : .white.opacity(0.4))
                            .frame(width: 24, height: 20)
                    }
                    .buttonStyle(.plain)
                    .help(model.speakReplies ? "Replies to voice asks are spoken" : "Spoken replies off")
                }
            }
            .contentShape(Rectangle())
            .overlay(DragHandle(onClick: { n in if n >= 2 { onSnap() } }, onDragEnd: onDragEnd).padding(.trailing, 32))
            .animation(.easeInOut(duration: 0.2), value: title)

            if model.listening {
                // listening: only the live words
                if !model.draft.isEmpty {
                    Text(model.draft).font(.system(size: 14)).foregroundStyle(.white.opacity(0.85))
                        .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                }
            } else {
                if let p = plain, let label = p.actionLabel {
                    if p.action == .circle {
                        Text(label).font(.system(size: 12)).foregroundStyle(.white.opacity(0.6))
                    } else {
                        Chip(icon: p.action == .retry || p.action == .startServer || p.action == .relaunch ? "arrow.clockwise" : "gearshape",
                             text: label) { onAction(p.action) }
                    }
                }
                if let d = plain?.detail {
                    Text(d).font(.system(size: 11)).foregroundStyle(.white.opacity(0.55))
                        .fixedSize(horizontal: false, vertical: true)
                }

                if model.handsOn, let g = s.gesture {  // what Hands mode is doing (EXPERIMENTAL)
                    Text("✋ \(g.line)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(Palette.accent.opacity(0.9))
                        .lineLimit(1)
                }

                if showsField {
                    TextField("Ask Reach…", text: $model.draft)
                        .textFieldStyle(.plain)
                        .font(.system(size: 14))
                        .foregroundStyle(.white)
                        .focused($focused)
                        .onSubmit { model.submit() }
                        .padding(.vertical, 8).padding(.horizontal, 10)
                        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(.white.opacity(0.07)))
                }

                if let chip = s.handChip {
                    HandChipView(chip: chip) { model.clearSelection() }
                }

                if model.selectionIsCircle || model.zoomTarget != nil {
                    HStack(spacing: 8) {
                        Label(model.selectionIsCircle ? "Circle attached" : "Circled", systemImage: "circle.dashed")
                            .font(.system(size: 11)).foregroundStyle(Palette.accent.opacity(0.95)).lineLimit(1)
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
                    // the answer (plain text), then at most two evidence cards: measured, never clipped, scrolls past the budget
                    ScrollView(.vertical) {
                        VStack(alignment: .leading, spacing: 8) {
                            if !s.answer.isEmpty {
                                Text(PlainText.display(s.answer))
                                    .font(.system(size: 13.5))
                                    .lineSpacing(2)
                                    .foregroundStyle(.white.opacity(0.94))
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            ForEach(s.evidence.prefix(2), id: \.stableID) { card in EvidenceRow(card: card) }
                        }
                        .padding(.trailing, 4)
                        .background(GeometryReader { g in Color.clear.preference(key: ContentHeight.self, value: g.size.height) })
                    }
                    .frame(height: min(max(contentHeight, 20), model.answerMax))
                    .onPreferenceChange(ContentHeight.self) { h in
                        contentHeight = h
                        if abs(model.measuredAnswer - h) > 0.5 { model.measuredAnswer = h }
                    }
                }

                // footer: details disclosure (receipt, decisions) and Speak; the hint only when nothing else shows
                if s.receipt != nil || (!s.answer.isEmpty && s.phase != .working) {
                    HStack(spacing: 8) {
                        if s.receipt != nil {
                            Button { model.showDetails.toggle() } label: {
                                Label("Details", systemImage: model.showDetails ? "chevron.down" : "chevron.right")
                                    .font(.system(size: 10, weight: .medium)).foregroundStyle(.white.opacity(0.4))
                            }
                            .buttonStyle(.plain)
                        }
                        Spacer(minLength: 0)
                        if !s.answer.isEmpty, s.phase != .working, !model.speaking {
                            // no Stop button once a run has ended; the speaker toggle above or Esc silences speech
                            Chip(icon: "play.fill", text: "Speak") { onSpeak() }
                        }
                    }
                    if model.showDetails, let r = s.receipt {
                        // one friendly line; the raw receipt only behind the menu's "Show technical details"
                        VStack(alignment: .leading, spacing: 4) {
                            Text(ReceiptSummary.line(r))
                                .font(.system(size: 11)).foregroundStyle(.white.opacity(0.6))
                            if model.technicalDetails {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(r.line)
                                    if let tools = r.tools, !tools.isEmpty { Text("tools: " + tools.joined(separator: ", ")) }
                                    if let c = r.est_cost_usd { Text(String(format: "est. cost $%.4f", c)) }
                                    ForEach(Array(s.decisions.enumerated()), id: \.offset) { _, d in Text("drex \(d.line)") }
                                }
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(.white.opacity(0.42))
                            }
                        }
                        .lineLimit(1).truncationMode(.tail)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                        .transition(.opacity)
                    }
                } else if showsField {
                    Text(Self.hint).font(.system(size: 11)).foregroundStyle(.white.opacity(0.5)).lineLimit(1)
                }
            }
        }
        .padding(16)
        .frame(width: 440)
        .background(ZStack { VisualEffect(); Color.black.opacity(0.42) })
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Palette.status(status).opacity(0.22)))
        .padding(4)
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: model.listening)
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: model.showDetails)
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
