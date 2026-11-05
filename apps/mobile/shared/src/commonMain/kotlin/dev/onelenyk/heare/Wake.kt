package dev.onelenyk.heare

/**
 * Was this said to the assistant? The Mac's rule (heare-voice
 * wake_phrases.py), carried over: an always-on ear that answers everything
 * answers the room. Whole words, any case, over the recogniser's words — so
 * the list holds what Whisper writes for the name, not only how it is spelled.
 */
class Wake(name: String?) {
    private val words: List<String> = buildList {
        val key = name?.trim()?.lowercase().orEmpty()
        if (key.isNotEmpty()) {
            val written = mutableListOf(key)
            if (key.all { it.code < 128 }) written += key.map { LATIN[it] ?: it.toString() }.joinToString("")
            else written += key.map { CYRILLIC[it] ?: it.toString() }.joinToString("")
            for (spelling in written.distinct()) {
                add(spelling)
                // The vocative: you call «Доко», not «Дока».
                if (spelling.length > 2 && spelling.endsWith("а")) add(spelling.dropLast(1) + "о")
            }
            addAll(MISHEARD[key].orEmpty())
        }
        add("heare")
    }.distinct()

    fun addressed(text: String): Boolean {
        val said = text.lowercase().split(Regex("[^\\p{L}\\p{N}']+")).filter { it.isNotEmpty() }
        return words.any { word ->
            val parts = word.split(" ")
            said.windowed(parts.size).any { it == parts }
        }
    }

    private companion object {
        val MISHEARD = mapOf(
            "дока" to listOf("доку", "дока́", "докер", "доко"),
            "doka" to listOf("доку", "дока́", "докер", "доко"),
            "гава" to listOf("гаво", "гави", "гаваа"),
        )
        val LATIN = mapOf(
            'a' to "а", 'b' to "б", 'c' to "к", 'd' to "д", 'e' to "е", 'f' to "ф", 'g' to "г",
            'h' to "г", 'i' to "і", 'j' to "й", 'k' to "к", 'l' to "л", 'm' to "м", 'n' to "н",
            'o' to "о", 'p' to "п", 'r' to "р", 's' to "с", 't' to "т", 'u' to "у", 'v' to "в",
            'y' to "и", 'z' to "з",
        )
        val CYRILLIC = mapOf(
            'а' to "a", 'б' to "b", 'в' to "v", 'г' to "g", 'д' to "d", 'е' to "e", 'ф' to "f",
            'и' to "y", 'і' to "i", 'й' to "j", 'к' to "k", 'л' to "l", 'м' to "m", 'н' to "n",
            'о' to "o", 'п' to "p", 'р' to "r", 'с' to "s", 'т' to "t", 'у' to "u", 'з' to "z",
        )
    }
}
