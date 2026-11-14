import Carbon.HIToolbox
import Foundation

/// System-wide hotkeys through Carbon's RegisterEventHotKey, which needs no Accessibility permission.
final class Hotkeys {
    static let shared = Hotkeys()
    private var actions: [UInt32: () -> Void] = [:]
    private var refs: [UInt32: EventHotKeyRef] = [:]
    private var nextID: UInt32 = 1
    private var installed = false

    @discardableResult
    func register(keyCode: Int, modifiers: Int, name: String, _ action: @escaping () -> Void) -> UInt32? {
        installHandler()
        let id = nextID
        nextID += 1
        var ref: EventHotKeyRef?
        let hkID = EventHotKeyID(signature: OSType(0x5245_4143), id: id) // 'REAC'
        let status = RegisterEventHotKey(UInt32(keyCode), UInt32(modifiers), hkID, GetEventDispatcherTarget(), 0, &ref)
        guard status == noErr, let ref else {
            log("hotkey \(name) unavailable (status \(status)); another app may own it")
            return nil
        }
        actions[id] = action
        refs[id] = ref
        log("hotkey \(name) registered")
        return id
    }

    func unregister(_ id: UInt32) {
        if let ref = refs.removeValue(forKey: id) { UnregisterEventHotKey(ref) }
        actions[id] = nil
    }

    fileprivate func fire(_ id: UInt32) {
        if let a = actions[id] { DispatchQueue.main.async(execute: a) }
    }

    private func installHandler() {
        guard !installed else { return }
        installed = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetEventDispatcherTarget(), { _, event, _ -> OSStatus in
            var hk = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            Hotkeys.shared.fire(hk.id)
            return noErr
        }, 1, &spec, nil, nil)
    }
}

enum Keys {
    static let space = kVK_Space
    static let escape = kVK_Escape
    static let option = optionKey
    static let shift = shiftKey
}
