package heare.core

import kotlinx.coroutines.flow.Flow

/**
 * The kinds of part, and exactly what the core asks of each.
 *
 * Everything that touches the world is one of these. The core knows the
 * forms and nothing behind them — not that one input is a keyboard, not
 * which company's model is answering.
 *
 * A channel (text, voice) is not another kind: it is one object that is
 * an input, an output, or both.
 */

/** One finished thing said to the assistant. */
data class Message(
    val text: String,
    /** Where it came from; the reply goes back there. */
    val channel: String,
)

/** One side of one turn, as the model is shown it. */
data class Turn(val role: String, val text: String)

/** What the core hands an input: the one thing it may report. */
fun interface Port {
    /** The person finished a turn. */
    fun heard(message: Message)
}

interface Input {
    val name: String

    /**
     * Deliver messages until there are no more. When a turn is over is
     * the input's own business: Enter for text, silence for a voice.
     */
    suspend fun run(port: Port)
}

interface Output {
    val name: String

    /**
     * Deliver a reply as it is written. There is no `stop()`: being
     * cancelled is how it is told to stop, and it must stop.
     */
    suspend fun say(pieces: Flow<String>)
}

interface Model {
    /** The reply, piece by piece, as it is written. */
    fun reply(history: List<Turn>): Flow<String>
}
