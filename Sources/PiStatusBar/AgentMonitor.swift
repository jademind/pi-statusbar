import Foundation

@MainActor
final class AgentMonitor: ObservableObject {
    @Published private(set) var agents: [AgentState] = []
    @Published private(set) var summary: StatusSummary = .empty
    @Published private(set) var daemonOnline: Bool = false
    @Published private(set) var lastMessage: String?

    private var timer: Timer?

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
}
