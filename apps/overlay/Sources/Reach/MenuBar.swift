import AppKit
import ReachCore

/// The menu bar item: pill show/hide and mode, voice engine, spoken replies, the setup checklist, quit.
@MainActor
final class MenuBarItem: NSObject, NSMenuDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let menu = NSMenu()
    private let model: OverlayModel
    var pillShown: () -> Bool = { false }
    var onTogglePill: () -> Void = {}
    var onPillMode: (PillMode) -> Void = { _ in }
    var onSetup: () -> Void = {}
    var handsFreeOn: () -> Bool = { false }
    var onHandsFree: () -> Void = {}
    var onPauseHandsFree: () -> Void = {}

    private var listening = false
    private var badge = false

    /// Menu bar icon: an ear while hands-free is listening.
    func setListening(_ on: Bool) { listening = on; redraw() }

    /// A tiny dot on the icon while a run needs the user (until the next ask).
    func setBadge(_ on: Bool) { badge = on; redraw() }

    private func redraw() {
        let name = listening ? "ear" : "circle.dashed"
        let desc = (listening ? "Reach (listening)" : "Reach") + (badge ? ", needs you" : "")
        guard let base = NSImage(systemSymbolName: name, accessibilityDescription: desc) else { return }
        guard badge else { base.isTemplate = true; item.button?.image = base; return }
        let size = NSSize(width: max(base.size.width, 16) + 3, height: max(base.size.height, 16))
        let img = NSImage(size: size, flipped: false) { r in
            base.draw(in: NSRect(x: 0, y: (r.height - base.size.height) / 2, width: base.size.width, height: base.size.height))
            NSColor.black.setFill()
            NSBezierPath(ovalIn: NSRect(x: r.width - 6, y: r.height - 6, width: 5, height: 5)).fill()
            return true
        }
        img.isTemplate = true   // monochrome dot, follows the menu bar appearance
        img.accessibilityDescription = desc
        item.button?.image = img
    }

    init(model: OverlayModel) {
        self.model = model
        super.init()
        if let b = item.button {
            let img = NSImage(systemSymbolName: "circle.dashed", accessibilityDescription: "Reach")
            img?.isTemplate = true   // monochrome, follows the menu bar appearance
            b.image = img
        }
        menu.delegate = self
        item.menu = menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        add(pillShown() ? "Hide pill" : "Show pill") { [weak self] in self?.onTogglePill() }
        menu.addItem(.separator())
        let mode = PillMode.load()
        for m in PillMode.allCases {
            add("Pill: \(m.title)", on: m == mode) { [weak self] in self?.onPillMode(m) }
        }
        menu.addItem(.separator())
        add("Voice: Cloud", on: model.voiceMode == .cloud) { [weak self] in self?.model.voiceMode = .cloud }
        add("Voice: On-device", on: model.voiceMode == .onDevice) { [weak self] in self?.model.voiceMode = .onDevice }
        add("Speak replies", on: model.speakReplies) { [weak self] in
            guard let self else { return }
            self.model.speakReplies.toggle()
            if !self.model.speakReplies, self.model.speaking { self.model.onStopSpeaking?() }
        }
        add("Sounds", on: model.soundsOn) { [weak self] in self?.model.soundsOn.toggle() }
        menu.addItem(.separator())
        add("Hands-free listening (⌥⇧Space)", on: handsFreeOn()) { [weak self] in self?.onHandsFree() }
        if handsFreeOn() { add("Pause for 5 minutes") { [weak self] in self?.onPauseHandsFree() } }
        add("Hands mode", on: model.handsOn) { [weak self] in
            guard let self else { return }
            self.model.handsOn.toggle()
            self.model.send(.hands(on: self.model.handsOn))
        }
        menu.addItem(.separator())
        add("Set up Reach…") { [weak self] in self?.onSetup() }
        add("Quit Reach") { NSApp.terminate(nil) }
    }

    private func add(_ title: String, on: Bool? = nil, _ action: @escaping () -> Void) {
        let mi = NSMenuItem(title: title, action: #selector(MenuAction.fire), keyEquivalent: "")
        let target = MenuAction(action)
        mi.target = target
        mi.representedObject = target   // keeps the target alive with the item
        if let on { mi.state = on ? .on : .off }
        menu.addItem(mi)
    }
}

private final class MenuAction: NSObject {
    let action: () -> Void
    init(_ a: @escaping () -> Void) { action = a }
    @MainActor @objc func fire() { action() }
}
