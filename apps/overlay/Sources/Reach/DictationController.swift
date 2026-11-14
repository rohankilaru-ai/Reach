import AppKit
import ApplicationServices
import Carbon.HIToolbox
import ReachCore
import Speech
import SwiftUI

/// What the pill shows for dictation (kept apart from the shared model so the pill needs one line).
@MainActor
final class DictationStatus: ObservableObject {
    static let shared = DictationStatus()
    @Published var on = false
    @Published var note: String?
    @Published var flash: String?
}

/// The pill's dictation indicator: a mic glyph with a red-orange pulse and "Dictating", plus a short note.
struct DictationBadge: View {
    @ObservedObject var status = DictationStatus.shared
    @State private var pulse = false

    var body: some View {
        if status.on {
            HStack(spacing: 4) {
                ZStack {
                    Circle().fill(Color(red: 1.0, green: 0.36, blue: 0.2)).frame(width: 14, height: 14)
                        .opacity(pulse ? 0.15 : 0.55).scaleEffect(pulse ? 1.35 : 0.9)
                    Image(systemName: "mic.fill").font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Color(red: 1.0, green: 0.45, blue: 0.25))
                }
                Text(status.note ?? Dictation.label).font(.system(size: 10, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.85)).lineLimit(1)
            }
            .help(Dictation.hint)
            .onAppear { withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) { pulse = true } }
            .onDisappear { pulse = false }
        } else if let f = status.flash {
            Text(f).font(.system(size: 10, weight: .medium)).foregroundStyle(.white.opacity(0.7))
        }
    }
}

/// Real Accessibility reads and the AX insert (read only except for setting the selected text).
final class SystemFocus: DictationFocus {
    var trusted: Bool { AXIsProcessTrusted() }

    private func element() -> AXUIElement? {
        let sys = AXUIElementCreateSystemWide()
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(sys, kAXFocusedUIElementAttribute as CFString, &v) == .success, let v,
              CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return (v as! AXUIElement)
    }

    private func string(_ e: AXUIElement, _ a: String) -> String? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(e, a as CFString, &v) == .success else { return nil }
        return v as? String
    }

    private func has(_ e: AXUIElement, _ a: String) -> Bool {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(e, a as CFString, &v) == .success && v != nil
    }

    private func range(_ e: AXUIElement) -> CFRange? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(e, kAXSelectedTextRangeAttribute as CFString, &v) == .success, let v,
              CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var r = CFRange()
        return AXValueGetValue(v as! AXValue, .cfRange, &r) ? r : nil
    }

    func focused() -> FocusInfo? {
        guard let e = element() else { return nil }
        var pid: pid_t = 0
        AXUIElementGetPid(e, &pid)
        let app = NSRunningApplication(processIdentifier: pid)
        var settable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(e, kAXValueAttribute as CFString, &settable)
        var selSettable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(e, kAXSelectedTextAttribute as CFString, &selSettable)
        return FocusInfo(role: string(e, kAXRoleAttribute), subrole: string(e, kAXSubroleAttribute),
                         bundleID: app?.bundleIdentifier, pid: pid,
                         hasValue: has(e, kAXValueAttribute), hasSelectedRange: range(e) != nil,
                         editable: settable.boolValue || selSettable.boolValue,
                         isReach: pid == ProcessInfo.processInfo.processIdentifier)
    }

    func textBeforeCaret() -> String? {
        guard let e = element(), let value = string(e, kAXValueAttribute), let r = range(e) else { return nil }
        let ns = value as NSString
        guard r.location >= 0, r.location <= ns.length else { return nil }
        let start = max(0, r.location - 4)
        return ns.substring(with: NSRange(location: start, length: r.location - start))
    }

    func insertAX(_ text: String) -> Bool {
        guard let e = element(), let before = range(e) else { return false }
        guard AXUIElementSetAttributeValue(e, kAXSelectedTextAttribute as CFString, text as CFString) == .success else {
            return false
        }
        // Some apps report success and do nothing: believe it only when the caret moved.
        guard let after = range(e) else { return true }
        return after.location != before.location || after.length != before.length
    }

    func deleteBeforeCaretAX(_ count: Int) -> Bool {
        guard let e = element(), let r = range(e), r.length == 0, r.location >= count else { return false }
        var sel = CFRange(location: r.location - count, length: count)
        guard let v = AXValueCreate(.cfRange, &sel),
              AXUIElementSetAttributeValue(e, kAXSelectedTextRangeAttribute as CFString, v) == .success else { return false }
        return AXUIElementSetAttributeValue(e, kAXSelectedTextAttribute as CFString, "" as CFString) == .success
    }
}

/// The general pasteboard: every item and type saved, restored only if nobody wrote to it since.
final class SystemPasteboard: DictationPasteboard {
    func save() -> [[(NSPasteboard.PasteboardType, Data)]] {
        (NSPasteboard.general.pasteboardItems ?? []).map { item in
            item.types.compactMap { t in item.data(forType: t).map { (t, $0) } }
        }
    }

    func put(_ text: String) -> Int {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        return pb.changeCount
    }

    func restore(_ saved: [[(NSPasteboard.PasteboardType, Data)]], ifChangeCount c: Int) {
        let pb = NSPasteboard.general
        guard pb.changeCount == c else { return }
        pb.clearContents()
        let items = saved.map { pairs -> NSPasteboardItem in
            let it = NSPasteboardItem()
            for (t, d) in pairs { it.setData(d, forType: t) }
            return it
        }
        if !items.isEmpty { pb.writeObjects(items) }
    }
}

/// The only synthetic keys dictation posts.
final class SystemKeys: DictationKeys {
    private func tap(_ key: Int, flags: CGEventFlags = []) {
        let src = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let ev = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(key), keyDown: down)
            ev?.flags = flags
            ev?.post(tap: .cghidEventTap)
        }
    }
    func commandV() { tap(kVK_ANSI_V, flags: .maskCommand) }
    func commandZ() { tap(kVK_ANSI_Z, flags: .maskCommand) }
    func delete(_ times: Int) { for _ in 0..<InsertLedger.keyCount(times) { tap(kVK_Delete) } }
}

/// Dictation mode: an on-device recognizer whose finalized segments are typed into the focused text box.
/// No audio is saved and nothing is sent to a model or the backend.
@MainActor
final class DictationController {
    private let model: OverlayModel
    private let speech = SpeechInput()
    private let status = DictationStatus.shared
    private var toggle = DictationToggle()
    private var cutter = DictationCutter()
    private var running = false
    private var startedAt = Date.distantPast
    private var segmentPID: Int32?
    private var timer: Timer?
    private var escID: UInt32?
    private let focus = SystemFocus()
    private lazy var inserter = DictationInserter(focus: focus, pasteboard: SystemPasteboard(), keys: SystemKeys(),
                                                  later: { d, run in DispatchQueue.main.asyncAfter(deadline: .now() + d, execute: run) })
    var onChange: () -> Void = {}

    private var now: Double { ProcessInfo.processInfo.systemUptime }

    init(model: OverlayModel) { self.model = model }

    var isOn: Bool { toggle.on }

    /// Registers Option-Shift-D (tap toggles). Call once at launch.
    func install() {
        Hotkeys.shared.register(keyCode: kVK_ANSI_D, modifiers: Keys.option | Keys.shift, name: "Option-Shift-D (dictation)") {
            [weak self] in self?.flip()
        }
    }

    func flip() { setOn(!toggle.on) }

    func setOn(_ on: Bool, why: String = "toggle") {
        guard on != toggle.on else { return }
        if on {
            guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
                flash("Dictation needs Speech Recognition (Set up Reach…)"); return
            }
            guard speech.onDevice else { flash("Dictation needs on-device speech recognition"); return }
            if !focus.trusted {
                model.needsUser("allow Accessibility for Reach so dictation can type")
                NSWorkspace.shared.open(URL(string: PermissionKind.accessibility.settingsURL)!)
                return
            }
        }
        toggle.set(on, now: now)
        log("dictation \(on ? "on" : "off") (\(why))")
        status.on = on
        status.note = nil
        if on {
            status.flash = nil
            escID = Hotkeys.shared.register(keyCode: Keys.escape, modifiers: 0, name: "Esc (stop dictation)") {
                [weak self] in self?.setOn(false, why: "esc")
            }
            timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.tick() }
            }
            noteTarget()
        } else {
            if let id = escID { Hotkeys.shared.unregister(id); escID = nil }
            timer?.invalidate(); timer = nil
            stopRecognizer()
            flash(Dictation.offFlash)
        }
        onChange()
    }

    private func flash(_ s: String) {
        status.flash = s
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { [weak self] in
            if self?.status.flash == s { self?.status.flash = nil }
        }
    }

    /// Says in the pill when there is nowhere to type.
    private func noteTarget() {
        let d = DictationTarget.decide(focus.focused(), trusted: focus.trusted)
        status.note = d.line
    }

    private func tick() {
        if toggle.tick(now: now) == .timedOut { setOn(false, why: "60 s of silence"); return }
        if !running { startRecognizer() }
        if running, Date().timeIntervalSince(startedAt) > Dictation.restartEvery { stopRecognizer() }
        if let seg = cutter.tick(now: now) { segment(seg) }
        if Int(now * 5) % 5 == 0 { noteTarget() }   // about once a second
    }

    private func startRecognizer() {
        guard speech.onDevice else { setOn(false, why: "on-device recognition unavailable"); return }
        speech.recordTo = nil   // text only, never audio
        running = true
        startedAt = Date()
        cutter.reset()
        speech.start(onResult: { [weak self] heard in
            guard let self, self.running else { return }
            if self.cutter.current.isEmpty { self.segmentPID = self.focus.focused()?.pid }
            self.cutter.feed(heard.text, now: self.now)
            self.toggle.heard(now: self.now)
        }, onError: { [weak self] _ in self?.stopRecognizer() })   // the next tick restarts it
    }

    private func stopRecognizer() {
        guard running else { return }
        running = false
        speech.cancel()
        cutter.reset()
    }

    private func segment(_ text: String) {
        stopRecognizer()   // fresh task per segment, so earlier words are not revised again
        let pid = segmentPID
        segmentPID = nil
        switch DictationCommand.match(text) {
        case .stopDictating: setOn(false, why: "spoken"); return
        case .deleteThat: inserter.deleteThat(); return
        case .newLine: report(inserter.newLine(startedIn: pid)); return
        case nil: report(inserter.insert(text, startedIn: pid))
        }
    }

    private func report(_ o: InsertOutcome) {
        switch o {
        case .inserted(let path, let t): log("dictation: inserted \(t.count) chars via \(path)"); status.note = nil
        case .refused(let d):
            status.note = d.line
            if d == .noAccessibility { model.needsUser("allow Accessibility for Reach so dictation can type") }
        case .focusChanged: setOn(false, why: "focused app changed")
        case .empty: break
        }
    }
}
