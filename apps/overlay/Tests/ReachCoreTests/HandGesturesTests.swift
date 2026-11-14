import Foundation
import Testing
@testable import ReachCore

@Suite struct HandGesturesTests {
    @Test func handsCommandMatchesWholeUtteranceOnly() {
        #expect(HandsCommand.parse("hands on") == .on)
        #expect(HandsCommand.parse("Hands ON.") == .on)
        #expect(HandsCommand.parse("  hands mode on! ") == .on)
        #expect(HandsCommand.parse("hands off") == .off)
        #expect(HandsCommand.parse("Hands-mode off") == .off)
        #expect(HandsCommand.parse("hands") == .toggle)
        #expect(HandsCommand.parse("Hands?") == .toggle)
        #expect(HandsCommand.parse("hands mode") == .toggle)
        #expect(HandsCommand.parse("hands on the table") == nil)
        #expect(HandsCommand.parse("put your hands up") == nil)
        #expect(HandsCommand.parse("handsome") == nil)
        #expect(HandsCommand.parse("") == nil)
    }

    @Test func recordAndForgetEncode() throws {
        let r = try ClientMessage.handsRecord(name: "top", action: GestureAction(kind: .top)).json()
        #expect(r == #"{"action":{"kind":"top","value":""},"name":"top","type":"hands_record"}"#)
        let k = try ClientMessage.handsRecord(name: "tab", action: GestureAction(kind: .keys, value: "cmd+shift+t")).json()
        #expect(k == #"{"action":{"kind":"keys","value":"cmd+shift+t"},"name":"tab","type":"hands_record"}"#)
        let f = try ClientMessage.handsForget(name: "top").json()
        #expect(f == #"{"name":"top","type":"hands_forget"}"#)
    }

    @Test func decodesHandsStateAndGesture() throws {
        #expect(try ServerMessage.decode(#"{"type":"hands_state","on":true,"pid":12}"#) == .handsState(on: true, pid: 12, reason: nil))
        #expect(try ServerMessage.decode(#"{"type":"hands_state","on":false,"pid":null}"#) == .handsState(on: false, pid: nil, reason: nil))
        let g = try ServerMessage.decode(#"{"type":"hands_gesture","name":"wave","action":{"kind":"command","value":"scroll_down"}}"#)
        #expect(g == .handsGesture(name: "wave", action: GestureAction(kind: .command, value: "scroll_down")))
        let a = try ServerMessage.decode(#"{"type":"hands_gesture","name":"q","action":{"kind":"ask","value":"summarise this"}}"#)
        #expect(a == .handsGesture(name: "q", action: GestureAction(kind: .ask, value: "summarise this")))
        #expect(try ServerMessage.decode(#"{"type":"hands_gesture","name":"x","action":{"kind":"bogus"}}"#)
                == .unknown(type: "hands_gesture"))
    }

    @Test func loadsGesturesFile() {
        let json = #"{"gestures":[{"name":"top","action":{"kind":"top","value":""},"hands":1,"examples":[[1,2]]},"#
            + #"{"name":"both","action":{"kind":"keys","value":"cmd+t"},"hands":2}]}"#
        let gs = HandGestures.load(Data(json.utf8))
        #expect(gs.map(\.name) == ["top", "both"])
        #expect(gs[0].line == "top · go to top · 1 hand")
        #expect(gs[1].line == "both · press cmd+t · 2 hands")
        #expect(HandGestures.load(nil).isEmpty)
        #expect(HandGestures.load(Data("nope".utf8)).isEmpty)
    }

    @Test func recordFormValidation() {
        #expect(HandGestures.canRecord(name: "top", action: GestureAction(kind: .top)))
        #expect(!HandGestures.canRecord(name: "  ", action: GestureAction(kind: .top)))
        #expect(!HandGestures.canRecord(name: String(repeating: "a", count: 41), action: GestureAction(kind: .bottom)))
        #expect(HandGestures.canRecord(name: String(repeating: "a", count: 40), action: GestureAction(kind: .bottom)))
        #expect(!HandGestures.canRecord(name: "k", action: GestureAction(kind: .keys, value: " ")))
        #expect(HandGestures.canRecord(name: "k", action: GestureAction(kind: .ask, value: "what is this")))
        #expect(!HandGestures.commands.contains("uncertain"))
        #expect(HandGestures.commands.contains("scroll_down"))
    }

    @Test func gestureCommandRoutesLikeVoice() {
        #expect(HandGestures.decision(command: "stop") == .localStop(chip: "stop"))
        #expect(HandGestures.decision(command: "scroll_down") == .ask("scroll down", chip: "scroll down"))
    }
}
