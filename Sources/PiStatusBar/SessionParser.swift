import Foundation

enum SessionParser {
    static func parseState(sessionPath: String) -> (cwd: String?, activity: AgentActivity)? {
        guard let data = readTail(from: sessionPath, maxBytes: 24 * 1024) else {
            return nil
        }

        guard let content = String(data: data, encoding: .utf8) else {
            return nil
        }

        let lines = content.split(separator: "\n").map(String.init)
        var title: String?
        var cwd: String?

        for line in lines.reversed() {
            guard let json = jsonObject(from: line) else { continue }

            if cwd == nil,
               json["type"] as? String == "session",
               let sessionCwd = json["cwd"] as? String {
                cwd = sessionCwd
            }

            if title == nil,
               json["type"] as? String == "custom",
               json["customType"] as? String == "ad:terminal-title",
               let data = json["data"] as? [String: Any],
               let t = data["title"] as? String {
                title = t
            }

            if title != nil, cwd != nil { break }
        }

        let inferredActivity: AgentActivity
        if let title {
            let normalized = title.trimmingCharacters(in: .whitespacesAndNewlines)
            let hasStatusSuffix = normalized.contains(" (") && normalized.hasSuffix(")")
            inferredActivity = hasStatusSuffix ? .running : .waitingInput
        } else {
            inferredActivity = .running
        }

        return (cwd, inferredActivity)
    }

    private static func readTail(from path: String, maxBytes: Int) -> Data? {
        guard let handle = FileHandle(forReadingAtPath: path) else { return nil }
        defer { try? handle.close() }

        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let fileSize = attrs[.size] as? NSNumber else {
            return nil
        }

        let size = fileSize.intValue
        let offset = max(0, size - maxBytes)
        try? handle.seek(toOffset: UInt64(offset))
        return try? handle.readToEnd()
    }

    private static func jsonObject(from line: String) -> [String: Any]? {
        guard let data = line.data(using: .utf8),
              let any = try? JSONSerialization.jsonObject(with: data),
              let obj = any as? [String: Any] else {
            return nil
        }
        return obj
    }
}
