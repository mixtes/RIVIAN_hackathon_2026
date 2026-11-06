package com.hackathon.drivescore.feature.stats

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.hackathon.drivescore.R
import com.hackathon.drivescore.core.designsystem.SelectChip
import com.hackathon.drivescore.core.designsystem.Space
import com.hackathon.drivescore.core.model.Period

@Composable
fun PeriodTabs(selected: Period, onSelect: (Period) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(Space.S)) {
        Period.entries.forEach { p ->
            SelectChip(
                stringResource(
                    when (p) {
                        Period.DAY -> R.string.period_day
                        Period.WEEK -> R.string.period_week
                        Period.MONTH -> R.string.period_month
                        Period.YEAR -> R.string.period_year
                        Period.ALL -> R.string.period_all
                    },
                ),
                selected = p == selected, onClick = { onSelect(p) },
            )
        }
    }
}
