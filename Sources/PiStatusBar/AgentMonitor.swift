import Foundation

@MainActor
final class AgentMonitor: ObservableObject {
    @Published private(set) var agents: [AgentState] = []
    @Published private(set) var summary: StatusSummary = .empty
    @Published private(set) var daemonOnline: Bool = false
    @Published private(set) var lastMessage: String?
    @Published private(set) var dataSource: String = "fallback"

    private var timer: Timer?

    var runningCount: Int { agents.filter { $0.activity == .running }.count }
    var waitingCount: Int { agents.filter { $0.activity == .waitingInput }.count }
    var closeToLimitCount: Int { agents.filter { $0.contextCloseToLimit == true }.count }
    var nearLimitCount: Int { agents.filter { $0.contextNearLimit == true }.count }
    var atLimitCount: Int { agents.filter { $0.contextPressure == "at_limit" }.count }

    func start() {
        if timer != nil { return }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refresh()
            }
        }
    }

    func refresh() {
        guard let response = DaemonClient.status(), response.ok else {
            daemonOnline = false
            agents = []
            summary = .empty
            dataSource = "offline"
            lastMessage = "pi-statusd unavailable"
            return
        }

        daemonOnline = true
        agents = (response.agents ?? []).sorted { lhs, rhs in
            if lhs.activity != rhs.activity {
                return lhs.activity == .waitingInput
            }
            return lhs.pid < rhs.pid
        }
        summary = response.summary ?? .empty
        dataSource = response.source ?? "fallback"
        lastMessage = nil
    }

    func jump(to agent: AgentState) {
        guard let response = DaemonClient.jump(pid: agent.pid), response.ok else {
            lastMessage = "Could not jump to PID \(agent.pid)"
            return
        }

        if response.focused == true {
            lastMessage = "Focused terminal for PID \(agent.pid)"
        } else if response.openedAttach == true {
            lastMessage = "Opened terminal and attached session for PID \(agent.pid)"
        } else if response.openedShell == true {
            lastMessage = "Opened shell for PID \(agent.pid)"
        } else if response.clientPid != nil {
            lastMessage = "Found attached client but could not focus window for PID \(agent.pid)"
        } else {
            lastMessage = "No matching open shell found for PID \(agent.pid)"
        }
    }

    func send(message: String, to agent: AgentState) -> Bool {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            lastMessage = "Message is empty"
            return false
        }

        guard let response = DaemonClient.sendMessage(pid: agent.pid, message: text) else {
            lastMessage = "Could not send message to PID \(agent.pid)"
            return false
        }

        guard response.ok else {
            lastMessage = response.error ?? "Could not send message to PID \(agent.pid)"
            return false
        }

        let delivery = response.delivery ?? "unknown"
        lastMessage = "Sent message to PID \(agent.pid) via \(delivery)"
        return true
    }

    func latestMessageResponse(for agent: AgentState) -> LatestMessageResponse? {
        guard let response = DaemonClient.latest(pid: agent.pid), response.ok else {
            return nil
        }
        return response
    }

    func setMessage(_ message: String) {
        lastMessage = message
    }
}
