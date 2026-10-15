package heare.channels

import heare.core.Input
import heare.core.Message
import heare.core.Output
import heare.core.Port
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext

/** Talking by typing: a line in is a turn, the reply is printed as it comes. */
class Terminal : Input, Output {
    override val name = "text"

    override suspend fun run(port: Port) = withContext(Dispatchers.IO) {
        while (true) {
            val line = readlnOrNull() ?: break
            if (line.isNotBlank()) port.heard(Message(line.trim(), name))
        }
    }

    override suspend fun say(pieces: Flow<String>) {
        try {
            pieces.collect { piece ->
                print(piece)
                System.out.flush()
            }
        } finally {
            println()
        }
    }
}
