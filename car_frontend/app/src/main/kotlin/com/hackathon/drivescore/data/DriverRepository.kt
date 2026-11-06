package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.common.Clock
import com.hackathon.drivescore.core.database.ApiCacheDao
import com.hackathon.drivescore.core.database.DriveDao
import com.hackathon.drivescore.core.database.DriverProfileDao
import com.hackathon.drivescore.core.database.DriverProfileEntity
import com.hackathon.drivescore.core.datastore.BytesCipher
import com.hackathon.drivescore.core.model.Driver
import com.hackathon.drivescore.core.network.ApiFailure
import com.hackathon.drivescore.core.network.CreateAccountRequest
import com.hackathon.drivescore.core.network.DriveScoreApi
import com.hackathon.drivescore.core.network.LinkDriverRequest
import com.hackathon.drivescore.core.network.UserProfileDto
import com.hackathon.drivescore.core.network.apiCall
import com.hackathon.drivescore.domain.LockoutStatus
import com.hackathon.drivescore.domain.PinHasher
import com.hackathon.drivescore.domain.PinLockoutPolicy
import com.hackathon.drivescore.domain.PinRecord
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.withContext

sealed interface UnlockResult {
    data class Success(val driver: Driver) : UnlockResult
    data class WrongPin(val attemptsLeft: Int, val lockout: LockoutStatus) : UnlockResult
    data class LockedOut(val remainingMs: Long) : UnlockResult
    data object PinDisabled : UnlockResult
    data object NoProfile : UnlockResult
}

data class SignInOutcome(val profile: UserProfileDto, val alreadyOnCar: Boolean)

const val MAX_DRIVERS_PER_CAR = 8

/** Driver profiles on this car: linking, PIN storage/verification, lockout, removal (spec §6.10–6.11, §10). */
class DriverRepository(
    private val api: DriveScoreApi,
    private val profiles: DriverProfileDao,
    private val drives: DriveDao,
    private val cache: ApiCacheDao,
    private val pinCipher: BytesCipher,
    private val hasher: PinHasher,
    private val clock: Clock,
) {
    fun observeDrivers(): Flow<List<Driver>> =
        combine(profiles.observeActive(), drives.observeUsersWithPending()) { list, pending ->
            list.map { it.toDriver(it.userId in pending) }
        }

    fun observeProfileEntities(): Flow<List<DriverProfileEntity>> = profiles.observeActive()

    fun observeDriver(userId: String): Flow<DriverProfileEntity?> = profiles.observe(userId)

    suspend fun entity(userId: String): DriverProfileEntity? = profiles.get(userId)

    suspend fun get(userId: String): Driver? = profiles.get(userId)?.toDriver(false)

    suspend fun activeCount(): Int = profiles.activeCount()

    suspend fun signIn(username: String, password: String): Result<SignInOutcome> =
        apiCall { api.linkDriver(LinkDriverRequest(username.trim().lowercase(), password)) }.map { r ->
            val local = profiles.get(r.user.id)
            SignInOutcome(r.user, alreadyOnCar = local != null && !local.pendingUnlink)
        }

    suspend fun createAccount(req: CreateAccountRequest): Result<UserProfileDto> =
        apiCall { api.createAccount(req) }.map { it.user }

    /** Stores (or replaces) the local profile with a new PIN; resets lockout and re-auth flags. */
    suspend fun savePin(profile: UserProfileDto, pin: String) {
        val record = withContext(Dispatchers.Default) { hasher.hash(pin) }
        val encrypted = pinCipher.encrypt(record.serialize())
        val existing = profiles.get(profile.id)
        profiles.upsert(
            DriverProfileEntity(
                userId = profile.id,
                username = profile.username,
                displayName = profile.displayName,
                avatarColor = profile.avatarColor,
                regionCode = profile.regionCode,
                encryptedPinRecord = encrypted,
                addedAt = existing?.addedAt ?: clock.nowMs(),
                lastUsedAt = existing?.lastUsedAt,
            ),
        )
    }

    fun lockoutStatus(p: DriverProfileEntity): LockoutStatus = PinLockoutPolicy.status(
        p.failedAttempts, p.pinDisabled, p.lockoutUntilEpochMs, p.lockoutUntilElapsedMs,
        clock.nowMs(), clock.elapsedRealtimeMs(),
    )

    suspend fun unlock(userId: String, pin: String): UnlockResult {
        val p = profiles.get(userId) ?: return UnlockResult.NoProfile
        when (val s = lockoutStatus(p)) {
            LockoutStatus.PinDisabled -> return UnlockResult.PinDisabled
            is LockoutStatus.LockedOut -> return UnlockResult.LockedOut(s.remainingMs)
            LockoutStatus.Open -> Unit
        }
        val ok = verify(p, pin)
        return if (ok) {
            val updated = p.copy(failedAttempts = 0, lockoutUntilEpochMs = null, lockoutUntilElapsedMs = null, lastUsedAt = clock.nowMs())
            profiles.upsert(updated)
            UnlockResult.Success(updated.toDriver(false))
        } else {
            val f = PinLockoutPolicy.onFailure(p.failedAttempts, clock.nowMs(), clock.elapsedRealtimeMs())
            val updated = p.copy(
                failedAttempts = f.failures, lockoutUntilEpochMs = f.lockoutUntilEpochMs,
                lockoutUntilElapsedMs = f.lockoutUntilElapsedMs, pinDisabled = f.pinDisabled,
            )
            profiles.upsert(updated)
            if (f.pinDisabled) UnlockResult.PinDisabled
            else UnlockResult.WrongPin(PinLockoutPolicy.attemptsLeftBeforeLockout(f.failures), lockoutStatus(updated))
        }
    }

    private suspend fun verify(p: DriverProfileEntity, pin: String): Boolean = withContext(Dispatchers.Default) {
        try {
            hasher.verify(pin, PinRecord.deserialize(pinCipher.decrypt(p.encryptedPinRecord)))
        } catch (_: Exception) {
            false
        }
    }

    suspend fun pinLength(userId: String): Int? = profiles.get(userId)?.let { p ->
        try { PinRecord.deserialize(pinCipher.decrypt(p.encryptedPinRecord)).length } catch (_: Exception) { null }
    }

    /** Change PIN (spec §6.11): verifies the current PIN with the same lockout rules. */
    suspend fun changePin(userId: String, current: String, newPin: String): UnlockResult {
        val r = unlock(userId, current)
        if (r is UnlockResult.Success) {
            val p = profiles.get(userId) ?: return UnlockResult.NoProfile
            val record = withContext(Dispatchers.Default) { hasher.hash(newPin) }
            profiles.upsert(p.copy(encryptedPinRecord = pinCipher.encrypt(record.serialize())))
        }
        return r
    }

    suspend fun unsyncedCount(userId: String): Int = drives.unsyncedCount(userId)

    /**
     * Remove from this car (spec §6.11, §10.5). Tries the DELETE now; when offline it is queued
     * (pendingUnlink) and SyncDrivesWorker sends it. Local data is wiped either way.
     */
    suspend fun removeDriver(userId: String): Boolean {
        val p = profiles.get(userId) ?: return true
        val removedNow = apiCall { api.unlinkDriver(userId) }.getOrNull()?.let { it.isSuccessful || it.code() == 404 } ?: false
        drives.deleteAllForUser(userId)
        cache.deleteForUser(userId)
        if (removedNow) profiles.delete(userId) else profiles.upsert(p.copy(pendingUnlink = true))
        return removedNow
    }

    /** Refresh display name, region and color on unlock when online (spec §12). */
    suspend fun refreshProfile(userId: String) {
        val dto = apiCall { api.me(userId) }.getOrElse {
            if (it is ApiFailure.Http && it.status == 403 && it.code == "DRIVER_NOT_LINKED") profiles.setNeedsReauth(userId, true)
            return
        }
        val p = profiles.get(userId) ?: return
        profiles.upsert(p.copy(displayName = dto.displayName, regionCode = dto.regionCode, avatarColor = dto.avatarColor))
    }

    private fun DriverProfileEntity.toDriver(pending: Boolean) = Driver(
        userId, username, displayName, avatarColor, regionCode, failedAttempts, pinDisabled || failedAttempts >= PinLockoutPolicy.MAX_ATTEMPTS,
        needsReauth, pending,
    )
}
