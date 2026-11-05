package dev.onelenyk.heare

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/** The phone's hands as the brain's model reads them: English, by protocol §6. */
val PHONE_SKILLS: List<Skill> = listOf(
    skill("screen", "Read what is on the phone's screen now: numbered elements, what each can do", "read"),
    skill("active_app", "Which app is open on the phone", "read"),
    skill("apps", "The apps on the phone that can be opened: label and package", "read"),
    skill("back", "Press back on the phone", "harmless"),
    skill("home", "Go to the phone's home screen", "harmless"),
    skill("scroll", "Scroll the list on the phone's screen", "harmless",
        "direction" to "down or up"),
    skill("open_app", "Open an app on the phone by its label or package", "change",
        "name" to "the app's label, as apps lists it", required = listOf("name")),
    skill("tap", "Tap an element on the phone's screen: by its number from screen, or by its text",
        "change", "index" to "the element's number from screen", "text" to "text on the element"),
    skill("type", "Type text into a field on the phone's screen (the focused one, or the one named)",
        "change", "text" to "what to type", "field" to "text or hint of the field",
        required = listOf("text")),
)

private fun skill(
    name: String, about: String, effect: String,
    vararg params: Pair<String, String>, required: List<String> = emptyList(),
) = Skill(name, about, effect, buildJsonObject {
    put("type", "object")
    putJsonObject("properties") {
        for ((key, what) in params) putJsonObject(key) {
            put("type", if (key == "index") "integer" else "string")
            put("description", what)
        }
    }
    if (required.isNotEmpty()) putJsonArray("required") { required.forEach { add(JsonPrimitive(it)) } }
})

/**
 * The app the person meant: the label exactly, or the package, then a label
 * that holds the words. Spoken names come in any case.
 */
fun pick(apps: List<Pair<String, String>>, wanted: String): Pair<String, String>? {
    val said = wanted.trim()
    if (said.isEmpty()) return null
    return apps.firstOrNull { it.first.equals(said, true) || it.second == said }
?: apps.firstOrNull { it.first.contains(said, true) }
}
