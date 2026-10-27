import Foundation

/// What the shell says to the person, in the language chosen on the page.
///
/// One catalog with the Python side: build.sh turns heare.po into
/// <lang>.lproj/Localizable.strings, and the key is the English line itself,
/// so a line with no translation is still a line. The language is the
/// `language` setting, read again when the settings file changes, not the
/// Mac's: the page, the voice and the menu speak one language.
enum Words {
    private static var seen: (Date?, String, Bundle?) = (nil, "", nil)

    static func bundle() -> Bundle? {
        let changed = (try? FileManager.default.attributesOfItem(atPath: Config.file.path))?[.modificationDate] as? Date
        if changed != nil, changed == seen.0 { return seen.2 }
        let language = Config.read().language
        let found = Bundle.main.path(forResource: language, ofType: "lproj").flatMap(Bundle.init(path:))
        seen = (changed, language, found)
        return found
    }
}

/// The line in the chosen language; `{name}` places are filled from *vars*.
func tr(_ key: String, _ vars: [String: CustomStringConvertible] = [:]) -> String {
    var said = Words.bundle()?.localizedString(forKey: key, value: key, table: nil) ?? key
    for (name, value) in vars { said = said.replacingOccurrences(of: "{\(name)}", with: "\(value)") }
    return said
}
