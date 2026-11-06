package dev.onelenyk.heare

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The brain drops a skill that breaks protocol §6 (heare/api/skills.py
 * `checked`) without saying so to the phone: these are its rules, here,
 * so a new skill that breaks one fails before it reaches the brain.
 */
class PhoneSkillsTest {
    @Test
    fun every_skill_is_one_the_brain_takes() {
        assertTrue(PHONE_SKILLS.size <= 32)
        assertEquals(PHONE_SKILLS.size, PHONE_SKILLS.map { it.name }.toSet().size, "names are unique")
        for (skill in PHONE_SKILLS) {
            assertTrue(Regex("[a-z][a-z0-9_]{0,31}").matches(skill.name), skill.name)
            assertTrue(skill.effect in setOf("read", "harmless", "change"), skill.name)
            assertTrue(skill.about.isNotBlank() && skill.about.all { it.code < 128 }, "about is English: ${skill.name}")
            assertEquals("object", skill.params["type"]?.jsonPrimitive?.content, skill.name)
            assertTrue(Json.encodeToString(JsonObject.serializer(), skill.params).length <= 2048, skill.name)
            val properties = skill.params["properties"] as JsonObject
            for (needed in (skill.params["required"] as? JsonArray).orEmpty()) {
                assertTrue(needed.jsonPrimitive.content in properties, "${skill.name} requires what it has")
            }
        }
    }

    @Test
    fun what_changes_the_phone_goes_past_the_judge() {
        val changes = PHONE_SKILLS.filter { it.effect == "change" }.map { it.name }.toSet()
        assertEquals(setOf("open_app", "tap", "type", "long_press", "tap_and_wait",
            "notification_reply", "notification_dismiss", "set_clipboard", "share"), changes)
        assertEquals(setOf("screen", "active_app", "apps", "notifications", "clipboard", "device"),
            PHONE_SKILLS.filter { it.effect == "read" }.map { it.name }.toSet())
        assertTrue(PHONE_SKILLS.single { it.name == "scroll_to_find" }.effect == "harmless")
    }

    @Test
    fun time_asked_for_is_a_time_the_brain_waits() {
        for (skill in PHONE_SKILLS) {
            val within = skill.within ?: continue
            assertTrue(within in 1..30, "${skill.name}: $within")
        }
        for (slow in listOf("wait", "tap_and_wait", "scroll_to_find")) {
            assertTrue(PHONE_SKILLS.single { it.name == slow }.within != null, "$slow says its time")
        }
    }

    @Test
    fun the_time_travels_in_the_knock_only_when_asked_for() {
        val wire = Json { encodeDefaults = true; explicitNulls = false }
        val slow = wire.encodeToString(Skill.serializer(), PHONE_SKILLS.single { it.name == "wait" })
        assertTrue("\"within\":25" in slow, slow)
        val quick = wire.encodeToString(Skill.serializer(), PHONE_SKILLS.single { it.name == "screen" })
        assertFalse("within" in quick, quick)
    }

    private val apps = listOf("Telegram" to "org.telegram.messenger", "Telegram X" to "org.thunderdog.challegram",
        "Google Maps" to "com.google.android.apps.maps", "Maps Go" to "com.google.android.apps.mapslite")

    @Test
    fun the_app_named_exactly_wins_over_one_that_holds_the_name() {
        assertEquals("org.telegram.messenger", pick(apps, "telegram")?.second)
        assertEquals("org.thunderdog.challegram", pick(apps, "Telegram X")?.second)
    }

    @Test
    fun a_package_or_part_of_a_label_finds_the_app() {
        assertEquals("Maps Go", pick(apps, "com.google.android.apps.mapslite")?.first)
        assertEquals("Google Maps", pick(apps, "maps")?.first)
    }

    @Test
    fun nothing_said_or_nothing_like_it_opens_nothing() {
        assertNull(pick(apps, "  "))
        assertNull(pick(apps, "Viber"))
    }
}
