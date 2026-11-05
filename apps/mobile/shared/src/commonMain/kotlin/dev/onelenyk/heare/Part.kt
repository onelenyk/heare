package dev.onelenyk.heare

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/** Where the part stands with the brain. */
enum class Link { Knocking, Here, Away, Unpaired }

enum class Who { Person, Brain }

data class Line(val who: Who, val text: String, val done: Boolean = true, val failed: Boolean = false)

data class Talk(
    val link: Link = Link.Knocking,
    val lines: List<Line> = emptyList(),
    val name: String? = null,
    val language: String? = null,
    val why: String? = null,
)

/**
 * One part's life, as `part.live` lives it on the Mac: knock with the
 * machine's key, keep the stream open, knock again and reopen whenever it
 * breaks. The brain restarting is a wait, never the end; only a machine key
 * the brain no longer knows ends it (the person unpaired this phone).
 *
 * [onSaid] gets each finished reply: the platform speaks it.
 */
class Part(
    private val brain: Brain,
    private val machineToken: String,
    private val about: Knock,
    private val onSaid: (String) -> Unit = {},
) {
    private val talk = MutableStateFlow(Talk())
    val state: StateFlow<Talk> = talk
    private var token: String? = null

    suspend fun live() {
        var wait = 1_000L
        while (true) {
            try {
                val fresh = token == null
                val mine = token ?: knock(machineToken)
                brain.events(mine, onOpen = {
                    // Back after a break: knock again, as part.py does on reopen,
                    // so a brain that restarted knows this part is here.
                    if (!fresh) knock(mine)
                    talk.update { it.copy(link = Link.Here, why = null) }
                    wait = 1_000L
                }).collect(::heard)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Refused) {
                if (e.status == 401 && token == null) {
                    talk.update { it.copy(link = Link.Unpaired, why = e.message) }
                    return
                }
                // Our own key was forgotten: the machine's key asks for a new one.
                if (e.status == 401 || e.status == 403) token = null
                talk.update { it.copy(link = Link.Away, why = e.message) }
            } catch (e: Exception) {
                talk.update { it.copy(link = Link.Away, why = e.message ?: e::class.simpleName) }
            }
            delay(wait)
            wait = (wait * 2).coerceAtMost(30_000L)
        }
    }

    private suspend fun knock(with: String): String {
        talk.update { it.copy(link = Link.Knocking) }
        val answer = brain.knock(with, about)
        if (!answer.approved) throw Refused(403, answer.why ?: "not approved")
        val mine = answer.token ?: with
        token = mine
        talk.update { it.copy(name = answer.name ?: it.name, language = answer.language ?: it.language) }
        return mine
    }

    /** What the person said here: shown at once, then sent. */
    suspend fun say(text: String) {
        val said = text.trim()
        if (said.isEmpty()) return
        talk.update { it.copy(lines = it.lines + Line(Who.Person, said)) }
        val mine = token ?: throw Refused(0, "not connected")
        brain.say(mine, about.channel, said)
    }

    internal fun heard(event: JsonObject) {
        if (event.text("channel") != about.channel) return
        when (event.text("kind")) {
            "reply" -> talk.update { now ->
                val piece = event.text("text") ?: return@update now
                val last = now.lines.lastOrNull()
                if (last != null && last.who == Who.Brain && !last.done) {
                    now.copy(lines = now.lines.dropLast(1) + last.copy(text = last.text + piece))
                } else {
                    now.copy(lines = now.lines + Line(Who.Brain, piece, done = false))
                }
            }
            "said" -> {
                val text = event.text("text").orEmpty()
                val failed = (event["failed"] as? JsonPrimitive)?.let { it.booleanOrNull ?: (it.contentOrNull != null) } ?: false
                talk.update { now ->
                    val last = now.lines.lastOrNull()
                    val line = Line(Who.Brain, text.ifEmpty { last?.text.orEmpty() }, done = true, failed = failed)
                    if (last != null && last.who == Who.Brain && !last.done) {
                        now.copy(lines = now.lines.dropLast(1) + line)
                    } else {
                        now.copy(lines = now.lines + line)
                    }
                }
                if (text.isNotBlank()) onSaid(text)
            }
        }
    }
}

private fun JsonObject.text(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
