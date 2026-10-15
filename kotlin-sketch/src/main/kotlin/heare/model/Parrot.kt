package heare.model

import heare.core.Model
import heare.core.Turn
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * A stand-in for a real model: no network, no key. It speaks slowly on
 * purpose, so that there is time to interrupt it.
 */
class Parrot : Model {
    override fun reply(history: List<Turn>): Flow<String> = flow {
        val heard = history.last().text
        val turns = history.count { it.role == "user" }
        val reply = "Ти сказав: «$heard». Це твоя репліка номер $turns. " +
            "Я підставна модель і говорю повільно, щоб мене можна було перебити."
        for (word in reply.split(" ")) {
            emit("$word ")
            delay(250)
        }
    }
}
