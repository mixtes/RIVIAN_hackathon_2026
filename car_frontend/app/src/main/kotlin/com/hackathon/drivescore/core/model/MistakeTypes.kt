package com.hackathon.drivescore.core.model

/**
 * Registry of the 8 known mistake types (spec §6.5, §8.5, §8.6, backend §5.2).
 * Unknown types are still accepted and shown with their raw name, title-cased.
 */
data class MistakeTypeInfo(
    val type: String,
    val label: String,
    val icon: String,
    val hasDuration: Boolean,
    val defaultAlertText: String,
)

object MistakeTypes {
    val TYPE_PATTERN = Regex("^[A-Z][A-Z0-9_]{1,39}$")

    val known: List<MistakeTypeInfo> = listOf(
        MistakeTypeInfo("EYES_OFF_ROAD", "Eyes off road", "👁", true, "Eyes on the road"),
        MistakeTypeInfo("PHONE_USE", "Phone use", "📱", true, "Put the phone down"),
        MistakeTypeInfo("EYES_CLOSED", "Eyes closed", "😴", true, "Wake up! Eyes closed"),
        MistakeTypeInfo("YAWN", "Yawns", "🥱", false, "You seem tired. Consider a break"),
        MistakeTypeInfo("HANDS_OFF_WHEEL", "Hands off wheel", "✋", true, "Hands on the wheel"),
        MistakeTypeInfo("PHONE_CALL", "Phone call", "📞", true, "Use hands-free for calls"),
        MistakeTypeInfo("EATING_DRINKING", "Eating / drinking", "🍔", true, "Focus on driving"),
        MistakeTypeInfo("SMOKING", "Smoking", "🚬", true, "Focus on driving"),
    )

    private val byType = known.associateBy { it.type }

    fun normalize(raw: String): String = raw.trim().uppercase()

    fun isValid(type: String): Boolean = TYPE_PATTERN.matches(type)

    fun info(type: String): MistakeTypeInfo {
        val t = normalize(type)
        byType[t]?.let { return it }
        if (t == "DROWSINESS") return MistakeTypeInfo(t, "Drowsiness", "☕", false, "You seem tired. Consider a break")
        return MistakeTypeInfo(t, titleCase(t), "⚠️", true, "Drive carefully")
    }

    fun titleCase(type: String): String =
        type.lowercase().split('_').filter { it.isNotEmpty() }
            .joinToString(" ").replaceFirstChar { it.uppercase() }

    /** Alert text: the engine's message as-is, the default only if missing/blank (spec §8.6). */
    fun alertText(type: String, message: String?): String =
        message?.takeIf { it.isNotBlank() } ?: info(type).defaultAlertText

    /**
     * Rows for the details table: all 8 known types (zeros included), then unknown types (spec §6.5).
     */
    fun detailRows(mistakes: List<Mistake>): List<Pair<MistakeTypeInfo, Mistake?>> {
        val byT = mistakes.associateBy { normalize(it.type) }
        val knownRows = known.map { it to byT[it.type] }
        val unknown = mistakes.filter { normalize(it.type) !in byType }
            .map { info(it.type) to it }
        return knownRows + unknown
    }
}
