// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "PiStatusBar",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "PiStatusBar", targets: ["PiStatusBar"])
    ],
    targets: [
        .executableTarget(
            name: "PiStatusBar",
            path: "Sources/PiStatusBar"
        )
    ]
)
