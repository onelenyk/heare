package dev.onelenyk.heare

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.HttpTimeout
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlin.test.Test
import kotlin.test.assertEquals

class PartTest {
    private val about = Knock("phone", listOf("input", "output"), "a phone", "phone@pixel", "pixel", "0.0.0", 0.0)

    private fun part(spoken: MutableList<String> = mutableListOf()): Part {
        val http = HttpClient(MockEngine { respond("{}", HttpStatusCode.OK, headersOf("Content-Type", "application/json")) }) {
            install(HttpTimeout)
        }
        return Part(Brain("http://brain", http), "machine", about) { spoken += it }
    }

    private fun event(text: String) = Json.parseToJsonElement(text).jsonObject

    @Test
    fun pieces_of_a_reply_grow_one_line_and_said_closes_it() {
        val spoken = mutableListOf<String>()
        val part = part(spoken)
        part.heard(event("""{"kind":"reply","channel":"phone","text":"При"}"""))
        part.heard(event("""{"kind":"reply","channel":"phone","text":"віт."}"""))
        assertEquals(listOf(Line(Who.Brain, "Привіт.", done = false)), part.state.value.lines)
        part.heard(event("""{"kind":"said","channel":"phone","text":"Привіт.","failed":null}"""))
        assertEquals(listOf(Line(Who.Brain, "Привіт.")), part.state.value.lines)
        assertEquals(listOf("Привіт."), spoken)
    }

    @Test
    fun another_channel_is_not_this_part_s_talk() {
        val part = part()
        part.heard(event("""{"kind":"reply","channel":"voice","text":"Привіт."}"""))
        assertEquals(emptyList(), part.state.value.lines)
    }

    @Test
    fun a_failed_turn_is_shown_as_failed() {
        val part = part()
        part.heard(event("""{"kind":"said","channel":"phone","text":"Не вийшло.","failed":"model"}"""))
        assertEquals(true, part.state.value.lines.single().failed)
    }

    @Test
    fun a_missing_line_reads_in_english() = runTest {
        Words.use("""{"Connected":"На зв'язку"}""")
        assertEquals("На зв'язку", tr("Connected"))
        assertEquals("Paired as {name}".replace("{name}", "pixel"), tr("Paired as {name}", "name" to "pixel"))
        Words.use(null)
    }
}
