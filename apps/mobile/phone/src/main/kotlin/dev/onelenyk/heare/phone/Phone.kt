package dev.onelenyk.heare.phone

import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import dev.onelenyk.heare.Brain
import dev.onelenyk.heare.Knock
import dev.onelenyk.heare.Part
import dev.onelenyk.heare.Refused
import dev.onelenyk.heare.Talk
import dev.onelenyk.heare.Whisper
import dev.onelenyk.heare.Words
import dev.onelenyk.heare.Hearing
import dev.onelenyk.heare.tr
import io.ktor.client.HttpClient
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.HttpTimeout
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlin.random.Random

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Phone.open(this)
    }
}

/**
 * The phone as one heare part: the channel `phone`, a voice of its own on
 * the brain. What it keeps is the brain's address and this machine's key,
 * in the app's private storage; the part's own key lives in memory only.
 */
object Phone {
    const val CHANNEL = "phone"
    const val DEFAULT_BRAIN = "https://brain.onelenyk.dev"

    private lateinit var prefs: SharedPreferences
    private lateinit var app: Context
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val http = HttpClient(OkHttp) { install(HttpTimeout) }

    /** null: not paired yet. */
    val talk = MutableStateFlow<Talk?>(null)
    var part: Part? = null
        private set

    fun open(context: Context) {
        app = context.applicationContext
        prefs = app.getSharedPreferences("heare", Context.MODE_PRIVATE)
        handsFree.value = prefs.getBoolean("hands_free", false)
    }

    val paired: Boolean get() = prefs.getString("machine_token", null) != null
    val base: String get() = prefs.getString("brain", null) ?: DEFAULT_BRAIN

    /** Stable for this install: the brain remembers trust by it. */
    val name: String
        get() = prefs.getString("name", null) ?: run {
            val model = Build.MODEL.replace(Regex("[^A-Za-z0-9._-]"), "-").trim('-').take(24)
            val made = "$model-${Random.nextInt(0x1000, 0xffff).toString(16)}"
            prefs.edit().putString("name", made).apply()
            made
        }

    /** A code from the pult; null when it worked, else what went wrong. */
    suspend fun pair(address: String, code: String): String? {
        val url = address.trim().trimEnd('/').ifEmpty { DEFAULT_BRAIN }
        return try {
            val paired = Brain(url, http).pair(code, name)
            prefs.edit().putString("brain", url).putString("machine_token", paired.token).apply()
            null
        } catch (e: Refused) {
            if (e.status == 401) tr("The code did not fit. Take a new one on the pult.") else e.message
        } catch (e: Exception) {
            tr("The brain does not answer at {url}", "url" to url)
        }
    }

    fun forget() {
        prefs.edit().remove("machine_token").apply()
        part = null
        talk.value = null
    }

    /** The part the service lives; made once per pairing. */
    fun partFor(speak: (String) -> Unit): Part? {
        val token = prefs.getString("machine_token", null) ?: return null
        part?.let { return it }
        val about = Knock(
            channel = CHANNEL,
            forms = listOf("input", "output"),
            about = "a phone in the person's pocket: hears what is typed or said to it and says the replies",
            key = "$CHANNEL@$name",
            host = name,
            version = BuildConfig.VERSION_NAME,
            started = System.currentTimeMillis() / 1000.0,
        )
        return Part(Brain(base, http), token, about, speak, Whisper(http)).also { made ->
            part = made
            scope.launch {
                var language: String? = null
                made.state.collect { now ->
                    if (now.language != language) {
                        language = now.language
                        Words.use(language?.let(::wordsFor))
                    }
                    talk.value = now
                }
            }
        }
    }

    private val mic = Mic()

    /** The conversation is on the screen: a reply needs no notification then. */
    @Volatile var visible = false

    /** The phone is speaking: the open microphone must not hear it. */
    @Volatile var speaking = false

    /** The microphone always open (Room), kept across launches. */
    val handsFree = MutableStateFlow(false)

    fun handsFree(on: Boolean) {
        prefs.edit().putBoolean("hands_free", on).apply()
        handsFree.value = on
    }

    /** Set by Ears: stops the phone speaking, so it does not hear itself. */
    var hush: () -> Unit = {}

    /** The microphone button: a tap listens, a second tap ends the phrase. */
    fun listen() {
        val live = part ?: return
        when (live.state.value.hearing) {
            Hearing.Listening -> mic.stop()
            Hearing.Recognizing -> Unit
            Hearing.Idle -> {
                hush()
                live.hearing(Hearing.Listening)
                scope.launch {
                    val pcm = runCatching { mic.utterance() }.getOrDefault(ShortArray(0))
                    runCatching { live.spoken(pcm) }.onFailure { live.hearing(Hearing.Idle) }
                }
            }
        }
    }

    fun say(text: String) {
        val live = part ?: return
        scope.launch { runCatching { live.say(text) } }
    }

    private fun wordsFor(language: String): String? =
        runCatching { app.assets.open("words/$language.json").bufferedReader().readText() }.getOrNull()
}
