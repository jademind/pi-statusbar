import Foundation
import Darwin

enum DaemonClient {
    private static let socketPath = (NSHomeDirectory() as NSString).appendingPathComponent(".pi/agent/statusd.sock")

    static func status() -> StatusResponse? {
        request("status", as: StatusResponse.self)
    }

    static func jump(pid: Int32) -> JumpResponse? {
        request("jump \(pid)", as: JumpResponse.self)
    }

    static func sendMessage(pid: Int32, message: String) -> SendMessageResponse? {
        let sanitized = message.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sanitized.isEmpty else { return nil }
        return request("send \(pid) \(sanitized)", as: SendMessageResponse.self)
    }

    static func latest(pid: Int32) -> LatestMessageResponse? {
        request("latest \(pid)", as: LatestMessageResponse.self)
    }

    private static func request<T: Decodable>(_ command: String, as type: T.Type) -> T? {
        guard let data = send(command: command + "\n") else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private static func send(command: String) -> Data? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        if fd < 0 { return nil }
        defer { close(fd) }

        var addr = sockaddr_un()
        addr.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        addr.sun_family = sa_family_t(AF_UNIX)

        let pathBytes = Array(socketPath.utf8) + [0]
        withUnsafeMutableBytes(of: &addr.sun_path) { buffer in
            buffer.initializeMemory(as: UInt8.self, repeating: 0)
            let n = min(pathBytes.count, buffer.count)
            _ = buffer.copyBytes(from: pathBytes.prefix(n))
        }

        let connectOK = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                connect(fd, sa, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if connectOK != 0 { return nil }

        command.withCString { cstr in
            _ = write(fd, cstr, strlen(cstr))
        }

        var result = Data()
        var buf = [UInt8](repeating: 0, count: 4096)

        while true {
            let n = read(fd, &buf, buf.count)
            if n <= 0 { break }
            result.append(contentsOf: buf[0..<n])
            if buf.prefix(max(0, n)).contains(10) { // newline
                break
            }
        }

        return result
    }
}
