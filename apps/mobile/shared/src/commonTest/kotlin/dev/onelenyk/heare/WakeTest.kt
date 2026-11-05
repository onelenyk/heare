package dev.onelenyk.heare

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The Mac's rule (wake_phrases.py), the same on the phone. */
class WakeTest {
    private val doka = Wake("Дока")

    @Test
    fun the_name_and_how_it_is_called_and_misheard() {
        assertTrue(doka.addressed("Дока, яка погода?"))
        assertTrue(doka.addressed("доко нагадай мені"))
        assertTrue(doka.addressed("Докер, що там"))
        assertTrue(doka.addressed("Doka, hi"))
        assertTrue(doka.addressed("hey heare"))
    }

    @Test
    fun the_room_is_not_addressed() {
        assertFalse(doka.addressed("документи лежать на столі"))
        assertFalse(doka.addressed("Дякую"))
        assertFalse(Wake(null).addressed("що там"))
    }
}
