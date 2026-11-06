package com.hackathon.drivescore.app

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.EmojiEvents
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.hackathon.drivescore.R
import com.hackathon.drivescore.core.designsystem.AlertOverlay
import com.hackathon.drivescore.core.designsystem.Avatar
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.CarDimens
import com.hackathon.drivescore.core.designsystem.CarShapes
import com.hackathon.drivescore.core.designsystem.Space
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.designsystem.PulsingDot
import com.hackathon.drivescore.core.model.AppState
import com.hackathon.drivescore.core.model.ConnectionStatus
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.feature.adddriver.AddDriverScreen
import com.hackathon.drivescore.feature.debug.DebugScreen
import com.hackathon.drivescore.feature.drive.DriveScreen
import com.hackathon.drivescore.feature.history.HistoryScreen
import com.hackathon.drivescore.feature.leaderboard.LeaderboardScreen
import com.hackathon.drivescore.feature.lock.DriverSelectScreen
import com.hackathon.drivescore.feature.lock.PinEntryScreen
import com.hackathon.drivescore.feature.result.DetailsScreen
import com.hackathon.drivescore.feature.result.ResultScreen
import com.hackathon.drivescore.feature.settings.SettingsScreen
import com.hackathon.drivescore.feature.stats.StatsScreen
import com.hackathon.drivescore.service.SessionEvent

/** Gate UI by driving state (spec §7.3). */
val LocalDrivingState = staticCompositionLocalOf { DrivingState.PARKED }

object Routes {
    const val SELECT = "select"
    const val ADD_DRIVER = "adddriver?username={username}"
    fun addDriver(username: String? = null) = if (username == null) "adddriver" else "adddriver?username=$username"
    const val DEBUG = "debug"
    const val HOME = "home"
    const val DETAILS = "details/{driveId}"
    fun details(id: String) = "details/$id"
    const val HISTORY = "history"
    const val STATS = "stats"
    const val LEADERBOARD = "leaderboard"
    const val SETTINGS = "settings"

    /** Screens allowed while moving (spec §7.3). */
    val allowedWhileMoving = setOf(HOME, SELECT)
}

@Composable
fun AppRoot(vm: RootViewModel) {
    val state by vm.appState.collectAsStateWithLifecycle()
    val driving by vm.drivingState.collectAsStateWithLifecycle()
    val alert by vm.alert.collectAsStateWithLifecycle()
    val status by vm.statusBar.collectAsStateWithLifecycle()
    val carName by vm.carName.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val engineNotResponding = stringResource(R.string.engine_not_responding)
    val resultInvalid = stringResource(R.string.result_invalid)

    LaunchedEffect(Unit) {
        vm.events.collect { e ->
            when (e) {
                SessionEvent.EngineNotResponding -> snackbar.showSnackbar(engineNotResponding)
                is SessionEvent.ResultInvalid -> snackbar.showSnackbar(resultInvalid)
                else -> Unit
            }
        }
    }

    CompositionLocalProvider(LocalDrivingState provides driving, LocalContentColor provides CarColors.OnDark) {
        Box(Modifier.fillMaxSize().background(CarColors.backgroundBrush)) {
            Column(Modifier.fillMaxSize()) {
                StatusBar(status, driving)
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    when (val s = state) {
                        AppState.Locked, is AppState.PinEntry -> LockedArea(vm, s, carName)
                        else -> UnlockedArea(vm, s, driving)
                    }
                }
            }
            AlertOverlay(alert)
            SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(Space.L)) { data ->
                Snackbar(
                    data, shape = CarShapes.Control,
                    containerColor = CarColors.SurfaceHigh, contentColor = CarColors.OnDark, actionColor = CarColors.Orange,
                )
            }
        }
    }
}

@Composable
private fun LockedArea(vm: RootViewModel, state: AppState, carName: String?) {
    val nav = rememberNavController()
    Box(Modifier.fillMaxSize()) {
        NavHost(nav, startDestination = Routes.SELECT) {
            composable(Routes.SELECT) {
                DriverSelectScreen(
                    carName = carName,
                    onSelect = { vm.sessionManager.selectDriver(it) },
                    onAddDriver = { nav.navigate(Routes.addDriver()) },
                    onSignInAgain = { nav.navigate(Routes.addDriver(it)) },
                    onDebug = { nav.navigate(Routes.DEBUG) },
                )
            }
            composable(
                Routes.ADD_DRIVER,
                arguments = listOf(navArgument("username") { type = NavType.StringType; nullable = true; defaultValue = null }),
            ) { entry ->
                AddDriverScreen(
                    prefillUsername = entry.arguments?.getString("username"),
                    onDone = { nav.popBackStack(Routes.SELECT, false) },
                )
            }
            composable(Routes.DEBUG) { DebugScreen(onBack = { nav.popBackStack() }) }
        }
        if (state is AppState.PinEntry) {
            PinEntryScreen(
                userId = state.userId,
                onCancel = { vm.sessionManager.cancelPinEntry() },
                onForgotPin = { username ->
                    vm.sessionManager.cancelPinEntry()
                    nav.navigate(Routes.addDriver(username))
                },
            )
        }
    }
}

@Composable
private fun UnlockedArea(vm: RootViewModel, state: AppState, driving: DrivingState) {
    val nav = rememberNavController()
    val backStack by nav.currentBackStackEntryAsState()
    val route = backStack?.destination?.route
    val parked = driving == DrivingState.PARKED

    // Moving: an open secondary screen falls back to the Drive/Result screen (spec §7.3).
    LaunchedEffect(parked, route) {
        if (!parked && route != null && route !in Routes.allowedWhileMoving) {
            nav.popBackStack(Routes.HOME, inclusive = false)
        }
    }
    // A finished/started drive brings the home screen back.
    LaunchedEffect(state::class) {
        if (route != null && route != Routes.HOME && state !is AppState.Result) nav.popBackStack(Routes.HOME, false)
    }

    Row(Modifier.fillMaxSize()) {
        if (parked) NavRail(nav, route)
        Box(Modifier.weight(1f).fillMaxHeight()) {
            NavHost(nav, startDestination = Routes.HOME) {
                composable(Routes.HOME) {
                    when (state) {
                        is AppState.SessionActive, is AppState.Ending -> DriveScreen()
                        is AppState.Result -> ResultScreen(state, onDetails = { nav.navigate(Routes.details(it)) })
                        else -> Unit
                    }
                }
                composable(Routes.DETAILS, arguments = listOf(navArgument("driveId") { type = NavType.StringType })) { e ->
                    DetailsScreen(driveId = e.arguments!!.getString("driveId")!!, onBack = { nav.popBackStack() })
                }
                composable(Routes.HISTORY) { HistoryScreen(onOpen = { nav.navigate(Routes.details(it)) }) }
                composable(Routes.STATS) { StatsScreen() }
                composable(Routes.LEADERBOARD) { LeaderboardScreen() }
                composable(Routes.SETTINGS) { SettingsScreen(onDebug = { nav.navigate(Routes.DEBUG) }) }
                composable(Routes.DEBUG) { DebugScreen(onBack = { nav.popBackStack() }) }
            }
        }
    }
}

@Composable
private fun NavRail(nav: NavHostController, route: String?) {
    data class Item(val route: String, val icon: ImageVector, val label: Int)
    val items = listOf(
        Item(Routes.HOME, Icons.Filled.DirectionsCar, R.string.nav_drive),
        Item(Routes.HISTORY, Icons.Filled.History, R.string.nav_history),
        Item(Routes.STATS, Icons.Filled.BarChart, R.string.nav_stats),
        Item(Routes.LEADERBOARD, Icons.Filled.EmojiEvents, R.string.nav_leaderboard),
        Item(Routes.SETTINGS, Icons.Filled.Settings, R.string.nav_settings),
    )
    val colors = NavigationRailItemDefaults.colors(
        selectedIconColor = CarColors.Orange, selectedTextColor = CarColors.Orange,
        indicatorColor = CarColors.TealDeep,
        unselectedIconColor = CarColors.Muted, unselectedTextColor = CarColors.Muted,
    )
    Row(Modifier.fillMaxHeight()) {
        NavigationRail(containerColor = CarColors.Surface, modifier = Modifier.fillMaxHeight().width(120.dp)) {
            VSpace(Space.M)
            items.forEach { item ->
                NavigationRailItem(
                    selected = route == item.route,
                    onClick = {
                        if (item.route == Routes.HOME) nav.popBackStack(Routes.HOME, false)
                        else nav.navigate(item.route) { popUpTo(Routes.HOME); launchSingleTop = true }
                    },
                    icon = { Icon(item.icon, contentDescription = stringResource(item.label), Modifier.size(40.dp)) },
                    label = { Text(stringResource(item.label), style = MaterialTheme.typography.bodySmall, maxLines = 1) },
                    colors = colors,
                    modifier = Modifier.padding(vertical = Space.XS),
                )
            }
        }
        Box(Modifier.fillMaxHeight().width(1.dp).background(CarColors.Outline))
    }
}

/** Top status bar: driver, tracking, engine dot, online, pending uploads (spec §6). */
@Composable
private fun StatusBar(s: StatusBarState, driving: DrivingState) {
    Column {
        Row(
            Modifier.fillMaxWidth().height(CarDimens.StatusBarHeight).background(CarColors.Surface).padding(horizontal = Space.L),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.L),
        ) {
            if (s.driver != null) {
                Avatar(s.driver.initials, s.driver.avatarColor, 48.dp)
                Text(s.driver.displayName, style = MaterialTheme.typography.titleMedium)
            } else {
                Text(stringResource(R.string.app_name), style = MaterialTheme.typography.titleMedium, color = CarColors.TealSoft)
            }
            if (s.tracking) {
                Row(
                    Modifier.clip(CarShapes.Pill).background(CarColors.SurfaceHigh).padding(horizontal = Space.M, vertical = Space.XS),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.XS),
                ) {
                    PulsingDot(CarColors.Orange, 14.dp)
                    Text(stringResource(R.string.tracking), style = MaterialTheme.typography.bodySmall, color = CarColors.Orange)
                }
            }
            Box(Modifier.weight(1f))
            if (driving == DrivingState.MOVING) Text(stringResource(R.string.moving), color = CarColors.Muted, style = MaterialTheme.typography.bodySmall)
            StatusDot(
                stringResource(R.string.engine),
                when (s.engine) {
                    ConnectionStatus.CONNECTED -> CarColors.Green
                    ConnectionStatus.CONNECTING, ConnectionStatus.RECONNECTING -> CarColors.Amber
                    ConnectionStatus.DISCONNECTED -> CarColors.Red
                },
            )
            StatusDot(stringResource(if (s.online) R.string.online else R.string.offline), if (s.online) CarColors.Green else CarColors.Muted)
            if (s.pendingUploads > 0) Text("⇡ ${s.pendingUploads}", style = MaterialTheme.typography.bodySmall, color = CarColors.Amber)
            if (s.carRevoked) Text(stringResource(R.string.car_revoked_short), color = CarColors.Red, style = MaterialTheme.typography.bodySmall)
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(CarColors.Outline))
    }
}

@Composable
private fun StatusDot(label: String, color: androidx.compose.ui.graphics.Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.XS)) {
        Box(Modifier.size(12.dp).clip(CircleShape).background(color))
        Text(label, style = MaterialTheme.typography.bodySmall, color = CarColors.Muted)
    }
}
