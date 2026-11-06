package com.hackathon.drivescore.feature.lock

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.model.Driver
import com.hackathon.drivescore.core.network.ConnectivityMonitor
import com.hackathon.drivescore.data.CarRepository
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.data.MAX_DRIVERS_PER_CAR
import com.hackathon.drivescore.data.UnlockResult
import com.hackathon.drivescore.domain.LockoutStatus
import com.hackathon.drivescore.domain.PinLockoutPolicy
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class DriverTile(val driver: Driver, val lockedOutMs: Long?, val pinDisabled: Boolean)

data class LockUiState(
    val tiles: List<DriverTile> = emptyList(),
    val online: Boolean = false,
    val carRegistered: Boolean = false,
    val loaded: Boolean = false,
) {
    val canAdd: Boolean get() = online && carRegistered && tiles.size < MAX_DRIVERS_PER_CAR
    val full: Boolean get() = tiles.size >= MAX_DRIVERS_PER_CAR
}

private fun ticker(ms: Long) = flow { while (true) { emit(Unit); delay(ms) } }

@HiltViewModel
class LockViewModel @Inject constructor(
    drivers: DriverRepository,
    connectivity: ConnectivityMonitor,
    cars: CarRepository,
) : ViewModel() {
    val state: StateFlow<LockUiState> = combine(
        drivers.observeDrivers(), drivers.observeProfileEntities(), connectivity.online, ticker(1_000),
    ) { list, entities, online, _ ->
        val byId = entities.associateBy { it.userId }
        LockUiState(
            tiles = list.map { d ->
                val e = byId[d.userId]
                val status = e?.let { drivers.lockoutStatus(it) } ?: LockoutStatus.Open
                DriverTile(d, (status as? LockoutStatus.LockedOut)?.remainingMs, status == LockoutStatus.PinDisabled)
            },
            online = online, carRegistered = cars.isRegistered(), loaded = true,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), LockUiState())
}

data class PinUiState(
    val driver: Driver? = null,
    val pinLength: Int = 4,
    val entered: String = "",
    val message: String? = null,
    val attemptsLeft: Int? = null,
    val lockedOutMs: Long? = null,
    val pinDisabled: Boolean = false,
    val busy: Boolean = false,
    val shakeKey: Int = 0,
)

@HiltViewModel
class PinViewModel @Inject constructor(
    private val drivers: DriverRepository,
    private val session: SessionManager,
    private val connectivity: ConnectivityMonitor,
    private val clock: Clock,
) : ViewModel() {
    private val _state = MutableStateFlow(PinUiState())
    val state: StateFlow<PinUiState> = _state.asStateFlow()
    private var userId: String? = null
    private var tickJob: kotlinx.coroutines.Job? = null

    fun load(userId: String) {
        if (this.userId == userId) return
        this.userId = userId
        tickJob?.cancel()
        viewModelScope.launch {
            val d = drivers.get(userId)
            val len = drivers.pinLength(userId) ?: 4
            _state.value = PinUiState(driver = d, pinLength = len, pinDisabled = d?.pinDisabled == true)
            refreshLockout()
        }
        tickJob = viewModelScope.launch {
            while (true) { delay(1_000); refreshLockout() }
        }
    }

    private suspend fun refreshLockout() {
        val id = userId ?: return
        val entity = drivers.entity(id) ?: return
        val status = drivers.lockoutStatus(entity)
        _state.update {
            it.copy(
                lockedOutMs = (status as? LockoutStatus.LockedOut)?.remainingMs,
                pinDisabled = status == LockoutStatus.PinDisabled,
            )
        }
    }

    fun digit(c: Char) {
        val s = _state.value
        if (s.busy || s.lockedOutMs != null || s.pinDisabled || s.entered.length >= s.pinLength) return
        val entered = s.entered + c
        _state.update { it.copy(entered = entered, message = null) }
        // Auto-submit when the length matches the stored PIN length (spec §6.2).
        if (entered.length == s.pinLength) submit(entered)
    }

    fun backspace() = _state.update { it.copy(entered = it.entered.dropLast(1)) }

    private fun submit(pin: String) {
        val id = userId ?: return
        _state.update { it.copy(busy = true) }
        viewModelScope.launch {
            when (val r = drivers.unlock(id, pin)) {
                is UnlockResult.Success -> {
                    _state.update { it.copy(busy = false, entered = "") }
                    session.onUnlocked(r.driver.userId, r.driver.displayName)
                    if (connectivity.online.value) drivers.refreshProfile(id)
                }
                is UnlockResult.WrongPin -> _state.update {
                    it.copy(
                        busy = false, entered = "", shakeKey = it.shakeKey + 1,
                        attemptsLeft = r.attemptsLeft.takeIf { left -> left in 1..PinLockoutPolicy.WARN_UNTIL },
                        lockedOutMs = (r.lockout as? LockoutStatus.LockedOut)?.remainingMs,
                    )
                }
                is UnlockResult.LockedOut -> _state.update { it.copy(busy = false, entered = "", lockedOutMs = r.remainingMs) }
                UnlockResult.PinDisabled -> _state.update { it.copy(busy = false, entered = "", pinDisabled = true, shakeKey = it.shakeKey + 1) }
                UnlockResult.NoProfile -> _state.update { it.copy(busy = false, entered = "") }
            }
        }
    }
}
