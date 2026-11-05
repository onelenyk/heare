package dev.onelenyk.heare.phone

import android.content.Context
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import dev.onelenyk.heare.Bpe
import java.io.File

/**
 * The assistant's name, heard on the phone itself: sherpa-onnx's keyword
 * spotter, as bringalive used it. Nothing leaves the phone until it hears
 * the name; then the room's next phrase goes to Whisper.
 *
 * The model is English, so the name is listened for as it sounds in Latin
 * letters (Wake.sounds). [threshold]: lower hears it more easily, and the
 * room too; tuned on a Pixel 7a.
 */
class Spotter private constructor(private val spotter: KeywordSpotter, private val stream: OnlineStream) {

    /** One 20 ms frame; true when the name was in it. */
    fun heard(frame: ShortArray): Boolean {
        stream.acceptWaveform(FloatArray(frame.size) { frame[it] / 32768f }, 16_000)
        while (spotter.isReady(stream)) spotter.decode(stream)
        if (spotter.getResult(stream).keyword.isBlank()) return false
        spotter.reset(stream)
        return true
    }

    fun release() {
        stream.release()
        spotter.release()
    }

    companion object {
        private val FILES = listOf("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")

        /** null when no sound of the name can be spelled for the model. */
        fun open(context: Context, sounds: List<String>, threshold: Float = 0.25f, score: Float = 1.5f): Spotter? {
            val dir = File(context.noBackupFilesDir, "kws").apply { mkdirs() }
            // A new APK may carry a new model: copy again after every install.
            val installed = context.packageManager.getPackageInfo(context.packageName, 0).lastUpdateTime.toString()
            val stamp = File(dir, "installed")
            if (stamp.takeIf { it.exists() }?.readText() != installed || FILES.any { !File(dir, it).exists() }) {
                for (name in FILES) {
                    context.assets.open("kws/$name").use { input ->
                        File(dir, name).outputStream().use { input.copyTo(it) }
                    }
                }
                stamp.writeText(installed)
            }
            val bpe = Bpe(File(dir, "tokens.txt").readText())
            val phrases = sounds.mapNotNull(bpe::pieces).distinct()
            if (phrases.isEmpty()) return null
            val keywords = File(dir, "keywords.txt")
            keywords.writeText(phrases.joinToString("\n") { "$it :$score #$threshold" } + "\n")
            val config = KeywordSpotterConfig(
                featConfig = FeatureConfig(sampleRate = 16_000, featureDim = 80),
                modelConfig = OnlineModelConfig(
                    transducer = OnlineTransducerModelConfig(
                        encoder = File(dir, "encoder.onnx").path,
                        decoder = File(dir, "decoder.onnx").path,
                        joiner = File(dir, "joiner.onnx").path,
                    ),
                    tokens = File(dir, "tokens.txt").path,
                    numThreads = 1,
                ),
                maxActivePaths = 4,
                keywordsFile = keywords.path,
                keywordsScore = score,
                keywordsThreshold = threshold,
                numTrailingBlanks = 1,
            )
            // A native abort cannot be caught: the mark stays when the model
            // brought the app down, and the next start listens without it.
            val trying = File(dir, "trying")
            if (trying.exists()) return null
            trying.writeText("1")
            val spotter = KeywordSpotter(assetManager = null, config = config)
            val made = Spotter(spotter, spotter.createStream())
            made.heard(ShortArray(16_000)) // a second of silence, decoded once
            trying.delete()
            return made
        }
    }
}
