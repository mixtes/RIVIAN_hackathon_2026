package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.model.Region
import com.hackathon.drivescore.core.network.DriveScoreApi
import com.hackathon.drivescore.core.network.apiCall
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale

/** Regions + mistake labels (spec §6.10, §12). Falls back to bundled data when offline. */
class MetaRepository(private val api: DriveScoreApi) {
    private val _labels = MutableStateFlow<Map<String, String>>(emptyMap())
    /** Server labels for mistake types (used for types the app doesn't know). */
    val labels: StateFlow<Map<String, String>> = _labels.asStateFlow()

    suspend fun regions(): List<Region> =
        apiCall { api.regions() }.getOrNull()?.map { Region(it.code, it.name) }?.takeIf { it.isNotEmpty() }
            ?: bundledRegions()

    suspend fun refreshMistakeTypes() {
        apiCall { api.mistakeTypes() }.onSuccess { list -> _labels.value = list.associate { it.type to it.label } }
    }

    companion object {
        /**
         * Bundled fallback: the platform's ISO 3166-1 alpha-2 list with localized names (always available
         * offline, same codes the backend validates with pycountry).
         */
        fun bundledRegions(locale: Locale = Locale.getDefault()): List<Region> =
            Locale.getISOCountries().map { Region(it, Locale.Builder().setRegion(it).build().getDisplayCountry(locale)) }
                .sortedBy { it.name }

        fun defaultRegion(): String = Locale.getDefault().country.takeIf { it.length == 2 } ?: "US"
    }
}
