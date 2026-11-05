package dev.onelenyk.heare.phone

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.konovalov.vad.webrtc.VadWebRTC
import com.konovalov.vad.webrtc.config.FrameSize
import com.konovalov.vad.webrtc.config.Mode
import com.konovalov.vad.webrtc.config.SampleRate
import dev.onelenyk.heare.Whisper
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.coroutines.coroutineContext

/**
 * The room, heard with no button: the microphone stays open, and nothing
 * leaves the phone until [spotter] hears the assistant's name — or the
 * person answers right after a reply ([answering]). Then the WebRTC voice
 * detector life-as-a-service used cuts the phrase out, and it goes to
 * [heard] — one at a time, while the next one is already listened for.
 *
 * With no spotter (the model would not load) every phrase goes, and the
 * words decide afterwards (Wake), as before.
 *
 * Deaf while [deaf] says so (the phone is speaking): it must not answer
 * itself.
 */
class Room(
    private val deaf: () -> Boolean,
    private val spotter: Spotter?,
    private val answering: () -> Boolean,
    private val heard: suspend (ShortArray, Boolean) -> Unit,
) {
    @SuppressLint("MissingPermission") // Ears starts only with the microphone allowed
    suspend fun listen() = coroutineScope {
        val phrases = Channel<ShortArray>(capacity = 4)
        launch { for (pcm in phrases) runCatching { heard(pcm, spotter != null) } }

        val frame = 320 // 20 ms at 16 kHz
        val size = maxOf(AudioRecord.getMinBufferSize(
            Whisper.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), frame * 8)
        val record = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
            Whisper.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
        val vad = VadWebRTC(
            sampleRate = SampleRate.SAMPLE_RATE_16K,
            frameSize = FrameSize.FRAME_SIZE_320,
            mode = Mode.VERY_AGGRESSIVE,
            silenceDurationMs = QUIET_MS,
            speechDurationMs = 150,
        )
        val before = ArrayDeque<ShortArray>()
        val kept = ArrayList<ShortArray>()
        var speaking = false
        var openUntil = 0L
        try {
            record.startRecording()
            val buffer = ShortArray(frame)
            while (coroutineContext.isActive) {
                val n = record.read(buffer, 0, frame)
                if (n != frame) continue
                if (deaf()) {
                    before.clear(); kept.clear(); speaking = false
                    continue
                }
                val piece = buffer.copyOf()
                val now = System.currentTimeMillis()
                if (spotter != null && !speaking && now > openUntil && !answering()) {
                    // The gate: only the name opens it, heard here, not in the cloud.
                    if (spotter.heard(piece)) {
                        openUntil = now + OPEN_MS
                        before.clear()
                    }
                    continue
                }
                val speech = vad.isSpeech(piece)
                if (!speaking) {
                    before.addLast(piece)
                    if (before.size > BEFORE_FRAMES) before.removeFirst()
                    if (speech) {
                        speaking = true
                        kept.addAll(before); before.clear()
                    }
                    continue
                }
                kept += piece
                if (!speech || kept.size >= LONGEST_FRAMES) {
                    // Shorter than a word is a knock on the table.
                    if (kept.size >= SHORTEST_FRAMES) phrases.trySend(kept.joined())
                    kept.clear(); speaking = false; openUntil = 0L
                }
            }
        } finally {
            phrases.close()
            runCatching { record.stop() }
            record.release()
            vad.close()
            spotter?.release()
        }
    }

    private fun List<ShortArray>.joined(): ShortArray {
        val out = ShortArray(sumOf { it.size })
        var at = 0
        for (piece in this) { piece.copyInto(out, at); at += piece.size }
        return out
    }

    private companion object {
        const val QUIET_MS = 800
        const val OPEN_MS = 8_000L // after the name, this long to start the phrase
        const val BEFORE_FRAMES = 15 // 300 ms: the start of the first word
        const val SHORTEST_FRAMES = 30 // 600 ms
        const val LONGEST_FRAMES = 1_500 // 30 s
    }
}
