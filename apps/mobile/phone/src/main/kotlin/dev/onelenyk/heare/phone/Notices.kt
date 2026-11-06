package dev.onelenyk.heare.phone

import android.app.Notification
import android.app.RemoteInput
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * The phone's notifications as one of heare's eyes: each one arrives as a
 * note, and the brain's hands may read, answer or dismiss them. The person
 * turns this on in Android's «Notification access»; until then every skill
 * says so and no note is sent.
 */
class Notices : NotificationListenerService() {
    override fun onListenerConnected() {
        now = this
        Phone.eyesOn.value = true
    }

    override fun onListenerDisconnected() = gone()

    override fun onDestroy() {
        gone()
        super.onDestroy()
    }

    private fun gone() {
        if (now === this) {
            now = null
            Phone.eyesOn.value = false
        }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (!worth(sbn)) return
        val (title, text) = words(sbn.notification)
        Phone.notification(label(sbn.packageName), title, text)
    }

    private fun worth(sbn: StatusBarNotification): Boolean =
        sbn.packageName != packageName &&
            sbn.notification.flags and Notification.FLAG_GROUP_SUMMARY == 0

    private fun words(notification: Notification): Pair<String, String> {
        val extra = notification.extras
        val title = extra.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty().trim()
        val text = (extra.getCharSequence(Notification.EXTRA_TEXT)
            ?: extra.getCharSequence(Notification.EXTRA_BIG_TEXT))?.toString().orEmpty().trim()
        return title.take(80) to text.take(200)
    }

    private fun label(pkg: String): String = runCatching {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    }.getOrDefault(pkg)

    companion object {
        @Volatile var now: Notices? = null
            private set

        private const val MAX = 12
        private const val MAX_CHARS = 2_000

        fun listing(): String {
            val live = now ?: return OFF
            val found = live.active().take(MAX)
            if (found.isEmpty()) return "no notifications now"
            return found.joinToString("\n") { sbn ->
                val (title, text) = live.words(sbn.notification)
                val words = if (text.isEmpty()) title else "$title — $text"
                "${live.label(sbn.packageName)}: $words"
            }.take(MAX_CHARS)
        }

        fun reply(pkg: String, text: String): Boolean {
            val live = now ?: return false
            if (text.isBlank()) return false
            val sbn = live.active().firstOrNull { it.packageName == pkg } ?: return false
            val action = sbn.notification.actions.orEmpty()
                .firstOrNull { !it.remoteInputs.isNullOrEmpty() } ?: return false
            val input = action.remoteInputs?.firstOrNull() ?: return false
            val intent = Intent()
            RemoteInput.addResultsToIntent(arrayOf(input), intent,
                Bundle().apply { putCharSequence(input.resultKey, text) })
            return runCatching { action.actionIntent.send(live, 0, intent) }.isSuccess
        }

        fun dismiss(pkg: String): Boolean {
            val live = now ?: return false
            val sbn = live.active().firstOrNull { it.packageName == pkg } ?: return false
            return runCatching { live.cancelNotification(sbn.key) }.isSuccess
        }

        private const val OFF =
            "notification access is off: the person turns heare on in Android's settings"
    }

    private fun active(): List<StatusBarNotification> =
        runCatching { activeNotifications }.getOrNull().orEmpty()
            .filter(::worth)
            .sortedByDescending { it.postTime }
}
