package dev.onelenyk.heare

import io.ktor.client.HttpClient
import io.ktor.client.request.forms.formData
import io.ktor.client.request.forms.submitFormWithBinaryData
import io.ktor.client.request.header
import io.ktor.client.statement.bodyAsText
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject

/**
 * Speech to words the way the Mac's voice does it (heare-voice stt.py):
 * Whisper large v3 at Groq, with the key the brain lent. 16 kHz mono PCM in.
 */
class Whisper(private val http: HttpClient) {
    suspend fun words(pcm: ShortArray, key: String, language: String?): String {
        val answer = http.submitFormWithBinaryData(
            url = "https://api.groq.com/openai/v1/audio/transcriptions",
            formData = formData {
                append("model", "whisper-large-v3")
                append("response_format", "json")
                language?.let { append("language", it) }
                append("file", wav(pcm), Headers.build {
                    append(HttpHeaders.ContentType, "audio/wav")
                    append(HttpHeaders.ContentDisposition, "filename=\"audio.wav\"")
                })
            },
        ) { header(HttpHeaders.Authorization, "Bearer $key") }
        val text = answer.bodyAsText()
        if (answer.status != HttpStatusCode.OK) throw Refused(answer.status.value, text)
        val said = (Json.parseToJsonElement(text).jsonObject["text"] as? JsonPrimitive)?.contentOrNull
        return said.orEmpty().trim()
    }

    companion object {
        const val RATE = 16_000

        /** A WAV file around 16-bit mono PCM: the header Whisper reads. */
        fun wav(pcm: ShortArray): ByteArray {
            val data = pcm.size * 2
            val out = ByteArray(44 + data)
            fun put(at: Int, text: String) = text.forEachIndexed { i, c -> out[at + i] = c.code.toByte() }
            fun int(at: Int, v: Int) { for (i in 0..3) out[at + i] = (v shr (8 * i)).toByte() }
            fun short(at: Int, v: Int) { out[at] = v.toByte(); out[at + 1] = (v shr 8).toByte() }
            put(0, "RIFF"); int(4, 36 + data); put(8, "WAVE")
            put(12, "fmt "); int(16, 16); short(20, 1); short(22, 1)
            int(24, RATE); int(28, RATE * 2); short(32, 2); short(34, 16)
            put(36, "data"); int(40, data)
            pcm.forEachIndexed { i, s -> short(44 + i * 2, s.toInt()) }
            return out
        }
    }
}
