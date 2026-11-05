package dev.onelenyk.heare.phone

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import dev.onelenyk.heare.Done
import dev.onelenyk.heare.PHONE_SKILLS
import dev.onelenyk.heare.pick
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
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

    /** When the screen last moved (uptime ms). */
    @Volatile private var moved = 0L

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        moved = SystemClock.uptimeMillis()
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
        target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        // The action's own answer lies mid-transition (false, yet typed):
        // what counts is what the field holds now.
        target.refresh()
        return target.text?.toString() == text
    }

    /** Typing into a field that is still appearing: once more, when it stands. */
    private suspend fun typedSurely(text: String, field: String?): Boolean {
        if (typed(text, field)) return true
        settled()
        return typed(text, field)
    }

    /**
     * Until the screen stops moving: bringalive's waitForIdle. A skill that
     * reads mid-animation reads the screen that is leaving, and one that
     * answers mid-animation tells the brain about a screen not there yet.
     */
    private suspend fun settled() {
        val start = SystemClock.uptimeMillis()
        delay(150)
        while (SystemClock.uptimeMillis() - start < SETTLE_MS) {
            if (SystemClock.uptimeMillis() - moved >= QUIET_MS && rootInActiveWindow != null) return
            delay(50)
        }
    }

    private fun front(): String? = rootInActiveWindow?.packageName?.toString() ?: app

    suspend fun does(name: String, args: JsonObject): Done {
        val read = name in setOf("screen", "active_app", "apps")
        if (read) settled()
        val done = act(name, args)
        if (!read && done.ok) settled()
        return done
    }

    private suspend fun act(name: String, args: JsonObject): Done = when (name) {
        "screen" -> Done(true, screen())
        "active_app" -> Done(true, JsonPrimitive(front() ?: "unknown"))
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
        "type" -> said(typedSurely(args.text("text").orEmpty(), args.text("field")), "typed")
        else -> Done(false, JsonPrimitive("no such skill"))
    }

    private suspend fun opened(name: String): Done {
        val wanted = name.trim()
        val match = pick(launchable(this), wanted) ?: return Done(false, JsonPrimitive("no app called $wanted; ask apps"))
        val start = packageManager.getLaunchIntentForPackage(match.second)
            ?: return Done(false, JsonPrimitive("${match.first} cannot be opened"))
        val before = front()
        startActivity(start.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        // Said opened only once Android shows it: the next skill reads that app.
        // An app opens where it was left, and that screen may be another
        // package's (Settings' search is), so any new front counts, and is named.
        val shown = withTimeoutOrNull(OPEN_MS) {
            while (front() != match.second && (front() == before || front() == null)) delay(50)
            true
        } == true
        return if (!shown) Done(false, JsonPrimitive("asked to open ${match.first}, but ${front()} is still in front"))
        else if (front() == match.second) Done(true, JsonPrimitive("opened ${match.first}"))
        else Done(true, JsonPrimitive("opened ${match.first}, where it was left: ${front()} in front"))
    }

    private fun said(ok: Boolean, what: String) =
        Done(ok, JsonPrimitive(if (ok) what else "could not: the screen did not allow it"))

    companion object {
        @Volatile var now: Hands? = null
            private set

        private const val SCREEN_CHARS = 6_000
        // Inside the brain's five seconds for a call, with room to answer.
        private const val SETTLE_MS = 2_000L
        private const val QUIET_MS = 300L
        private const val OPEN_MS = 2_500L

        /** What the person can open: (label, package), by label. */
        fun launchable(context: Context): List<Pair<String, String>> {
            val pm = context.packageManager
            val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            return pm.queryIntentActivities(launcher, 0)
                .map { it.loadLabel(pm).toString() to it.activityInfo.packageName }
                .distinctBy { it.second }
                .sortedBy { it.first.lowercase() }
        }

        val SKILLS = PHONE_SKILLS
    }
}

private fun JsonObject.text(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
