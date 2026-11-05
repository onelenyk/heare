package dev.onelenyk.heare.phone

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import dev.onelenyk.heare.Done
import dev.onelenyk.heare.Skill
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * The phone's hands for the brain: bringalive's accessibility service,
 * carried over. The person turns it on in Android's settings; until then
 * every skill answers that it is off. What it reads of the screen goes to
 * the brain only when the brain's hands ask for it.
 */
class Hands : AccessibilityService() {
    @Volatile private var app: String? = null
    /** The screen as last read: an index means an element of this reading. */
    private val seen = mutableMapOf<Int, AccessibilityNodeInfo>()

    override fun onServiceConnected() { now = this; Phone.handsOn.value = true }
    override fun onDestroy() {
        if (now === this) { now = null; Phone.handsOn.value = false }
        super.onDestroy()
    }
    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            app = event.packageName?.toString()
        }
    }

    /** Every element worth naming: with text, a description, or something to do. */
    private fun screen(): JsonObject {
        seen.clear()
        val lines = StringBuilder()
        val root = rootInActiveWindow
        fun walk(node: AccessibilityNodeInfo) {
            val text = node.text?.toString()?.take(80)
            val desc = node.contentDescription?.toString()?.take(80)
            if (text != null || desc != null || node.isClickable || node.isEditable || node.isScrollable) {
                val index = seen.size
                seen[index] = node
                if (lines.length < SCREEN_CHARS) {
                    lines.append(index).append(' ').append(node.className?.toString()?.substringAfterLast('.'))
                    text?.let { lines.append(" \"").append(it).append('"') }
                    desc?.takeIf { it != text }?.let { lines.append(" (").append(it).append(')') }
                    val can = buildList {
                        if (node.isClickable) add("tap")
                        if (node.isEditable) add("type")
                        if (node.isScrollable) add("scroll")
                    }
                    if (can.isNotEmpty()) lines.append(" [").append(can.joinToString(",")).append(']')
                    lines.append('\n')
                }
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let(::walk)
        }
        root?.let(::walk)
        return buildJsonObject {
            put("app", root?.packageName?.toString() ?: app)
            put("elements", lines.toString().ifEmpty { "(nothing readable)" })
        }
    }

    private fun find(query: String): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        root.findAccessibilityNodeInfosByText(query).firstOrNull()?.let { return it }
        fun walk(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            val words = listOfNotNull(node.text, node.contentDescription, node.hintText, node.viewIdResourceName)
            if (words.any { it.toString().contains(query, ignoreCase = true) }) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let(::walk)?.let { return it }
            return null
        }
        return walk(root)
    }

    /** A label is often not clickable itself; its row is. */
    private fun tap(node: AccessibilityNodeInfo): Boolean {
        var at: AccessibilityNodeInfo? = node
        while (at != null) {
            if (at.isClickable) return at.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            at = at.parent
        }
        return false
    }

    private fun scrolled(forward: Boolean): Boolean {
        fun walk(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            if (node.isScrollable) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let(::walk)?.let { return it }
            return null
        }
        val list = rootInActiveWindow?.let(::walk) ?: return false
        return list.performAction(
            if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
    }

    private fun typed(text: String, field: String?): Boolean {
        val root = rootInActiveWindow ?: return false
        val target = field?.let(::find)
            ?: root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.takeIf { it.isEditable }
            ?: run {
                fun walk(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
                    if (node.isEditable) return node
                    for (i in 0 until node.childCount) node.getChild(i)?.let(::walk)?.let { return it }
                    return null
                }
                walk(root)
            } ?: return false
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        return target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    fun does(name: String, args: JsonObject): Done = when (name) {
        "screen" -> Done(true, screen())
        "active_app" -> Done(true, JsonPrimitive(rootInActiveWindow?.packageName?.toString() ?: app ?: "unknown"))
        "apps" -> Done(true, JsonPrimitive(launchable(this).joinToString("\n") { "${it.first}: ${it.second}" }))
        "back" -> said(performGlobalAction(GLOBAL_ACTION_BACK), "went back")
        "home" -> said(performGlobalAction(GLOBAL_ACTION_HOME), "home screen")
        "scroll" -> said(scrolled(args.text("direction") != "up"), "scrolled")
        "open_app" -> opened(args.text("name").orEmpty())
        "tap" -> {
            val index = (args["index"] as? JsonPrimitive)?.intOrNull
            val node = index?.let { seen[it] } ?: args.text("text")?.let(::find)
            if (node == null) Done(false, JsonPrimitive("no such element; read the screen again"))
            else said(tap(node), "tapped")
        }
        "type" -> said(typed(args.text("text").orEmpty(), args.text("field")), "typed")
        else -> Done(false, JsonPrimitive("no such skill"))
    }

    private fun opened(name: String): Done {
        val wanted = name.trim()
        val match = launchable(this).firstOrNull { it.first.equals(wanted, true) || it.second == wanted }
            ?: launchable(this).firstOrNull { it.first.contains(wanted, true) }
            ?: return Done(false, JsonPrimitive("no app called $wanted; ask apps"))
        val start = packageManager.getLaunchIntentForPackage(match.second)
            ?: return Done(false, JsonPrimitive("${match.first} cannot be opened"))
        startActivity(start.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return Done(true, JsonPrimitive("opened ${match.first}"))
    }

    private fun said(ok: Boolean, what: String) =
        Done(ok, JsonPrimitive(if (ok) what else "could not: the screen did not allow it"))

    companion object {
        @Volatile var now: Hands? = null
            private set

        private const val SCREEN_CHARS = 6_000

        /** What the person can open: (label, package), by label. */
        fun launchable(context: Context): List<Pair<String, String>> {
            val pm = context.packageManager
            val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            return pm.queryIntentActivities(launcher, 0)
                .map { it.loadLabel(pm).toString() to it.activityInfo.packageName }
                .distinctBy { it.second }
                .sortedBy { it.first.lowercase() }
        }

        /** The phone's hands as the brain's model reads them: English, by protocol §6. */
        val SKILLS: List<Skill> = listOf(
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
    }
}

private fun JsonObject.text(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
