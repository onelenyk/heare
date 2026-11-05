package dev.onelenyk.heare

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject

/**
 * What the app says to the person, from the one catalog the brain, the page
 * and the Mac read: the build turns heare.po into words/<language>.json, and
 * the key is the English line, so a line with no translation still reads.
 * The language is the brain's (its knock answer says it), not the phone's.
 */
object Words {
    private var table: Map<String, String> = emptyMap()

    /** The JSON a platform found for the brain's language, or null for English. */
    fun use(json: String?) {
        table = json?.let { text ->
            runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull()
                ?.mapNotNull { (k, v) -> (v as? JsonPrimitive)?.contentOrNull?.let { k to it } }
                ?.toMap()
        } ?: emptyMap()
    }

    internal fun line(key: String): String = table[key] ?: key
}

/** The line in the brain's language; `{name}` places are filled from [vars]. */
fun tr(key: String, vararg vars: Pair<String, Any>): String {
    var said = Words.line(key)
    for ((name, value) in vars) said = said.replace("{$name}", value.toString())
    return said
}
