package dev.onelenyk.heare

import io.ktor.client.HttpClient
import io.ktor.client.plugins.timeout
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.prepareGet
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsChannel
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.utils.io.readUTF8Line
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/** The wire version the brain speaks (`PROTOCOL` in heare.core.forms). */
const val PROTOCOL = 1

@Serializable
data class Paired(val pid: String, val token: String)

/** What a part says of itself when it knocks (protocol.md §4). */
@Serializable
data class Knock(
    val channel: String,
    val forms: List<String>,
    val about: String,
    val key: String,
    val host: String,
    val version: String,
    val started: Double,
    /** What the brain's hands may ask of this part (protocol.md §6); none for a voice. */
    val skills: List<Skill>? = null,
    val v: Int = PROTOCOL,
)

/**
 * One thing a part can do (protocol.md §6). [about] is read by the brain's
 * model, so it is English; [effect] is `read`, `harmless` or `change`, and
 * only a change goes past the judge.
 */
@Serializable
data class Skill(val name: String, val about: String, val effect: String, val params: JsonObject)

/** What a part made of a call: [result] goes back to the hands as it is. */
data class Done(val ok: Boolean, val result: JsonElement)

@Serializable
private data class Answer(val ok: Boolean, val result: JsonElement)

@Serializable
data class Knocked(
    val approved: Boolean = false,
    val pid: String? = null,
    val token: String? = null,
    val why: String? = null,
    val name: String? = null,
    val language: String? = null,
)

@Serializable
private data class PairAsk(val kind: String, val code: String, val name: String)

@Serializable
private data class Heard(
    val channel: String,
    val text: String,
    val kind: String = "heard",
    val addressed: Boolean = true,
    val v: Int = PROTOCOL,
)

@Serializable
private data class KeyAsk(val key: String, val fingerprint: String, val version: String)

@Serializable
data class Lent(val value: String? = null, val waiting: Boolean = false)

/** The brain answered, but not with a yes: the code, the key or the name. */
class Refused(val status: Int, why: String) : Exception(why)

/**
 * The brain's door, seen from a part: the same calls `heare/part.py` makes.
 * Nothing here keeps state; [Part] does.
 */
class Brain(val base: String, private val http: HttpClient) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false }

    /** A one-time code from the pult becomes this machine's key. */
    suspend fun pair(code: String, name: String): Paired {
        val answer = http.post("$base/api/pair") {
            contentType(ContentType.Application.Json)
            setBody(json.encodeToString(PairAsk.serializer(), PairAsk("machine", code.trim(), name)))
        }
        val text = answer.bodyAsText()
        if (answer.status != HttpStatusCode.OK) throw Refused(answer.status.value, text)
        return json.decodeFromString(Paired.serializer(), text)
    }

    /** The knock waits while the person decides (up to 120 s on the brain). */
    suspend fun knock(token: String, about: Knock): Knocked {
        val answer = http.post("$base/api/bodies") {
            header(TOKEN, token)
            contentType(ContentType.Application.Json)
            setBody(json.encodeToString(Knock.serializer(), about))
            timeout { requestTimeoutMillis = 180_000 }
        }
        val text = answer.bodyAsText()
        if (answer.status != HttpStatusCode.OK) throw Refused(answer.status.value, text)
        return json.decodeFromString(Knocked.serializer(), text)
    }

    /** A phrase to the brain; [overheard] when it was not said to it (kept, not answered). */
    suspend fun say(token: String, channel: String, text: String, overheard: Boolean = false) {
        val answer = http.post("$base/api/input") {
            header(TOKEN, token)
            contentType(ContentType.Application.Json)
            setBody(json.encodeToString(Heard.serializer(),
                Heard(channel, text, kind = if (overheard) "overheard" else "heard")))
        }
        if (answer.status != HttpStatusCode.OK) throw Refused(answer.status.value, answer.bodyAsText())
    }

    /**
     * Ask the brain to lend a key (protocol.md, «Ключі частин»). The person
     * allows it on the pult once per [fingerprint]; until then [Lent.waiting].
     */
    suspend fun ask(token: String, key: String, fingerprint: String, version: String): Lent {
        val answer = http.post("$base/api/keys/ask") {
            header(TOKEN, token)
            contentType(ContentType.Application.Json)
            setBody(json.encodeToString(KeyAsk.serializer(), KeyAsk(key, fingerprint, version)))
        }
        val text = answer.bodyAsText()
        if (answer.status != HttpStatusCode.OK) throw Refused(answer.status.value, text)
        return json.decodeFromString(Lent.serializer(), text)
    }

    /** The answer to call [id]; the brain takes it only from the part it was for. */
    suspend fun answer(token: String, id: Long, done: Done) {
        val answer = http.post("$base/api/skills/$id") {
            header(TOKEN, token)
            contentType(ContentType.Application.Json)
            setBody(json.encodeToString(Answer.serializer(), Answer(done.ok, done.result)))
        }
        if (answer.status != HttpStatusCode.OK) throw Refused(answer.status.value, answer.bodyAsText())
    }

    suspend fun alive(): Boolean =
        runCatching { http.get("$base/health").status == HttpStatusCode.OK }.getOrDefault(false)

    /**
     * One opening of the event stream; it ends when the stream does. The
     * token rides in the query as the Mac's parts send it: a stream that
     * reconnects through a proxy keeps no headers of its own.
     */
    fun events(token: String, onOpen: suspend () -> Unit = {}): Flow<JsonObject> = flow {
        http.prepareGet("$base/api/events") {
            parameter("token", token)
            timeout {
                requestTimeoutMillis = Long.MAX_VALUE
                socketTimeoutMillis = SILENT_MS
            }
        }.execute { response ->
            if (response.status != HttpStatusCode.OK) {
                throw Refused(response.status.value, response.bodyAsText())
            }
            onOpen()
            val channel = response.bodyAsChannel()
            while (true) {
                val line = channel.readUTF8Line() ?: break
                if (!line.startsWith("data: ")) continue
                val event = runCatching { json.parseToJsonElement(line.substring(6)).jsonObject }
                    .getOrNull() ?: continue
                emit(event)
            }
        }
    }

    companion object {
        const val TOKEN = "X-Heare-Token"
        // The brain writes «still here» every few seconds; longer is a dead stream.
        const val SILENT_MS = 45_000L
    }
}
