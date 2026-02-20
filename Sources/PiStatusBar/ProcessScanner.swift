import Foundation
import Darwin

struct ProcessSession {
    let pid: Int32
    let cwd: String?
    let sessionPath: String?
}

enum ProcessScanner {
    static func findPiAgentSessions() -> [ProcessSession] {
        let pids = listPiPidsFromPs()
        if pids.isEmpty { return [] }

        return pids.map { pid in
            let cwd = currentWorkingDirectory(for: pid)
            let sessionPath = SessionLocator.sessionPath(forCwd: cwd)
            return ProcessSession(pid: pid, cwd: cwd, sessionPath: sessionPath)
        }
    }

    private static func listPiPidsFromPs() -> [Int32] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/ps")
        process.arguments = ["-axo", "pid=,comm="]

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()

        do {
            try process.run()
        } catch {
            return []
        }

        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return [] }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let text = String(data: data, encoding: .utf8) else { return [] }

        var pids: [Int32] = []
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }

            let parts = line.split(maxSplits: 1, whereSeparator: { $0.isWhitespace }).map(String.init)
            guard parts.count == 2,
                  let pid = Int32(parts[0]),
                  parts[1] == "pi" else {
                continue
            }
            pids.append(pid)
        }

        return pids
    }

    private static func currentWorkingDirectory(for pid: pid_t) -> String? {
        var vinfo = proc_vnodepathinfo()
        let size = proc_pidinfo(
            pid,
            PROC_PIDVNODEPATHINFO,
            0,
            &vinfo,
            Int32(MemoryLayout<proc_vnodepathinfo>.stride)
        )
        if size <= 0 {
            return nil
        }

        return withUnsafePointer(to: &vinfo.pvi_cdir.vip_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXPATHLEN)) {
                String(cString: $0)
            }
        }
    }
}
