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
