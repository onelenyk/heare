package dev.onelenyk.heare.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.core.app.ServiceCompat
import dev.onelenyk.heare.Wake
import dev.onelenyk.heare.tr
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * The phone's part, alive with the screen off: a foreground service of type
 * microphone, which Android does not kill for being in the background. It
 * holds the brain's stream, and with «hands-free» on, the open microphone
 * (Room). Started only while the app is open, as Android 14 requires for a
 * microphone service.
 */
class Ears : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var living: Job? = null
    private var mouth: TextToSpeech? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServiceCompat.startForeground(
            this, NOTE, notice(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        if (mouth == null) mouth = TextToSpeech(this) { }.apply {
            setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(id: String?) { Phone.speaking = true }
                override fun onDone(id: String?) { quiet() }
                @Deprecated("the old one, still called on some phones")
                override fun onError(id: String?) { quiet() }
            })
        }
        if (living?.isActive != true) {
            val part = Phone.partFor(::speak) ?: run { stopSelf(); return START_NOT_STICKY }
            living = scope.launch { part.live() }
            scope.launch { part.keepKey() }
            Phone.handsFor()?.let { hands -> scope.launch { hands.live() } }
            Phone.eyesFor()?.let { eyes -> scope.launch { eyes.live() } }
            Phone.hush = { mouth?.stop(); Phone.speaking = false }
            scope.launch {
                var ear: Job? = null
                Phone.handsFree.collect { on ->
                    ear?.cancel()
                    ear = if (on) launch { room(part).listen() } else null
                }
            }
        }
        return START_STICKY
    }

    /** The open ear, with the name it listens for — the brain's, once it is known. */
    private suspend fun room(part: dev.onelenyk.heare.Part): Room {
        // The name comes with the knock's answer; wait a little for it.
        val name = withTimeoutOrNull(15_000) { part.state.first { it.name != null }.name }
        val spotter = runCatching { Spotter.open(this, Wake(name).sounds) }
            .onFailure { android.util.Log.w("heare", "no wake word spotter", it) }.getOrNull()
        return Room(
            deaf = { Phone.speaking },
            spotter = spotter,
            answering = { part.answering(System.currentTimeMillis()) },
        ) { pcm, called ->
            part.spoken(pcm, handsFree = true, now = System::currentTimeMillis, called = called)
        }
    }

    /** Spoken out: the room is heard again a moment later, past the echo. */
    private fun quiet() {
        scope.launch {
            delay(300)
            Phone.speaking = false
            Phone.part?.replied(System.currentTimeMillis())
        }
    }

    private fun speak(text: String) {
        if (!Phone.visible) told(text)
        val tts = mouth ?: return
        val language = Phone.talk.value?.language ?: "uk"
        tts.language = Locale.forLanguageTag(language)
        tts.speak(text, TextToSpeech.QUEUE_ADD, null, text.hashCode().toString())
    }

    override fun onDestroy() {
        scope.cancel()
        mouth?.shutdown()
        super.onDestroy()
    }

    /**
     * What the brain said while the conversation was not on the screen —
     * a reminder, news, a reply heard from the pocket — as a notification
     * that sounds, so a phone face down still shows it.
     */
    private fun told(text: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(SAID, tr("What heare says"), NotificationManager.IMPORTANCE_HIGH))
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        manager.notify(SAID_NOTE, Notification.Builder(this, SAID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle(Phone.talk.value?.name ?: "heare")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build())
    }

    private fun notice(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(NOTIFICATION_CHANNEL, tr("heare is here"), NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, NOTIFICATION_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("heare")
            .setContentText(tr("Connected to the brain"))
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val NOTE = 1
        private const val NOTIFICATION_CHANNEL = "ears"
        private const val SAID = "said"
        private const val SAID_NOTE = 2

        /** The conversation is open: what was told is read already. */
        fun seen(context: Context) {
            context.getSystemService(NotificationManager::class.java).cancel(SAID_NOTE)
        }

        fun start(context: Context) {
            context.startForegroundService(Intent(context, Ears::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, Ears::class.java))
        }
    }
}
