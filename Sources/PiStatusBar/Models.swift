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
    let attachedWindow: Bool?
    let terminalApp: String?
    let telemetrySource: String?
    let modelProvider: String?
    let modelId: String?
    let modelName: String?
    let sessionId: String?
    let sessionName: String?
    let contextPercent: Double?
    let contextPressure: String?
    let contextCloseToLimit: Bool?
    let contextNearLimit: Bool?
    let contextTokens: Int?
    let contextWindow: Int?
    let contextRemainingTokens: Int?
    let latestMessage: String?
    let latestMessageFull: String?
    let latestMessageHtml: String?
    let latestMessageAt: Int?

    enum CodingKeys: String, CodingKey {
        case pid, ppid, state, tty, cpu, cwd, activity, confidence, mux
        case muxSession = "mux_session"
        case clientPid = "client_pid"
        case attachedWindow = "attached_window"
        case terminalApp = "terminal_app"
        case telemetrySource = "telemetry_source"
        case modelProvider = "model_provider"
        case modelId = "model_id"
        case modelName = "model_name"
        case sessionId = "session_id"
        case sessionName = "session_name"
        case contextPercent = "context_percent"
        case contextPressure = "context_pressure"
        case contextCloseToLimit = "context_close_to_limit"
        case contextNearLimit = "context_near_limit"
        case contextTokens = "context_tokens"
        case contextWindow = "context_window"
        case contextRemainingTokens = "context_remaining_tokens"
        case latestMessage = "latest_message"
        case latestMessageFull = "latest_message_full"
        case latestMessageHtml = "latest_message_html"
        case latestMessageAt = "latest_message_at"
    }

    var id: Int32 { pid }
    var hasAttachedClient: Bool { clientPid != nil }
    var hasAttachedWindow: Bool { attachedWindow ?? hasAttachedClient }
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

struct SendMessageResponse: Decodable {
    let ok: Bool
    let pid: Int32?
    let delivery: String?
    let muxSession: String?
    let tty: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, pid, delivery, tty, error
        case muxSession = "mux_session"
    }
}

struct LatestMessageResponse: Decodable {
    let ok: Bool
    let pid: Int32?
    let latestMessage: String?
    let latestMessageFull: String?
    let latestMessageHtml: String?
    let latestMessageAt: Int?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, pid, error
        case latestMessage = "latest_message"
        case latestMessageFull = "latest_message_full"
        case latestMessageHtml = "latest_message_html"
        case latestMessageAt = "latest_message_at"
    }
}
