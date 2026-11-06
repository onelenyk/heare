package dev.onelenyk.heare.phone

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** heare's own colours: near-black ink and the lilac accent (README badges). */
internal val heareInk = Color(0xFF131217)
internal val heareRaised = Color(0xFF1B1921)
internal val heareHigh = Color(0xFF232030)
internal val heareAccent = Color(0xFF9D7CFF)
internal val heareOnAccent = Color(0xFF17102B)
internal val heareText = Color(0xFFEDEAF4)
internal val heareMuted = Color(0xFFA9A3B8)
internal val heareGood = Color(0xFF7BD88F)
internal val heareWarn = Color(0xFFF2C14E)
internal val heareBad = Color(0xFFFF8A80)

private val HeareScheme = darkColorScheme(
    primary = heareAccent,
    onPrimary = heareOnAccent,
    primaryContainer = Color(0xFF3B2A69),
    onPrimaryContainer = Color(0xFFEDE6FF),
    inversePrimary = Color(0xFF5B3FC4),
    secondary = Color(0xFFB9B0D0),
    onSecondary = Color(0xFF201A2E),
    secondaryContainer = Color(0xFF2A2438),
    onSecondaryContainer = Color(0xFFE7E0F5),
    tertiary = heareWarn,
    onTertiary = Color(0xFF241B00),
    tertiaryContainer = Color(0xFF3D2F12),
    onTertiaryContainer = Color(0xFFFFE0A3),
    background = heareInk,
    onBackground = heareText,
    surface = heareInk,
    onSurface = heareText,
    surfaceVariant = heareRaised,
    onSurfaceVariant = heareMuted,
    surfaceTint = heareAccent,
    inverseSurface = heareText,
    inverseOnSurface = heareInk,
    error = heareBad,
    onError = Color(0xFF3A0B08),
    errorContainer = Color(0xFF4E1A17),
    onErrorContainer = Color(0xFFFFD9D5),
    outline = Color(0xFF4A4555),
    outlineVariant = Color(0xFF2E2A38),
    scrim = Color(0xFF000000),
)

@Composable
internal fun HeareTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = HeareScheme, content = content)
}
