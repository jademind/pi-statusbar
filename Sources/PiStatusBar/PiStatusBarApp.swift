import AppKit
import SwiftUI

@main
struct PiStatusBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var monitor = AgentMonitor()

    var body: some Scene {
        MenuBarExtra {
            ContentView(monitor: monitor)
                .frame(width: 380)
        } label: {
            StatusBarIcon(summary: monitor.summary)
        }
        .menuBarExtraStyle(.window)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}

struct ContentView: View {
    @ObservedObject var monitor: AgentMonitor

    private func agentPrimaryLine(_ agent: AgentState) -> String {
        if let mux = agent.mux, let session = agent.muxSession {
            return "\(mux): \(session) · \(agent.activity.label)"
        }
        return agent.activity.label
    }

    private func windowStatusText(_ agent: AgentState) -> String {
        agent.hasAttachedClient ? "window attached" : "no attached window"
    }

    private func contextStatusText(_ agent: AgentState) -> String? {
        guard let percent = agent.contextPercent else { return nil }
        let rounded = Int(percent.rounded())

        if agent.contextNearLimit == true {
            return "Context \(rounded)% · near limit"
        }
        if agent.contextCloseToLimit == true {
            return "Context \(rounded)% · close to limit"
        }
        return "Context \(rounded)%"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(monitor.summary.uiColor)
                    .frame(width: 10, height: 10)
                Text(monitor.summary.label)
                    .font(.headline)
                Spacer()
                Text(monitor.daemonOnline ? "daemon: online" : "daemon: offline")
                    .font(.caption2)
                    .foregroundStyle(monitor.daemonOnline ? .green : .red)
            }

            if monitor.agents.isEmpty {
                Text("No active Pi agents detected")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(monitor.agents) { agent in
                    Button {
                        monitor.jump(to: agent)
                    } label: {
                        HStack(alignment: .top, spacing: 8) {
                            Circle()
                                .fill(agent.activity.color)
                                .frame(width: 8, height: 8)
                                .padding(.top, 4)

                            VStack(alignment: .leading, spacing: 2) {
                                Text(agentPrimaryLine(agent))
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(.primary)
                                    .lineLimit(1)

                                Text(agent.cwd ?? "(no cwd)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)

                                Text("PID \(agent.pid) · \(windowStatusText(agent))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)

                                if let context = contextStatusText(agent) {
                                    Text(context)
                                        .font(.caption2)
                                        .foregroundStyle((agent.contextNearLimit == true || agent.contextCloseToLimit == true) ? .orange : .secondary)
                                        .lineLimit(1)
                                }
                            }

                            Spacer()
                            Text("Jump")
                                .font(.caption)
                                .foregroundStyle(.blue)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }

            if let msg = monitor.lastMessage {
                Text(msg)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Divider()

            HStack {
                Text("Refresh: 2s")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Refresh now") {
                    monitor.refresh()
                }
                .buttonStyle(.borderless)

                Button("Quit") {
                    NSApp.terminate(nil)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(12)
        .onAppear { monitor.start() }
    }
}

struct StatusBarIcon: View {
    let summary: StatusSummary

    private var fillColor: NSColor {
        switch summary.color {
        case "green": return .systemGreen
        case "yellow": return .systemYellow
        case "red": return .systemRed
        default: return .white
        }
    }

    var body: some View {
        Image(nsImage: StatusIconImage.make(fill: fillColor))
            .renderingMode(.original)
            .help(summary.label)
    }
}

enum StatusIconImage {
    static func make(fill: NSColor) -> NSImage {
        let size = NSSize(width: 20, height: 20)
        let image = NSImage(size: size)
        image.lockFocus()

        NSColor.clear.setFill()
        NSBezierPath(rect: NSRect(origin: .zero, size: size)).fill()

        let circleRect = NSRect(x: 0.8, y: 0.8, width: 18.4, height: 18.4)
        let circle = NSBezierPath(ovalIn: circleRect)
        fill.setFill()
        circle.fill()
        NSColor.black.withAlphaComponent(0.28).setStroke()
        circle.lineWidth = 0.8
        circle.stroke()

        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11.5, weight: .bold),
            .foregroundColor: NSColor.black,
        ]
        let pi = "π" as NSString
        let s = pi.size(withAttributes: attrs)
        let r = NSRect(
            x: circleRect.midX - (s.width / 2),
            y: circleRect.midY - (s.height / 2) + 0.2,
            width: s.width,
            height: s.height
        )

        // Cut out π so the menu bar background shows through (Grammarly-style glyph).
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current?.compositingOperation = .clear
        pi.draw(in: r, withAttributes: attrs)
        NSGraphicsContext.restoreGraphicsState()

        image.unlockFocus()
        image.isTemplate = false
        return image
    }
}
