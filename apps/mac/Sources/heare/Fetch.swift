import CryptoKit
import Foundation

/// A body's update, now that the brain lives elsewhere: the brain names the
/// version and where the release keeps it; this machine fetches the dmg,
/// checks it against the release's sums, and leaves it where `Updater`
/// takes it from.
enum Fetch {
    /// Only this repository's releases: the brain names the file, never the
    /// place it may come from.
    static let releases = "https://github.com/onelenyk/heare/releases/download/"

    struct Asked {
        let version: String
        let dmg: URL
        let sums: URL
        let name: String
    }

    static func asked(_ json: Any?) -> Asked? {
        guard let said = json as? [String: Any],
              let version = said["version"] as? String,
              version.range(of: #"^\d+\.\d+\.\d+$"#, options: .regularExpression) != nil,
              let dmg = (said["dmg"] as? String).flatMap(URL.init(string:)),
              let sums = (said["sums"] as? String).flatMap(URL.init(string:)),
              let name = said["name"] as? String,
              name.range(of: #"^heare-[a-z0-9_]+\.dmg$"#, options: .regularExpression) != nil,
              dmg.absoluteString.hasPrefix(releases), sums.absoluteString.hasPrefix(releases)
        else { return nil }
        return Asked(version: version, dmg: dmg, sums: sums, name: name)
    }

    /// The architecture CI names a dmg for.
    static var arch: String {
        #if arch(arm64)
        return "arm64"
        #else
        return "x86_64"
        #endif
    }

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 30 * 60
        config.httpAdditionalHeaders = ["User-Agent": "heare"]
        return URLSession(configuration: config)
    }()

    /// The checked dmg's path, or why there is none. Blocking: call it off
    /// the main thread.
    static func take(_ asked: Asked) -> Result<String, Updater.Failure> {
        guard let sums = get(asked.sums), let text = String(data: sums, encoding: .utf8) else {
            return .failure(Updater.Failure(tr("could not download the new version")))
        }
        guard let wanted = expectedSum(text, name: asked.name) else {
            return .failure(Updater.Failure(tr("{sums} has no sum for {dmg}", ["sums": "SHA256SUMS.txt", "dmg": asked.name])))
        }
        guard let file = download(asked.dmg) else {
            return .failure(Updater.Failure(tr("could not download the new version")))
        }
        guard let data = try? Data(contentsOf: file, options: .mappedIfSafe),
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == wanted else {
            try? FileManager.default.removeItem(at: file)
            return .failure(Updater.Failure(tr("the download does not match the release sum — not installing")))
        }
        let folder = Config.heare.appendingPathComponent("cache/update")
        let target = folder.appendingPathComponent("heare-\(asked.version)-\(arch).dmg")
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: target)
            try FileManager.default.moveItem(at: file, to: target)
        } catch {
            return .failure(Updater.Failure(tr("could not download the new version")))
        }
        return .success(target.path)
    }

    /// The sum SHA256SUMS.txt gives one file, as `sha256sum` writes it.
    static func expectedSum(_ sums: String, name: String) -> String? {
        for line in sums.split(separator: "\n") {
            let parts = line.split(separator: " ", omittingEmptySubsequences: true)
            guard parts.count == 2, parts[1].trimmingCharacters(in: CharacterSet(charactersIn: "*")) == name,
                  parts[0].range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { continue }
            return String(parts[0])
        }
        return nil
    }

    private static func get(_ url: URL) -> Data? {
        var out: Data?
        let done = DispatchSemaphore(value: 0)
        session.dataTask(with: url) { data, response, _ in
            if (response as? HTTPURLResponse)?.statusCode == 200 { out = data }
            done.signal()
        }.resume()
        done.wait()
        return out
    }

    private static func download(_ url: URL) -> URL? {
        var out: URL?
        let done = DispatchSemaphore(value: 0)
        session.downloadTask(with: url) { file, response, _ in
            if let file, (response as? HTTPURLResponse)?.statusCode == 200 {
                let kept = FileManager.default.temporaryDirectory
                    .appendingPathComponent("heare-\(UUID().uuidString).dmg")
                if (try? FileManager.default.moveItem(at: file, to: kept)) != nil { out = kept }
            }
            done.signal()
        }.resume()
        done.wait()
        return out
    }
}
