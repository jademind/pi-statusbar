import Foundation

enum SessionLocator {
    private static var lastScan: Date = .distantPast
    private static var cwdToSessionPath: [String: String] = [:]

    static func sessionPath(forCwd cwd: String?) -> String? {
        guard let cwd, !cwd.isEmpty else { return nil }
        refreshIfNeeded()
        return cwdToSessionPath[cwd]
    }

    private static func refreshIfNeeded() {
        if Date().timeIntervalSince(lastScan) < 8 {
            return
        }
        lastScan = Date()

        let root = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".pi-statubar/sessions", isDirectory: true)

        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isRegularFileKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else {
            cwdToSessionPath = [:]
            return
        }

        var newestByCwd: [String: (path: String, modified: Date)] = [:]

        for case let url as URL in enumerator {
            guard url.pathExtension == "jsonl" else { continue }

            guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .contentModificationDateKey]),
                  values.isRegularFile == true,
                  let modified = values.contentModificationDate else {
                continue
            }

            guard let cwd = readSessionCwd(path: url.path) else { continue }

            if let existing = newestByCwd[cwd], existing.modified >= modified {
                continue
            }
            newestByCwd[cwd] = (url.path, modified)
        }

        cwdToSessionPath = newestByCwd.mapValues { $0.path }
    }

    private static func readSessionCwd(path: String) -> String? {
        guard let handle = FileHandle(forReadingAtPath: path) else { return nil }
        defer { try? handle.close() }

        guard let data = try? handle.read(upToCount: 1200),
              let text = String(data: data, encoding: .utf8),
              let firstLine = text.split(separator: "\n", maxSplits: 1).first,
              let jsonData = String(firstLine).data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
              obj["type"] as? String == "session",
              let cwd = obj["cwd"] as? String else {
            return nil
        }

        return cwd
    }
}
