import Foundation

/// A selection-only URL. It cannot contain code, filesystem paths or commands.
struct SpaceLink {
    let spaceID: String
    let botID: String

    init?(url: URL) {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "grok-space", parts.host == "open", parts.path.isEmpty,
              parts.user == nil, parts.password == nil, parts.port == nil, parts.fragment == nil,
              let query = parts.queryItems, query.count == 2,
              Set(query.map(\.name)) == Set(["space_id", "bot_id"]),
              let space = query.first(where: { $0.name == "space_id" })?.value,
              let bot = query.first(where: { $0.name == "bot_id" })?.value,
              space.range(of: "\\Aspace_[a-f0-9]{32}\\z", options: .regularExpression) != nil,
              bot.range(of: "\\Abot_[a-f0-9]{32}\\z", options: .regularExpression) != nil else { return nil }
        spaceID = space
        botID = bot
    }

    func localURL(baseURL: URL) -> URL {
        var parts = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "space_id", value: spaceID), URLQueryItem(name: "bot_id", value: botID)]
        return parts.url!
    }
}
