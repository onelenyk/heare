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
import androidx.core.app.ServiceCompat
import dev.onelenyk.heare.tr
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * The phone's part, alive with the screen off: a foreground service of type
 * microphone, which Android does not kill for being in the background. It
 * holds the brain's stream now; the microphone joins it with the voice
 * (mobile.md, step 3). Started only while the app is open, as Android 14
 * requires for a microphone service.
 */
class Ears : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var living: Job? = null
    private var mouth: TextToSpeech? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServiceCompat.startForeground(
            this, NOTE, notice(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        if (mouth == null) mouth = TextToSpeech(this) { }
        if (living?.isActive != true) {
            val part = Phone.partFor(::speak) ?: run { stopSelf(); return START_NOT_STICKY }
            living = scope.launch { part.live() }
            scope.launch { part.keepKey() }
            Phone.hush = { mouth?.stop() }
        }
        return START_STICKY
    }

    private fun speak(text: String) {
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

    private fun notice(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, tr("heare is here"), NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("heare")
            .setContentText(tr("Connected to the brain"))
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val NOTE = 1
        private const val CHANNEL = "ears"

        fun start(context: Context) {
            context.startForegroundService(Intent(context, Ears::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, Ears::class.java))
        }
    }
}
