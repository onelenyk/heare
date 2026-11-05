package dev.onelenyk.heare.phone

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import dev.onelenyk.heare.Whisper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext
import kotlin.math.sqrt

/**
 * One utterance from the microphone: it starts at the first sound and ends
 * when the person has been quiet for [quietMs], or at [longestMs].
 *
 * The voice is told from the room by loudness over the room's own floor,
 * learned in the first moments: a plain gate, enough for a button the
 * person pressed. The WebRTC VAD of life-as-a-service comes with hands-free
 * listening, where a gate would let every noise through.
 */
class Mic(
    private val quietMs: Int = 900,
    private val longestMs: Int = 30_000,
    private val waitMs: Int = 6_000,
) {
    @Volatile private var stopped = false

    /** Ends the utterance now: the person tapped again. */
    fun stop() { stopped = true }

    @SuppressLint("MissingPermission") // Ears starts only with the microphone allowed
    suspend fun utterance(): ShortArray = withContext(Dispatchers.IO) {
        stopped = false
        val frame = Whisper.RATE / 50 // 20 ms
        val size = maxOf(AudioRecord.getMinBufferSize(
            Whisper.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), frame * 4)
        val record = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
            Whisper.RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
        val kept = ArrayList<ShortArray>()
        try {
            record.startRecording()
            val buffer = ShortArray(frame)
            var floor = 0.0
            var frames = 0
            var heard = false
            var quiet = 0
            while (coroutineContext.isActive && !stopped) {
                val n = record.read(buffer, 0, frame)
                if (n <= 0) continue
                frames++
                val level = rms(buffer, n)
                // The room's floor: the quietest it has been, rising slowly.
                floor = if (frames == 1) level else minOf(level, floor * 1.002 + 1)
                val loud = frames > 10 && level > maxOf(floor * 3, 250.0)
                kept += buffer.copyOf(n)
                if (loud) { heard = true; quiet = 0 } else quiet += 20
                val ms = frames * 20
                if (heard && quiet >= quietMs) break
                if (!heard && ms >= waitMs) break
                if (ms >= longestMs) break
                // Before any voice keep only the last half second: the start of a word.
                if (!heard && kept.size > 25) kept.removeAt(0)
            }
            if (!heard) ShortArray(0) else kept.flatten()
        } finally {
            runCatching { record.stop() }
            record.release()
        }
    }

    private fun rms(buffer: ShortArray, n: Int): Double {
        var sum = 0.0
        for (i in 0 until n) sum += buffer[i].toDouble() * buffer[i]
        return sqrt(sum / n)
    }

    private fun List<ShortArray>.flatten(): ShortArray {
        val out = ShortArray(sumOf { it.size })
        var at = 0
        for (piece in this) { piece.copyInto(out, at); at += piece.size }
        return out
    }
}
