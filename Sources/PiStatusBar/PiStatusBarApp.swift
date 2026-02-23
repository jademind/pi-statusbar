import AppKit
import Foundation
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
    @State private var replyDrafts: [Int32: String] = [:]
    @State private var expandedAgentPid: Int32?
    @State private var expandedFullMessages: [Int32: String] = [:]

    private func sourceText(_ source: String) -> String {
        switch source {
        case "pi-telemetry": return "source: telemetry"
        case "process-fallback": return "source: fallback"
        case "offline": return "source: offline"
        default: return "source: fallback"
        }
    }

    private func sourceColor(_ source: String) -> Color {
        switch source {
        case "pi-telemetry": return .green
        case "offline": return .red
        default: return .orange
        }
    }

    private func pill(_ text: String, color: Color = .secondary) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(
                Capsule()
                    .fill(Color.gray.opacity(0.12))
            )
    }

    private func agentPrimaryLine(_ agent: AgentState) -> String {
        let cleanedSessionName = agent.sessionName?.trimmingCharacters(in: .whitespacesAndNewlines)
        let hasSessionName = !(cleanedSessionName ?? "").isEmpty

        if let mux = agent.mux, let muxSession = agent.muxSession, hasSessionName {
            return "\(mux): \(muxSession) · \(cleanedSessionName!) · \(agent.activity.label)"
        }
        if let mux = agent.mux, let muxSession = agent.muxSession {
            return "\(mux): \(muxSession) · \(agent.activity.label)"
        }
        if let mux = agent.mux, hasSessionName {
            return "\(mux) · \(cleanedSessionName!) · \(agent.activity.label)"
        }
        if hasSessionName {
            return "session: \(cleanedSessionName!) · \(agent.activity.label)"
        }
        if let mux = agent.mux {
            return "\(mux) · \(agent.activity.label)"
        }
        return "shell · \(agent.activity.label)"
    }

    private func windowStatusText(_ agent: AgentState) -> String {
        if agent.hasAttachedWindow {
            if let app = agent.terminalApp {
                return "window attached · \(app)"
            }
            return "window attached"
        }
        if let app = agent.terminalApp {
            return "no attached window · \(app)"
        }
        return "no attached window"
    }

    private func formatInt(_ value: Int?) -> String? {
        guard let value else { return nil }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: value))
    }

    private func modelMetricsLine(_ agent: AgentState) -> String {
        let model = agent.modelName ?? agent.modelId ?? "model unknown"

        if let used = formatInt(agent.contextTokens),
           let limit = formatInt(agent.contextWindow) {
            return "\(model) · tokens \(used)/\(limit)"
        }

        if let remaining = formatInt(agent.contextRemainingTokens) {
            return "\(model) · remaining \(remaining)"
        }

        return model
    }

    private func contextMood(_ agent: AgentState) -> (emoji: String, label: String, color: Color) {
        if agent.contextPressure == "at_limit" {
            return ("🙁", "at limit", .red)
        }
        if agent.contextNearLimit == true || agent.contextCloseToLimit == true {
            return ("☹️", "close to limit", .orange)
        }
        return ("🙂", "healthy", .secondary)
    }

    private func contextStatusText(_ agent: AgentState) -> String? {
        guard let percent = agent.contextPercent else { return nil }
        let rounded = Int(percent.rounded())
        let mood = contextMood(agent)
        return "Context \(rounded)% · \(mood.label) \(mood.emoji)"
    }

    private func contextStatusColor(_ agent: AgentState) -> Color {
        contextMood(agent).color
    }

    private func latestMessageText(_ agent: AgentState) -> String {
        if let msg = agent.latestMessageFull?.trimmingCharacters(in: .whitespacesAndNewlines), !msg.isEmpty {
            return msg
        }
        if let msg = agent.latestMessage?.trimmingCharacters(in: .whitespacesAndNewlines), !msg.isEmpty {
            return msg
        }
        return "(no assistant message available yet)"
    }

    private func latestMessageGist(_ agent: AgentState) -> String {
        if let gist = agent.latestMessage?.trimmingCharacters(in: .whitespacesAndNewlines), !gist.isEmpty {
            return gist
        }
        let full = latestMessageText(agent)
        let compact = full.replacingOccurrences(of: "\n", with: " ")
        if compact.count <= 420 { return compact }
        let tail = compact.suffix(417)
        return "..." + tail
    }

    private func expandedMessageText(_ agent: AgentState) -> String {
        if let cached = expandedFullMessages[agent.pid]?.trimmingCharacters(in: .whitespacesAndNewlines), !cached.isEmpty {
            return cached
        }
        return latestMessageText(agent)
    }

    private func isExpanded(_ agent: AgentState) -> Bool {
        expandedAgentPid == agent.pid
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

            HStack(spacing: 6) {
                pill(sourceText(monitor.dataSource), color: sourceColor(monitor.dataSource))
                pill("active: \(monitor.agents.count)")
                pill("running: \(monitor.runningCount)")
                pill("waiting: \(monitor.waitingCount)")
                if monitor.atLimitCount > 0 {
                    pill("at limit: \(monitor.atLimitCount)", color: .red)
                } else if (monitor.nearLimitCount + monitor.closeToLimitCount) > 0 {
                    pill("close limit: \(monitor.nearLimitCount + monitor.closeToLimitCount)", color: .orange)
                }
            }

            if monitor.atLimitCount > 0 {
                Text("🚨 Attention: \(monitor.atLimitCount) agent(s) are at context limit.")
                    .font(.caption2)
                    .foregroundStyle(.red)
            } else if (monitor.nearLimitCount + monitor.closeToLimitCount) > 0 {
                Text("⚠️ Attention: \(monitor.nearLimitCount + monitor.closeToLimitCount) agent(s) are close to context limit.")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }

            if monitor.agents.isEmpty {
                Text("No active Pi agents detected")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(monitor.agents) { agent in
                    let expanded = isExpanded(agent)

                    VStack(alignment: .leading, spacing: 8) {
                        Button {
                            withAnimation(.easeInOut(duration: 0.15)) {
                                expandedAgentPid = expanded ? nil : agent.pid
                            }
                            if !expanded {
                                if let full = monitor.latestFullMessage(for: agent) {
                                    expandedFullMessages[agent.pid] = full
                                }
                            }
                        } label: {
                            HStack(alignment: .top, spacing: 8) {
                                Circle()
                                    .fill(agent.activity.color)
                                    .frame(width: 8, height: 8)
                                    .padding(.top, 4)

                                VStack(alignment: .leading, spacing: 3) {
                                    Text(agentPrimaryLine(agent))
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(.primary)
                                        .lineLimit(1)

                                    if let cwd = agent.cwd {
                                        Text(cwd)
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }

                                    Text("PID \(agent.pid) · \(windowStatusText(agent))")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)

                                    Text(modelMetricsLine(agent))
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)

                                    if let context = contextStatusText(agent) {
                                        Text(context)
                                            .font(.caption2)
                                            .foregroundStyle(contextStatusColor(agent))
                                            .lineLimit(1)
                                    }

                                    Text("Latest: \(latestMessageGist(agent))")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(3)
                                        .fixedSize(horizontal: false, vertical: true)
                                }

                                Spacer(minLength: 6)

                                Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)

                        if expanded {
                            ScrollView(.vertical) {
                                Text(expandedMessageText(agent))
                                    .font(.body)
                                    .lineSpacing(2)
                                    .foregroundStyle(.primary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled)
                            }
                            .frame(maxHeight: 360)

                            HStack(spacing: 6) {
                                TextField("Reply to agent", text: Binding(
                                    get: { replyDrafts[agent.pid, default: ""] },
                                    set: { replyDrafts[agent.pid] = $0 }
                                ))
                                .textFieldStyle(.roundedBorder)

                                Button("Send") {
                                    let current = replyDrafts[agent.pid, default: ""]
                                    if monitor.send(message: current, to: agent) {
                                        replyDrafts[agent.pid] = ""
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(replyDrafts[agent.pid, default: ""].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                                Button("Jump") {
                                    monitor.jump(to: agent)
                                }
                                .buttonStyle(.borderless)
                            }
                        }

                        Divider()
                    }
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
