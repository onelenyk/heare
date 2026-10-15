package heare

import heare.channels.Terminal
import heare.core.Core
import heare.model.Parrot
import kotlinx.coroutines.runBlocking

/**
 * Assembly: the one file that knows every part.
 *
 * Everything else knows the core and itself.
 */
fun main(): Unit = runBlocking {
    val core = Core(Parrot())

    val terminal = Terminal()
    core.addInput(terminal)
    core.addOutput(terminal)

    core.run()
}
