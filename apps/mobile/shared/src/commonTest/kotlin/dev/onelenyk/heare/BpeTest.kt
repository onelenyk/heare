package dev.onelenyk.heare

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class BpeTest {
    // A slice of the model's tokens.txt: enough to spell the names.
    private val bpe = Bpe("<blk> 0\n▁HE 1\nY 2\n▁DO 3\nKA 4\nKO 5\n▁HEAR 6\nE 7\nK 8\nA 9\nO 10\n")

    @Test
    fun a_name_is_spelled_in_the_longest_pieces() {
        assertEquals("▁DO KA", bpe.pieces("Doka"))
        assertEquals("▁HE Y ▁HEAR E", bpe.pieces("hey heare"))
    }

    @Test
    fun a_letter_the_model_lacks_is_no_phrase() {
        assertNull(bpe.pieces("дока"))
    }

    @Test
    fun the_name_sounds_in_latin_with_its_vocative() {
        assertEquals(listOf("doka", "doko", "hey heare"), Wake("Дока").sounds)
    }
}
