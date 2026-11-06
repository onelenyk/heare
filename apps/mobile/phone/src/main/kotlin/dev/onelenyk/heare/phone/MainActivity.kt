package dev.onelenyk.heare.phone

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import dev.onelenyk.heare.Hearing
import dev.onelenyk.heare.Line
import dev.onelenyk.heare.Link
import dev.onelenyk.heare.Talk
import dev.onelenyk.heare.Who
import dev.onelenyk.heare.tr
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val asking = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        startEars()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            HeareTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    val talk by Phone.talk.collectAsState()
                    var paired by remember { mutableStateOf(Phone.paired) }
                    if (!paired) {
                        Pairing { paired = true; startEars() }
                    } else {
                        // The words come with the brain's language: draw again when it arrives.
                        key(talk?.language) {
                            Conversation(talk) {
                                Ears.stop(this)
                                Phone.forget()
                                paired = false
                            }
                        }
                    }
                }
            }
        }
        if (Phone.paired) startEars()
    }

    override fun onResume() { super.onResume(); Phone.visible = true; Ears.seen(this) }
    override fun onPause() { Phone.visible = false; super.onPause() }

    /** The microphone service may start only with the microphone allowed. */
    private fun startEars() {
        val missing = listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS)
            .filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty() || missing == listOf(Manifest.permission.POST_NOTIFICATIONS)) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) Ears.start(this)
            if (missing.isEmpty()) return
        }
        asking.launch(missing.toTypedArray())
    }
}

@Composable
private fun Pairing(done: () -> Unit) {
    var address by remember { mutableStateOf(Phone.DEFAULT_BRAIN) }
    var code by remember { mutableStateOf("") }
    var trouble by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(
        Modifier.fillMaxSize().safeDrawingPadding().imePadding()
            .verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Spacer(Modifier.height(24.dp))
        Text(tr("heare"), style = MaterialTheme.typography.headlineLarge,
            color = MaterialTheme.colorScheme.primary)
        Text(tr("Pair this phone with its brain."), style = MaterialTheme.typography.headlineSmall)
        Text(tr("On the pult, take a pairing code and type it here."),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(address, { address = it }, Modifier.fillMaxWidth(),
            label = { Text(tr("Brain address")) }, singleLine = true,
            shape = MaterialTheme.shapes.medium)
        OutlinedTextField(code, { code = it }, Modifier.fillMaxWidth(),
            label = { Text(tr("Code")) }, singleLine = true,
            shape = MaterialTheme.shapes.medium)
        trouble?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(
            enabled = !busy && code.isNotBlank(),
            onClick = {
                busy = true
                scope.launch {
                    trouble = Phone.pair(address, code)
                    busy = false
                    if (trouble == null) done()
                }
            },
            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
        ) { Text(if (busy) tr("Pairing…") else tr("Pair")) }
    }
}

@Composable
private fun Conversation(talk: Talk?, forget: () -> Unit) {
    var typed by remember { mutableStateOf("") }
    var typing by remember { mutableStateOf(false) }
    val list = rememberLazyListState()
    val lines = talk?.lines.orEmpty()
    val handsOn by Phone.handsOn.collectAsState()
    val eyesOn by Phone.eyesOn.collectAsState()
    val handsFree by Phone.handsFree.collectAsState()
    val speaking by Phone.speakingNow.collectAsState()
    val hearing = talk?.hearing ?: Hearing.Idle
    val ready = talk?.link == Link.Here && talk.keyWaiting.not()
    LaunchedEffect(lines.size, lines.lastOrNull()?.text) {
        if (lines.isNotEmpty()) list.animateScrollToItem(lines.size - 1)
    }
    Column(Modifier.fillMaxSize().safeDrawingPadding().imePadding().padding(horizontal = 16.dp)) {
        Header(talk?.name, forget)
        StateCard(talk, handsOn, eyesOn, speaking)
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth(),
            state = list,
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {
            if (lines.isEmpty()) item { EmptyConversation() }
            items(lines) { Said(it) }
        }
        if (!typing) {
            Permissions(handsOn = handsOn, eyesOn = eyesOn, keyWaiting = talk?.keyWaiting == true)
            Spacer(Modifier.height(10.dp))
        }
        VoiceControl(
            handsFree = handsFree,
            hearing = hearing,
            ready = ready,
            speaking = speaking,
            onHandsFree = { Phone.handsFree(it) },
            onListen = { Phone.listen() },
        )
        Spacer(Modifier.height(6.dp))
        SayRow(
            typed = typed,
            onTyped = { typed = it },
            canSend = typed.isNotBlank() && talk?.link == Link.Here,
            onSend = { Phone.say(typed); typed = "" },
            onFocus = { typing = it },
        )
    }
}

@Composable
private fun Header(name: String?, forget: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            name ?: tr("heare"),
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.onBackground,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = forget, modifier = Modifier.heightIn(min = 48.dp)) {
            Text(tr("Unpair"), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun StateCard(talk: Talk?, handsOn: Boolean, eyesOn: Boolean, speaking: Boolean) {
    val hearing = talk?.hearing ?: Hearing.Idle
    Surface(
        Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StateChip(linkWords(talk), linkColor(talk?.link))
                if (speaking) {
                    StateChip(tr("Speaking…"), heareAccent)
                } else {
                    StateChip(hearingWords(hearing), hearingColor(hearing))
                }
            }
            if (talk?.keyWaiting == true) {
                Spacer(Modifier.height(8.dp))
                StateChip(tr("Voice is waiting for its key"), heareWarn, Modifier.fillMaxWidth())
            }
            Spacer(Modifier.height(10.dp))
            PartRow(tr("Voice"), tr("hears and answers"), true)
            PartRow(tr("Hands"), tr("uses the screen"), handsOn)
            PartRow(tr("Eyes"), tr("sees notifications"), eyesOn)
        }
    }
}

@Composable
private fun StateChip(words: String, color: Color, modifier: Modifier = Modifier) {
    Surface(modifier, shape = MaterialTheme.shapes.small, color = color.copy(alpha = 0.14f)) {
        Row(
            Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Dot(color)
            Text(words, style = MaterialTheme.typography.labelMedium, color = color)
        }
    }
}

@Composable
private fun Dot(color: Color, size: Dp = 8.dp) {
    Box(Modifier.size(size).background(color, CircleShape))
}

@Composable
private fun PartRow(name: String, what: String, on: Boolean) {
    val color = if (on) heareGood else MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Dot(color)
        Spacer(Modifier.width(8.dp))
        Text(name, style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.width(6.dp))
        Text(what, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
        Text(if (on) tr("On") else tr("Off"),
            style = MaterialTheme.typography.labelSmall, color = color)
    }
}

@Composable
private fun Permissions(handsOn: Boolean, eyesOn: Boolean, keyWaiting: Boolean) {
    val context = LocalContext.current
    val allGood = handsOn && eyesOn && !keyWaiting
    Surface(
        Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.surfaceVariant,
        border = if (allGood) null else BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        if (allGood) {
            Row(
                Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Dot(heareGood)
                Text(tr("The brain can see and do everything here."),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            Column(
                Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(tr("So the brain can see and do"),
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.onSurface)
                if (!handsOn) {
                    PermissionRow(
                        tr("Hands need Accessibility to use the screen."),
                        tr("Open"),
                    ) { context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
                }
                if (!eyesOn) {
                    PermissionRow(
                        tr("Eyes need Notification access to see what arrives."),
                        tr("Open"),
                    ) { context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
                }
                if (keyWaiting) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Dot(heareWarn, 6.dp)
                        Spacer(Modifier.width(8.dp))
                        Text(tr("Voice waits for the recogniser's key on the pult."),
                            style = MaterialTheme.typography.bodySmall, color = heareWarn)
                    }
                }
            }
        }
    }
}

@Composable
private fun PermissionRow(words: String, action: String, onAction: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(words, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        TextButton(onClick = onAction, modifier = Modifier.heightIn(min = 48.dp)) {
            Text(action)
        }
    }
}

@Composable
private fun VoiceControl(
    handsFree: Boolean,
    hearing: Hearing,
    ready: Boolean,
    speaking: Boolean,
    onHandsFree: (Boolean) -> Unit,
    onListen: () -> Unit,
) {
    if (handsFree) {
        HandsFreeControl(hearing, ready, speaking, onHandsFree)
    } else {
        Column {
            Button(
                onClick = onListen,
                enabled = ready && hearing != Hearing.Recognizing && !speaking,
                modifier = Modifier.fillMaxWidth().height(64.dp),
                colors = if (hearing == Hearing.Listening) ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error) else ButtonDefaults.buttonColors(),
            ) {
                Text(speakWords(hearing), style = MaterialTheme.typography.titleMedium)
            }
            Row(
                Modifier.fillMaxWidth().heightIn(min = 48.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(tr("Listen hands-free"), Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Switch(checked = false, onCheckedChange = { onHandsFree(true) })
            }
        }
    }
}

@Composable
private fun HandsFreeControl(
    hearing: Hearing,
    ready: Boolean,
    speaking: Boolean,
    onHandsFree: (Boolean) -> Unit,
) {
    val color = if (speaking || (ready && hearing != Hearing.Recognizing)) heareAccent else heareWarn
    val words = when {
        speaking -> tr("Speaking…")
        hearing == Hearing.Recognizing -> tr("Recognising…")
        ready -> tr("Listening to the room. Call me by name.")
        else -> tr("Waiting for the brain…")
    }
    Surface(
        Modifier.fillMaxWidth().heightIn(min = 112.dp),
        shape = MaterialTheme.shapes.large,
        color = heareAccent.copy(alpha = 0.10f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.5f)),
    ) {
        Column(
            Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Dot(color, 10.dp)
                Text(words, style = MaterialTheme.typography.titleMedium,
                    color = color, textAlign = TextAlign.Center)
            }
            Spacer(Modifier.height(2.dp))
            Text(tr("Hands-free is on"), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { onHandsFree(false) }, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(tr("Turn off hands-free"))
            }
        }
    }
}

@Composable
private fun SayRow(
    typed: String,
    onTyped: (String) -> Unit,
    canSend: Boolean,
    onSend: () -> Unit,
    onFocus: (Boolean) -> Unit,
) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Bottom) {
        OutlinedTextField(
            typed, onTyped,
            Modifier.weight(1f).onFocusChanged { onFocus(it.isFocused) },
            placeholder = { Text(tr("Say something…")) },
            maxLines = 4,
            shape = MaterialTheme.shapes.large,
        )
        Spacer(Modifier.width(4.dp))
        TextButton(
            enabled = canSend,
            onClick = onSend,
            modifier = Modifier.heightIn(min = 48.dp),
        ) { Text(tr("Send")) }
    }
}

@Composable
private fun Said(line: Line) {
    val mine = line.who == Who.Person
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Column(
            Modifier.widthIn(max = 320.dp),
            horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
        ) {
            Text(
                if (mine) tr("You") else tr("heare"),
                style = MaterialTheme.typography.labelSmall,
                color = if (line.failed) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
            Spacer(Modifier.height(2.dp))
            val bubble = when {
                line.failed -> MaterialTheme.colorScheme.errorContainer
                mine -> MaterialTheme.colorScheme.primaryContainer
                else -> MaterialTheme.colorScheme.surfaceVariant
            }
            val ink = when {
                line.failed -> MaterialTheme.colorScheme.onErrorContainer
                mine -> MaterialTheme.colorScheme.onPrimaryContainer
                else -> MaterialTheme.colorScheme.onSurface
            }
            Surface(color = bubble, shape = MaterialTheme.shapes.medium) {
                Text(line.text, Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                    color = ink, style = MaterialTheme.typography.bodyLarge)
            }
        }
    }
}

@Composable
private fun EmptyConversation() {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(tr("Nothing said yet."), style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(tr("Speak, type, or turn on hands-free."),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun linkWords(talk: Talk?): String = when (talk?.link) {
    Link.Here -> tr("Connected")
    Link.Knocking, null -> tr("Connecting…")
    Link.Away -> tr("No connection")
    Link.Unpaired -> tr("Unpaired")
}

private fun linkColor(link: Link?): Color = when (link) {
    Link.Here -> heareGood
    Link.Knocking, null -> heareWarn
    Link.Away, Link.Unpaired -> heareBad
}

private fun hearingWords(hearing: Hearing): String = when (hearing) {
    Hearing.Idle -> tr("Quiet")
    Hearing.Listening -> tr("Hearing you")
    Hearing.Recognizing -> tr("Recognising…")
}

private fun hearingColor(hearing: Hearing): Color = when (hearing) {
    Hearing.Idle -> heareMuted
    Hearing.Listening -> heareAccent
    Hearing.Recognizing -> heareWarn
}

private fun speakWords(hearing: Hearing): String = when (hearing) {
    Hearing.Idle -> tr("Speak")
    Hearing.Listening -> tr("Listening… tap to finish")
    Hearing.Recognizing -> tr("Recognising…")
}
