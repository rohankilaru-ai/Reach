// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "ReachOverlay",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Reach", targets: ["Reach"]),
    ],
    targets: [
        .target(name: "ReachCore"),
        .executableTarget(name: "Reach", dependencies: ["ReachCore"]),
        .testTarget(name: "ReachCoreTests", dependencies: ["ReachCore"]),
    ],
    swiftLanguageModes: [.v5]
)
