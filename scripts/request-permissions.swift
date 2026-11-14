// Asks macOS for every permission Reach can use, one at a time, for the app that runs this script (usually Terminal).
// Run:  swift scripts/request-permissions.swift
// It only raises the standard system prompts; you approve each in System Settings > Privacy & Security.
import AVFoundation
import ApplicationServices
import CoreGraphics
import Foundation
import Speech

func line(_ s: String) { print(s); fflush(stdout) }
func wait(_ seconds: Double, _ done: () -> Bool) { let end = Date().addingTimeInterval(seconds); while !done() && Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.2)) } }

line("1/6 Screen Recording")
if CGPreflightScreenCaptureAccess() { line("    already allowed") } else { _ = CGRequestScreenCaptureAccess(); line("    prompt raised: allow it in System Settings") }

line("2/6 Accessibility (lets Do mode press buttons and fill fields)")
let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
line(AXIsProcessTrustedWithOptions(opts) ? "    already allowed" : "    prompt raised: allow it in System Settings")

var micDone = false
line("3/6 Microphone")
switch AVCaptureDevice.authorizationStatus(for: .audio) {
case .authorized: line("    already allowed")
default: AVCaptureDevice.requestAccess(for: .audio) { ok in line(ok ? "    allowed" : "    denied"); micDone = true }; wait(45) { micDone }
}

var camDone = false
line("4/6 Camera (pinch and lip reading)")
switch AVCaptureDevice.authorizationStatus(for: .video) {
case .authorized: line("    already allowed")
default: AVCaptureDevice.requestAccess(for: .video) { ok in line(ok ? "    allowed" : "    denied"); camDone = true }; wait(45) { camDone }
}

var spDone = false
line("5/6 Speech Recognition")
if SFSpeechRecognizer.authorizationStatus() == .authorized { line("    already allowed") } else {
    SFSpeechRecognizer.requestAuthorization { s in line(s == .authorized ? "    allowed" : "    denied"); spDone = true }; wait(45) { spDone }
}

line("6/6 Automation (control Chrome, Finder, Notes, TextEdit)")
for app in ["Google Chrome", "Finder", "Notes", "TextEdit"] {
    let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-e", "tell application \"\(app)\" to get name"]
    p.standardOutput = Pipe(); p.standardError = Pipe()
    try? p.run(); p.waitUntilExit()
    line("    \(app): \(p.terminationStatus == 0 ? "allowed" : "needs your OK (a prompt appears) or the app is not installed")")
}
line("Done. Re-run this script to see what is still missing.")
