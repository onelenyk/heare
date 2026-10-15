package heare.core

import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.launch

/**
 * The conversation. It works with messages and reads or writes nothing
 * itself: what it hears comes from inputs, what it says goes to outputs.
 */
class Core(private val model: Model) {
    private val inputs = mutableListOf<Input>()
    private val outputs = mutableMapOf<String, Output>()
    private val inbox = Channel<Message>(Channel.UNLIMITED)
    private val history = mutableListOf<Turn>()

    fun addInput(part: Input) {
        inputs += part
    }

    fun addOutput(part: Output) {
        outputs[part.name] = part
    }

    /** Listen and answer until every input has nothing more to say. */
    suspend fun run(): Unit = coroutineScope {
        val port = Port { message -> inbox.trySend(message) }
        launch {
            coroutineScope { inputs.forEach { input -> launch { input.run(port) } } }
            inbox.close()
        }

        var answering: Job? = null
        for (message in inbox) {
            // The person spoke: whatever was being said stops here.
            answering?.cancelAndJoin()
            answering = launch { answer(message) }
        }
        answering?.join()
    }

    private suspend fun answer(message: Message) {
        val out = outputs[message.channel] ?: return
        history += Turn("user", message.text)
        val said = StringBuilder()
        try {
            out.say(model.reply(history.toList()).onEach { piece -> said.append(piece) })
        } finally {
            // What was cut short was still said, as far as it got.
            if (said.isNotEmpty()) history += Turn("assistant", said.toString().trim())
        }
    }
}
