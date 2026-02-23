import AppKit
import Foundation
import SwiftUI
import WebKit

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
    @State private var selectedAgentPid: Int32?
    @State private var latestFullMessages: [Int32: String] = [:]
    @State private var latestHtmlMessages: [Int32: String] = [:]
    @State private var selectedLatestAt: Int?

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

    private func selectedMessageText(_ agent: AgentState) -> String {
        if let cached = latestFullMessages[agent.pid]?.trimmingCharacters(in: .whitespacesAndNewlines), !cached.isEmpty {
            return cached
        }
        return latestMessageText(agent)
    }

    private func selectedMessageHtml(_ agent: AgentState) -> String? {
        if let cached = latestHtmlMessages[agent.pid]?.trimmingCharacters(in: .whitespacesAndNewlines), !cached.isEmpty {
            return cached
        }
        if let html = agent.latestMessageHtml?.trimmingCharacters(in: .whitespacesAndNewlines), !html.isEmpty {
            return html
        }
        return nil
    }

    private func htmlAttributedString(_ html: String) -> AttributedString? {
        guard let data = html.data(using: .utf8) else { return nil }
        let options: [NSAttributedString.DocumentReadingOptionKey: Any] = [
            .documentType: NSAttributedString.DocumentType.html,
            .characterEncoding: String.Encoding.utf8.rawValue,
        ]
        guard let ns = try? NSAttributedString(data: data, options: options, documentAttributes: nil), !ns.string.isEmpty else {
            return nil
        }
        return try? AttributedString(ns, including: \.appKit)
    }

    private func htmlStrippedText(_ html: String) -> String {
        let source = html.replacingOccurrences(of: "<br\\s*/?>", with: "\n", options: .regularExpression)
        guard let data = source.data(using: .utf8),
              let ns = try? NSAttributedString(
                data: data,
                options: [
                    .documentType: NSAttributedString.DocumentType.html,
                    .characterEncoding: String.Encoding.utf8.rawValue,
                ],
                documentAttributes: nil
              ) else {
            return source
        }
        return normalizeMojibake(ns.string)
    }

    private func decodeHtmlEntities(_ text: String) -> String {
        text
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&nbsp;", with: " ")
    }

    private func markdownTextFromHtml(_ html: String) -> String? {
        guard let preRange = html.range(of: #"<pre[^>]*>([\s\S]*?)</pre>"#, options: .regularExpression) else {
            return nil
        }
        let preBlock = String(html[preRange])
        guard let bodyRange = preBlock.range(of: #"<pre[^>]*>([\s\S]*?)</pre>"#, options: .regularExpression) else {
            return nil
        }

        let body = String(preBlock[bodyRange])
            .replacingOccurrences(of: #"^<pre[^>]*>"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: "</pre>", with: "")
            .replacingOccurrences(of: "<br>", with: "\n")
            .replacingOccurrences(of: "<br/>", with: "\n")
            .replacingOccurrences(of: "<br />", with: "\n")

        let decoded = normalizeMojibake(
            decodeHtmlEntities(body)
                .replacingOccurrences(of: "\r\n", with: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
        )

        return decoded.isEmpty ? nil : decoded
    }

    private func normalizeMojibake(_ text: String) -> String {
        text
            .replacingOccurrences(of: "â€”", with: "—")
            .replacingOccurrences(of: "â€“", with: "–")
            .replacingOccurrences(of: "â€™", with: "’")
            .replacingOccurrences(of: "â€˜", with: "‘")
            .replacingOccurrences(of: "â€œ", with: "“")
            .replacingOccurrences(of: "â€�", with: "”")
    }

    private func detailMessageText(_ agent: AgentState) -> String {
        let fromSelected = selectedMessageText(agent).trimmingCharacters(in: .whitespacesAndNewlines)
        if !fromSelected.isEmpty { return normalizeMojibake(fromSelected) }
        let gist = latestMessageGist(agent).trimmingCharacters(in: .whitespacesAndNewlines)
        if !gist.isEmpty { return normalizeMojibake(gist) }
        return "(no assistant message available yet)"
    }

    private func detailHtmlAttributed(_ agent: AgentState) -> AttributedString? {
        guard let html = selectedMessageHtml(agent)?.trimmingCharacters(in: .whitespacesAndNewlines), !html.isEmpty else {
            return nil
        }
        return htmlAttributedString(html)
    }

    private func sanitizeHtmlForWebView(_ html: String) -> String {
        let strippedScripts = html.replacingOccurrences(
            of: #"<script[\s\S]*?</script>"#,
            with: "",
            options: .regularExpression
        )
        let strippedEvents = strippedScripts
            .replacingOccurrences(of: #"\son[a-zA-Z]+\s*=\s*"[^"]*""#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\son[a-zA-Z]+\s*=\s*'[^']*'"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: "javascript:", with: "")
        return strippedEvents
    }

    private func escapeHtml(_ text: String) -> String {
        text
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

    private func renderInlineMarkdown(_ text: String) -> String {
        var out = escapeHtml(text)
        out = out.replacingOccurrences(of: #"`([^`]+)`"#, with: "<code>$1</code>", options: .regularExpression)
        out = out.replacingOccurrences(of: #"\*\*([^*]+)\*\*"#, with: "<strong>$1</strong>", options: .regularExpression)
        out = out.replacingOccurrences(of: #"\*([^*]+)\*"#, with: "<em>$1</em>", options: .regularExpression)
        return out
    }

    private func markdownToSimpleHtml(_ markdown: String) -> String {
        let lines = markdown.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var html: [String] = []
        var inUl = false
        var inOl = false
        var inCode = false
        var codeLines: [String] = []

        func closeLists() {
            if inUl { html.append("</ul>"); inUl = false }
            if inOl { html.append("</ol>"); inOl = false }
        }

        for raw in lines {
            let line = raw.trimmingCharacters(in: .whitespaces)

            if line.hasPrefix("```") {
                closeLists()
                if inCode {
                    html.append("<pre><code>\(escapeHtml(codeLines.joined(separator: "\n")))</code></pre>")
                    codeLines.removeAll(keepingCapacity: false)
                    inCode = false
                } else {
                    inCode = true
                }
                continue
            }

            if inCode {
                codeLines.append(raw)
                continue
            }

            if line.isEmpty {
                closeLists()
                continue
            }

            if line.hasPrefix("### ") {
                closeLists()
                html.append("<h3>\(renderInlineMarkdown(String(line.dropFirst(4))))</h3>")
                continue
            }
            if line.hasPrefix("## ") {
                closeLists()
                html.append("<h2>\(renderInlineMarkdown(String(line.dropFirst(3))))</h2>")
                continue
            }
            if line.hasPrefix("# ") {
                closeLists()
                html.append("<h1>\(renderInlineMarkdown(String(line.dropFirst(2))))</h1>")
                continue
            }

            if line.range(of: #"^\d+\.\s+"#, options: .regularExpression) != nil {
                if inUl { html.append("</ul>"); inUl = false }
                if !inOl { html.append("<ol>"); inOl = true }
                let item = line.replacingOccurrences(of: #"^\d+\.\s+"#, with: "", options: .regularExpression)
                html.append("<li>\(renderInlineMarkdown(item))</li>")
                continue
            }

            if line.hasPrefix("- ") || line.hasPrefix("* ") {
                if inOl { html.append("</ol>"); inOl = false }
                if !inUl { html.append("<ul>"); inUl = true }
                let item = String(line.dropFirst(2))
                html.append("<li>\(renderInlineMarkdown(item))</li>")
                continue
            }

            closeLists()
            html.append("<p>\(renderInlineMarkdown(line))</p>")
        }

        if inCode {
            html.append("<pre><code>\(escapeHtml(codeLines.joined(separator: "\n")))</code></pre>")
        }
        closeLists()
        return html.joined(separator: "\n")
    }

    private func wrappedHtmlDocument(_ html: String) -> String {
        let markdownBody = markdownTextFromHtml(html).flatMap { md in
            looksLikeMarkdown(md) ? markdownToSimpleHtml(md) : nil
        }
        let body = sanitizeHtmlForWebView(markdownBody ?? html)

        return """
        <!doctype html>
        <html>
        <head>
          <meta charset=\"utf-8\" />
          <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
          <style>
            :root { color-scheme: light dark; }
            html, body { margin: 0; padding: 0; }
            body {
              font-family: -apple-system, BlinkMacSystemFont, \"SF Pro Text\", sans-serif;
              font-size: 13.5px;
              line-height: 1.48;
              color: #202124;
              background: transparent;
              padding: 4px 4px 10px 4px;
              word-wrap: break-word;
              white-space: normal;
            }
            p { margin: 0 0 0.85em 0; }
            ul, ol { margin: 0.35em 0 1em 1.35em; padding-left: 0.35em; }
            ul { list-style: disc outside; }
            ol { list-style: decimal outside; }
            li { margin: 0.26em 0; padding-left: 0.1em; }
            h1,h2,h3,h4 { margin: 0.75em 0 0.48em 0; line-height: 1.25; }
            h1 + p, h2 + p, h3 + p, h4 + p,
            h1 + ul, h2 + ul, h3 + ul, h4 + ul,
            h1 + ol, h2 + ol, h3 + ol, h4 + ol { margin-top: 0.2em; }
            h1 { font-size: 1.15em; }
            h2 { font-size: 1.08em; }
            h3 { font-size: 1.02em; }
            strong { color: #111827; }
            code {
              font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-size: 0.92em;
              background: rgba(99,102,241,0.10);
              border: 1px solid rgba(99,102,241,0.18);
              border-radius: 6px;
              padding: 0.08em 0.34em;
            }
            pre {
              white-space: pre-wrap;
              overflow-wrap: anywhere;
              font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-size: 0.90em;
              line-height: 1.45;
              background: linear-gradient(180deg, rgba(15,23,42,0.06), rgba(15,23,42,0.04));
              border: 1px solid rgba(15,23,42,0.12);
              border-radius: 10px;
              padding: 10px 12px;
              margin: 0.45em 0 0.9em 0;
            }
            blockquote {
              margin: 0.4em 0;
              padding-left: 0.9em;
              border-left: 3px solid rgba(59,130,246,0.45);
              color: rgba(55,65,81,0.95);
            }
            a { color: #0a84ff; text-decoration: none; }
            a:hover { text-decoration: underline; }
          </style>
        </head>
        <body>
        \(body)
        </body>
        </html>
        """
    }

    private func markdownAttributedString(_ markdown: String) -> AttributedString? {
        let text = markdown.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        return try? AttributedString(
            markdown: text,
            options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .full)
        )
    }

    private func looksLikeMarkdown(_ text: String) -> Bool {
        let sample = text.lowercased()
        if sample.contains("```") || sample.contains("###") || sample.contains("##") || sample.contains("# ") {
            return true
        }
        if sample.contains("- ") || sample.contains("* ") || sample.contains("1. ") {
            return true
        }
        if sample.contains("**") || sample.contains("`") {
            return true
        }
        return false
    }

    private func isSelected(_ agent: AgentState) -> Bool {
        selectedAgentPid == agent.pid
    }

    private func refreshLatest(for agent: AgentState) {
        guard let latest = monitor.latestMessageResponse(for: agent) else { return }
        if let full = latest.latestMessageFull?.trimmingCharacters(in: .whitespacesAndNewlines), !full.isEmpty {
            latestFullMessages[agent.pid] = full
        } else if let gist = latest.latestMessage?.trimmingCharacters(in: .whitespacesAndNewlines), !gist.isEmpty {
            latestFullMessages[agent.pid] = gist
        }
        if let html = latest.latestMessageHtml?.trimmingCharacters(in: .whitespacesAndNewlines), !html.isEmpty {
            latestHtmlMessages[agent.pid] = html
        }
    }

    private func selectAgent(_ agent: AgentState) {
        if selectedAgentPid == agent.pid {
            selectedAgentPid = nil
            selectedLatestAt = nil
            return
        }

        selectedAgentPid = agent.pid
        selectedLatestAt = agent.latestMessageAt
        refreshLatest(for: agent)
    }

    private var selectedAgent: AgentState? {
        guard let pid = selectedAgentPid else { return nil }
        return monitor.agents.first(where: { $0.pid == pid })
    }

    private func syncSelectedAgentFromStatus() {
        guard let agent = selectedAgent else { return }

        if let full = agent.latestMessageFull?.trimmingCharacters(in: .whitespacesAndNewlines), !full.isEmpty {
            latestFullMessages[agent.pid] = full
        } else if let gist = agent.latestMessage?.trimmingCharacters(in: .whitespacesAndNewlines), !gist.isEmpty {
            latestFullMessages[agent.pid] = gist
        }

        if let html = agent.latestMessageHtml?.trimmingCharacters(in: .whitespacesAndNewlines), !html.isEmpty {
            latestHtmlMessages[agent.pid] = html
        }

        if selectedLatestAt != agent.latestMessageAt {
            selectedLatestAt = agent.latestMessageAt
            refreshLatest(for: agent)
        }
    }

    @ViewBuilder
    private func agentRow(_ agent: AgentState) -> some View {
        let selected = isSelected(agent)
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
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                if let context = contextStatusText(agent) {
                    Text(context)
                        .font(.system(size: 10))
                        .foregroundStyle(contextStatusColor(agent))
                        .lineLimit(1)
                }

                Text("Latest: \(latestMessageGist(agent))")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 6)

            VStack(alignment: .trailing, spacing: 4) {
                Button("Jump") {
                    monitor.jump(to: agent)
                }
                .font(.caption2)
                .buttonStyle(.link)

                Image(systemName: selected ? "checkmark.circle.fill" : "chevron.right.circle")
                    .font(.caption)
                    .foregroundStyle(selected ? Color.accentColor : Color.secondary)
            }
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
        .onTapGesture {
            selectAgent(agent)
        }
    }

    @ViewBuilder
    private func agentDetail(_ agent: AgentState) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Agent reply")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Button("Collapse") {
                    selectedAgentPid = nil
                    selectedLatestAt = nil
                }
                .font(.caption)
                .buttonStyle(.link)

                Button("Refresh") {
                    refreshLatest(for: agent)
                }
                .font(.caption)
                .buttonStyle(.link)
            }

            Text(agentPrimaryLine(agent))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            if let html = selectedMessageHtml(agent)?.trimmingCharacters(in: .whitespacesAndNewlines), !html.isEmpty {
                SafeHTMLView(html: wrappedHtmlDocument(html))
                    .frame(minHeight: 170, maxHeight: 380, alignment: .top)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                ScrollView(.vertical) {
                    let text = detailMessageText(agent)
                    if let markdown = markdownAttributedString(text) {
                        Text(markdown)
                            .font(.body)
                            .foregroundStyle(.primary)
                            .tint(.blue)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    } else {
                        Text(text)
                            .font(.body)
                            .lineSpacing(3)
                            .foregroundStyle(.primary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                }
                .frame(minHeight: 170, maxHeight: 380, alignment: .top)
            }

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
                        refreshLatest(for: agent)
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
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(nsColor: .windowBackgroundColor).opacity(0.86))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.accentColor.opacity(0.15), lineWidth: 1)
        )
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
                    agentRow(agent)
                    Divider()
                }

                if let selectedAgent {
                    agentDetail(selectedAgent)
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
        .onChange(of: monitor.agents, initial: false) { _, agents in
            if let selected = selectedAgentPid, !agents.contains(where: { $0.pid == selected }) {
                selectedAgentPid = nil
                selectedLatestAt = nil
                return
            }
            syncSelectedAgentFromStatus()
        }
    }
}

struct SafeHTMLView: NSViewRepresentable {
    let html: String

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = false

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground")
        webView.allowsMagnification = false
        webView.allowsBackForwardNavigationGestures = false
        webView.loadHTMLString(html, baseURL: URL(string: "about:blank"))
        context.coordinator.lastHTML = html
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.lastHTML != html else { return }
        context.coordinator.lastHTML = html
        webView.loadHTMLString(html, baseURL: URL(string: "about:blank"))
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var lastHTML: String = ""

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            let scheme = (url.scheme ?? "").lowercased()
            if scheme == "about" || scheme == "data" {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
            }
        }
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
