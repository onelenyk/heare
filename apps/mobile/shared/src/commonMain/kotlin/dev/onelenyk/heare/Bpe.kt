package dev.onelenyk.heare

/**
 * A wake phrase in the keyword model's own pieces: the English BPE of
 * sherpa-onnx's gigaspeech model ("▁DO KA"), greedy longest match over its
 * tokens.txt, as bringalive's spotter did it. A phrase with a letter the
 * model does not know cannot be spotted, and says so with null.
 */
class Bpe(tokens: String) {
    private val known: Set<String> = tokens.lineSequence()
        .mapNotNull { it.trim().split(" ").firstOrNull()?.takeIf(String::isNotEmpty) }
        .toSet()

    fun pieces(phrase: String): String? {
        val out = mutableListOf<String>()
        for (word in phrase.trim().uppercase().split(Regex("\\s+")).filter { it.isNotEmpty() }) {
            val marked = "▁$word"
            var at = 0
            while (at < marked.length) {
                val piece = (marked.length downTo at + 1)
                    .map { marked.substring(at, it) }
                    .firstOrNull { it in known } ?: return null
                out += piece
                at += piece.length
            }
        }
        return out.joinToString(" ").ifEmpty { null }
    }
}
