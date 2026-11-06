package com.hackathon.drivescore.feature.lock

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hackathon.drivescore.R
import com.hackathon.drivescore.core.designsystem.Avatar
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.CarDimens
import com.hackathon.drivescore.core.designsystem.CarShapes
import com.hackathon.drivescore.core.designsystem.GhostButton
import com.hackathon.drivescore.core.designsystem.SectionCard
import com.hackathon.drivescore.core.designsystem.Space
import com.hackathon.drivescore.core.designsystem.Keypad
import com.hackathon.drivescore.core.designsystem.PinDots
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.SecondaryButton
import com.hackathon.drivescore.core.designsystem.VSpace
import kotlinx.coroutines.withTimeoutOrNull

private fun mmss(ms: Long): String {
    val s = (ms + 999) / 1000
    return "%d:%02d".format(s / 60, s % 60)
}

/** Driver Select / Lock screen (spec §6.1). */
@Composable
fun DriverSelectScreen(
    carName: String?,
    onSelect: (String) -> Unit,
    onAddDriver: () -> Unit,
    onSignInAgain: (String) -> Unit,
    onDebug: () -> Unit,
    vm: LockViewModel = hiltViewModel(),
) {
    val s by vm.state.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize().padding(CarDimens.ScreenPadding)) {
        Text(stringResource(R.string.who_is_driving), style = MaterialTheme.typography.headlineLarge)
        VSpace(Space.XL)
        if (s.loaded && s.tiles.isEmpty()) {
            Column(Modifier.weight(1f).fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                Text(stringResource(R.string.no_drivers_yet), style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
                VSpace(Space.L)
                PrimaryButton(stringResource(R.string.add_driver), onAddDriver, enabled = s.canAdd, modifier = Modifier.width(360.dp))
                if (!s.canAdd) {
                    VSpace(Space.S)
                    Text(addHint(s), color = CarColors.Muted, style = MaterialTheme.typography.bodyMedium)
                }
            }
        } else {
            LazyVerticalGrid(
                GridCells.Adaptive(CarDimens.ProfileTileWidth), Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(Space.L), horizontalArrangement = Arrangement.spacedBy(Space.L),
            ) {
                items(s.tiles, key = { it.driver.userId }) { t ->
                    ProfileTile(t, onClick = {
                        when {
                            t.pinDisabled -> onSignInAgain(t.driver.username)
                            t.lockedOutMs == null -> onSelect(t.driver.userId)
                        }
                    }, onSignInAgain = { onSignInAgain(t.driver.username) })
                }
                item {
                    AddTile(enabled = s.canAdd, hint = if (s.canAdd) null else addHint(s), onClick = onAddDriver)
                }
            }
        }
        // Footer: car name; long-press 5 s → debug settings (spec §6.1).
        Text(
            carName ?: stringResource(R.string.car_not_registered),
            style = MaterialTheme.typography.bodySmall, color = CarColors.Muted,
            modifier = Modifier.padding(top = Space.M).pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown()
                    val released = withTimeoutOrNull(5_000) { waitForUpOrCancellation() }
                    if (released == null) onDebug()
                }
            },
        )
    }
}

@Composable
private fun addHint(s: LockUiState): String = when {
    s.full -> stringResource(R.string.car_full)
    !s.online -> stringResource(R.string.add_driver_offline)
    !s.carRegistered -> stringResource(R.string.car_registering)
    else -> ""
}

private val TileAvatar = 112.dp

/** Profile and "Add driver" tiles share one fixed size so the grid stays even. */
@Composable
private fun ProfileTile(t: DriverTile, onClick: () -> Unit, onSignInAgain: () -> Unit) {
    SectionCard(Modifier.fillMaxWidth().height(CarDimens.ProfileTileHeight), onClick = onClick) {
        Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            Box {
                Avatar(t.driver.initials, t.driver.avatarColor, TileAvatar, Modifier.alpha(if (t.lockedOutMs != null || t.pinDisabled) 0.4f else 1f))
                if (t.driver.hasPendingUploads) {
                    Box(
                        Modifier.align(Alignment.TopEnd).size(36.dp).clip(CircleShape).background(CarColors.Amber)
                            .border(3.dp, CarColors.Surface, CircleShape),
                        contentAlignment = Alignment.Center,
                    ) { Text("⇡", fontSize = 20.sp, color = CarColors.BackgroundDeep) }
                }
            }
            VSpace(Space.M)
            Text(t.driver.displayName, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center, maxLines = 1, overflow = TextOverflow.Ellipsis)
            when {
                t.pinDisabled -> Text(stringResource(R.string.sign_in_with_password), color = CarColors.Amber, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
                t.lockedOutMs != null -> Text(stringResource(R.string.try_again_in, mmss(t.lockedOutMs)), color = CarColors.Amber, style = MaterialTheme.typography.bodySmall)
                t.driver.needsReauth -> TextButton(onClick = onSignInAgain) {
                    Text(stringResource(R.string.sign_in_again_to_upload), color = CarColors.Amber, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
                }
            }
        }
    }
}

@Composable
private fun AddTile(enabled: Boolean, hint: String?, onClick: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().height(CarDimens.ProfileTileHeight).clip(CarShapes.Card)
            .border(2.dp, CarColors.Outline, CarShapes.Card)
            .clickable(enabled = enabled, onClick = onClick).padding(CarDimens.CardPadding).alpha(if (enabled) 1f else 0.5f),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
    ) {
        Box(
            Modifier.size(TileAvatar).clip(CircleShape).background(CarColors.SurfaceHigh)
                .border(2.dp, CarColors.Orange, CircleShape),
            contentAlignment = Alignment.Center,
        ) { Text("+", fontSize = 64.sp, color = CarColors.Orange) }
        VSpace(Space.M)
        Text(stringResource(R.string.add_driver), style = MaterialTheme.typography.titleMedium)
        if (hint != null) Text(hint, color = CarColors.Muted, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
    }
}

/** PIN entry (spec §6.2): auto-submit, shake on error, lockout countdown, Forgot PIN. */
@Composable
fun PinEntryScreen(userId: String, onCancel: () -> Unit, onForgotPin: (String) -> Unit, vm: PinViewModel = hiltViewModel()) {
    LaunchedEffect(userId) { vm.load(userId) }
    val s by vm.state.collectAsStateWithLifecycle()
    val shake = remember { Animatable(0f) }
    val haptics = LocalHapticFeedback.current
    LaunchedEffect(s.shakeKey) {
        if (s.shakeKey > 0) {
            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
            for (x in listOf(30f, -30f, 20f, -20f, 10f, 0f)) shake.animateTo(x, tween(50))
        }
    }
    Row(
        Modifier.fillMaxSize().background(CarColors.backgroundBrush).padding(CarDimens.ScreenPadding),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.XXL),
    ) {
        Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
            s.driver?.let {
                Avatar(it.initials, it.avatarColor, 140.dp)
                VSpace(Space.M)
                Text(it.displayName, style = MaterialTheme.typography.headlineMedium)
            }
            VSpace(Space.XL)
            PinDots(s.pinLength, s.entered.length, Modifier.offset { IntOffset(shake.value.toInt(), 0) })
            VSpace(Space.L)
            val msg = when {
                s.pinDisabled -> stringResource(R.string.pin_disabled)
                s.lockedOutMs != null -> stringResource(R.string.locked_try_again_in, mmss(s.lockedOutMs!!))
                s.attemptsLeft != null -> stringResource(R.string.wrong_pin_attempts_left, s.attemptsLeft!!)
                else -> stringResource(R.string.enter_pin)
            }
            Text(msg, style = MaterialTheme.typography.titleMedium, color = if (s.attemptsLeft != null || s.lockedOutMs != null || s.pinDisabled) CarColors.Amber else CarColors.Muted, textAlign = TextAlign.Center)
            VSpace(Space.XL)
            Row(horizontalArrangement = Arrangement.spacedBy(Space.M)) {
                SecondaryButton(stringResource(R.string.cancel), onCancel)
                if (s.pinDisabled) PrimaryButton(stringResource(R.string.sign_in_with_password), { s.driver?.let { onForgotPin(it.username) } })
                else GhostButton(stringResource(R.string.forgot_pin), { s.driver?.let { onForgotPin(it.username) } })
            }
        }
        Keypad(onDigit = vm::digit, onBackspace = vm::backspace, enabled = !s.busy && s.lockedOutMs == null && !s.pinDisabled)
    }
}
