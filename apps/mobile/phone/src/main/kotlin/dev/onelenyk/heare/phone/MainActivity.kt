package dev.onelenyk.heare.phone

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
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
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(Modifier.fillMaxSize()) {
                    val talk by Phone.talk.collectAsState()
                    var paired by remember { mutableStateOf(Phone.paired) }
                    if (!paired) {
                        Pairing { paired = true; startEars() }
                    } else {
                        Conversation(talk) {
                            Ears.stop(this)
                            Phone.forget()
                            paired = false
                        }
                    }
                }
            }
        }
        if (Phone.paired) startEars()
    }

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
        Modifier.fillMaxSize().safeDrawingPadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("heare", style = MaterialTheme.typography.headlineLarge)
        Text(tr("On the pult, take a pairing code and type it here."))
        OutlinedTextField(address, { address = it }, Modifier.fillMaxWidth(),
            label = { Text(tr("Brain address")) }, singleLine = true)
        OutlinedTextField(code, { code = it }, Modifier.fillMaxWidth(),
            label = { Text(tr("Code")) }, singleLine = true)
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
        ) { Text(tr("Pair")) }
    }
}

@Composable
private fun Conversation(talk: Talk?, forget: () -> Unit) {
    var typed by remember { mutableStateOf("") }
    val list = rememberLazyListState()
    val lines = talk?.lines.orEmpty()
    LaunchedEffect(lines.size, lines.lastOrNull()?.text) {
        if (lines.isNotEmpty()) list.animateScrollToItem(lines.size - 1)
    }
    Column(Modifier.fillMaxSize().safeDrawingPadding().imePadding().padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(talk?.name ?: "heare", style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.weight(1f))
            Text(linkWords(talk), color = linkColor(talk?.link))
            TextButton(onClick = forget) { Text(tr("Unpair")) }
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list,
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(lines) { Said(it) }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(typed, { typed = it }, Modifier.weight(1f),
                placeholder = { Text(tr("Say something…")) })
            TextButton(
                enabled = typed.isNotBlank() && talk?.link == Link.Here,
                onClick = { Phone.say(typed); typed = "" },
            ) { Text(tr("Send")) }
        }
    }
}

@Composable
private fun Said(line: Line) {
    val mine = line.who == Who.Person
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Surface(
            color = if (mine) MaterialTheme.colorScheme.primaryContainer
            else MaterialTheme.colorScheme.surfaceVariant,
            shape = MaterialTheme.shapes.medium,
        ) {
            Text(line.text, Modifier.padding(12.dp),
                color = if (line.failed) MaterialTheme.colorScheme.error else Color.Unspecified)
        }
    }
}

private fun linkWords(talk: Talk?): String = when (talk?.link) {
    Link.Here -> tr("connected")
    Link.Knocking, null -> tr("connecting…")
    Link.Away -> tr("no connection")
    Link.Unpaired -> tr("the brain forgot this phone")
}

private fun linkColor(link: Link?): Color = when (link) {
    Link.Here -> Color(0xFF7BD88F)
    Link.Away, Link.Unpaired -> Color(0xFFFF8A80)
    else -> Color(0xFFB0B0B0)
}
