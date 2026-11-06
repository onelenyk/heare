package dev.onelenyk.heare

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

private val NUMBERS = setOf("index", "seconds", "percent", "max")
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
    skill("wait", "Wait on the phone, then look again: screens that load need this",
        "harmless", "seconds" to "how long to wait, 1 to 20", required = listOf("seconds"),
        within = 25),
    skill("long_press", "Press and hold an element on the phone's screen: by its number from screen, or by its text",
        "change", "index" to "the element's number from screen", "text" to "text on the element",
        within = 10),
    skill("swipe", "Swipe across the phone's screen", "harmless",
        "direction" to "down, up, left or right", "percent" to "how far, 10 to 90, half by default",
        required = listOf("direction"), within = 10),
    skill("tap_and_wait", "Tap an element and wait until the screen changes; answers with the screen after it changed",
        "change", "index" to "the element's number from screen", "text" to "text on the element",
        "seconds" to "how long to wait for the change, 1 to 20, five by default",
        within = 30),
    skill("scroll_to_find", "Scroll the list until the words are on the screen; answers with the screen that has them",
        "harmless", "text" to "the words to find", "direction" to "down or up",
        "max" to "how many scrolls at most, six by default",
        required = listOf("text"), within = 30),
    skill("notifications", "What notifications are on the phone now: app, title and text", "read"),
    skill("notification_reply", "Answer a notification on the phone, where it can be answered", "change",
        "app" to "the app's package, as notifications gives it", "text" to "what to answer",
        required = listOf("app", "text"), within = 10),
    skill("notification_dismiss", "Dismiss an app's notification on the phone", "change",
        "app" to "the app's package, as notifications gives it",
        required = listOf("app"), within = 10),
    skill("clipboard", "What the clipboard holds on the phone, when Android lets it be read", "read"),
    skill("set_clipboard", "Put text on the phone's clipboard", "change",
        "text" to "what to copy", required = listOf("text")),
    skill("share", "Share text from the phone with another app, the person choosing which", "change",
        "text" to "what to share", required = listOf("text"), within = 15),
    skill("open_url", "Open a web page on the phone", "harmless",
        "url" to "an http or https address", required = listOf("url"), within = 15),
    skill("device", "What phone this is: maker, model, Android and heare versions", "read"),
)

private fun skill(
    name: String, about: String, effect: String,
    vararg params: Pair<String, String>, required: List<String> = emptyList(),
    within: Int? = null,
) = Skill(name, about, effect, buildJsonObject {
    put("type", "object")
    putJsonObject("properties") {
        for ((key, what) in params) putJsonObject(key) {
            put("type", if (key in NUMBERS) "integer" else "string")
            put("description", what)
        }
    }
    if (required.isNotEmpty()) putJsonArray("required") { required.forEach { add(JsonPrimitive(it)) } }
}, within)

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
