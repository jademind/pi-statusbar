import SwiftUI

enum AgentActivity: String, Decodable, Equatable {
    case running
    case waitingInput = "waiting_input"
    case unknown

    var label: String {
        switch self {
        case .running: return "Running"
        case .waitingInput: return "Waiting for input"
        case .unknown: return "Unknown"
        }
    }

    var color: Color {
        switch self {
        case .running: return .red
        case .waitingInput: return .green
        case .unknown: return .yellow
        }
    }
}

struct AgentState: Identifiable, Decodable, Equatable {
    let pid: Int32
    let ppid: Int32
    let state: String
    let tty: String
    let cpu: Double
    let cwd: String?
    let activity: AgentActivity
    let confidence: String
    let mux: String?
    let muxSession: String?
    let clientPid: Int32?
    let telemetrySource: String?
    let contextPercent: Double?
    let contextPressure: String?
    let contextCloseToLimit: Bool?
    let contextNearLimit: Bool?

    enum CodingKeys: String, CodingKey {
        case pid, ppid, state, tty, cpu, cwd, activity, confidence, mux
        case muxSession = "mux_session"
        case clientPid = "client_pid"
        case telemetrySource = "telemetry_source"
        case contextPercent = "context_percent"
        case contextPressure = "context_pressure"
        case contextCloseToLimit = "context_close_to_limit"
        case contextNearLimit = "context_near_limit"
    }

    var id: Int32 { pid }
    var hasAttachedClient: Bool { clientPid != nil }
}

struct StatusSummary: Decodable, Equatable {
    let total: Int
    let running: Int
    let waitingInput: Int
    let unknown: Int
    let color: String
    let label: String

    enum CodingKeys: String, CodingKey {
        case total, running, unknown, color, label
        case waitingInput = "waiting_input"
    }

    var uiColor: Color {
        switch color {
        case "green": return .green
        case "yellow": return .yellow
        case "red": return .red
        default: return .gray
        }
    }

    static let empty = StatusSummary(total: 0, running: 0, waitingInput: 0, unknown: 0, color: "gray", label: "No Pi agents")
}

struct StatusResponse: Decodable {
    let ok: Bool
    let timestamp: Int?
    let agents: [AgentState]?
    let summary: StatusSummary?
    let source: String?
    let error: String?
}

struct JumpResponse: Decodable {
    let ok: Bool
    let pid: Int32?
    let clientPid: Int32?
    let focused: Bool?
    let openedAttach: Bool?
    let openedShell: Bool?
    let fallbackOpened: Bool?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, pid, focused, error
        case clientPid = "client_pid"
        case openedAttach = "opened_attach"
        case openedShell = "opened_shell"
        case fallbackOpened = "fallback_opened"
    }
}
