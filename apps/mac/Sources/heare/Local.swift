import Foundation

/// Calls to the brain and the keeper on this machine.
///
/// An ephemeral session that keeps no cookies: the only cookie the app
/// holds is the page's own, in the web view's store.
enum Local {
    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        config.httpShouldSetCookies = false
        config.httpCookieAcceptPolicy = .never
        return URLSession(configuration: config)
    }()

    struct Answer {
        let status: Int
        let json: Any?
        let response: HTTPURLResponse?
    }

    static func call(_ url: URL, method: String = "GET", body: [String: Any]? = nil,
                     headers: [String: String] = [:],
                     done: @escaping (Answer) -> Void) {
        var request = URLRequest(url: url)
        request.httpMethod = method
        for (name, value) in headers { request.setValue(value, forHTTPHeaderField: name) }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        session.dataTask(with: request) { data, response, _ in
            let http = response as? HTTPURLResponse
            let json = data.flatMap { try? JSONSerialization.jsonObject(with: $0) }
            let answer = Answer(status: http?.statusCode ?? 0, json: json, response: http)
            DispatchQueue.main.async { done(answer) }
        }.resume()
    }
}
