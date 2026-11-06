package dev.onelenyk.heare.phone

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import dev.onelenyk.heare.tr

/**
 * After a restart the microphone cannot come back by itself: Android does not
 * let a foreground microphone service start in the background. So the person
 * gets one notification that opens the app — `Ears` then starts as usual.
 */
class Boot : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED || !Phone.paired) return
        val open = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, tr("heare"), NotificationManager.IMPORTANCE_DEFAULT))
        manager.notify(ID, NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle(tr("heare"))
            .setContentText(tr("Tap to listen"))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build())
    }

    private companion object {
        const val CHANNEL = "heare-start"
        const val ID = 1
    }
}
