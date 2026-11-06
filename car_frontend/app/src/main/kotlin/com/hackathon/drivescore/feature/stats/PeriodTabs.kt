package com.hackathon.drivescore.feature.stats

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.unit.dp
import com.hackathon.drivescore.R
import com.hackathon.drivescore.core.model.Period

@Composable
fun PeriodTabs(selected: Period, onSelect: (Period) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Period.entries.forEach { p ->
            FilterChip(
                selected = p == selected, onClick = { onSelect(p) },
                label = {
                    Text(
                        stringResource(
                            when (p) {
                                Period.DAY -> R.string.period_day
                                Period.WEEK -> R.string.period_week
                                Period.MONTH -> R.string.period_month
                                Period.YEAR -> R.string.period_year
                                Period.ALL -> R.string.period_all
                            },
                        ),
                        style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(8.dp),
                    )
                },
                modifier = Modifier.heightIn(min = 64.dp),
            )
        }
    }
}
