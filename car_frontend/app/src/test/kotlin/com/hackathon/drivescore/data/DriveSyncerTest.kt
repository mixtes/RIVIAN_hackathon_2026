package com.hackathon.drivescore.data

import com.hackathon.drivescore.core.common.NoopLogger
import com.hackathon.drivescore.core.database.DriveEntity
import com.hackathon.drivescore.core.model.DriveSource
import com.hackathon.drivescore.core.model.SyncState
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class UploadClassifierTest {
    private fun c(status: Int, code: String? = null) = UploadClassifier.classify(HttpOutcome(status, code))

    @Test fun `response classes from spec 11_3`() {
        assertEquals(UploadOutcome.SYNCED, c(201))
        assertEquals(UploadOutcome.SYNCED, c(200))
        assertEquals(UploadOutcome.FAILED_PERMANENT, c(409, "DRIVE_CONFLICT"))
        assertEquals(UploadOutcome.FAILED_PERMANENT, c(422, "VALIDATION_ERROR"))
        assertEquals(UploadOutcome.DRIVER_NOT_LINKED, c(403, "DRIVER_NOT_LINKED"))
        assertEquals(UploadOutcome.CAR_REVOKED, c(403, "CAR_REVOKED"))
        assertEquals(UploadOutcome.CAR_REVOKED, c(401, "UNAUTHENTICATED"))
        assertEquals(UploadOutcome.RETRY, c(500))
        assertEquals(UploadOutcome.RETRY, c(503))
        assertEquals(UploadOutcome.RETRY, c(429))
    }
}

class DriveSyncerTest {
    private class Store : SyncStore {
        val drives = linkedMapOf<String, DriveEntity>()
        val states = mutableMapOf<String, SyncState>()
        val reauth = mutableSetOf<String>()
        val unlinks = mutableListOf<String>()
        val completedUnlinks = mutableListOf<String>()
        var revoked = false
        override suspend fun pendingDrives(limit: Int) = drives.values.filter { states[it.id] == SyncState.PENDING || states[it.id] == SyncState.SYNCING }.take(limit)
        override suspend fun needsReauth(userId: String) = userId in reauth
        override suspend fun setNeedsReauth(userId: String, value: Boolean) { if (value) reauth += userId else reauth -= userId }
        override suspend fun mark(driveId: String, outcome: UploadOutcome, error: String?) {
            states[driveId] = when (outcome) {
                UploadOutcome.SYNCED -> SyncState.SYNCED
                UploadOutcome.FAILED_PERMANENT -> SyncState.FAILED_PERMANENT
                else -> SyncState.PENDING
            }
        }
        override suspend fun markSyncing(driveId: String) { states[driveId] = SyncState.SYNCING }
        override suspend fun pendingUnlinks() = unlinks
        override suspend fun completeUnlink(userId: String) { completedUnlinks += userId }
        override suspend fun setCarRevoked() { revoked = true }
        override suspend fun isCarRevoked() = revoked

        fun add(id: String, user: String = "u1") {
            drives[id] = DriveEntity(id, user, "c", null, 0, 1000, 1, null, 80.0, null,
                """{"session_id":"$id","user_id":"$user","score":80.0}""", DriveSource.LOCAL, SyncState.PENDING, createdAt = drives.size.toLong())
            states[id] = SyncState.PENDING
        }
    }

    private class Remote(val responses: (String, JsonObject) -> HttpOutcome) : SyncRemote {
        val uploaded = mutableListOf<Pair<String, JsonObject>>()
        var unlinkResponse = HttpOutcome(204, null)
        override suspend fun upload(driverId: String, body: JsonObject): HttpOutcome {
            uploaded += driverId to body
            return responses(driverId, body)
        }
        override suspend fun unlink(userId: String) = unlinkResponse
    }

    @Test fun `uploads every pending drive with X-Driver-Id and id body`() = runTest {
        val store = Store().apply { add("d1"); add("d2", "u2") }
        val remote = Remote { _, _ -> HttpOutcome(201, null) }
        assertEquals(SyncRunResult.SUCCESS, DriveSyncer(store, remote, NoopLogger).run())
        assertEquals(listOf("u1", "u2"), remote.uploaded.map { it.first })
        assertEquals("d1", remote.uploaded[0].second["id"]!!.jsonPrimitive.content)
        assertFalse(remote.uploaded[0].second.containsKey("session_id"))
        assertEquals(SyncState.SYNCED, store.states["d1"])
    }

    @Test fun `conflicts and validation errors are permanent, others continue`() = runTest {
        val store = Store().apply { add("d1"); add("d2") }
        val remote = Remote { _, b -> if (b["id"]!!.jsonPrimitive.content == "d1") HttpOutcome(409, "DRIVE_CONFLICT") else HttpOutcome(201, null) }
        DriveSyncer(store, remote, NoopLogger).run()
        assertEquals(SyncState.FAILED_PERMANENT, store.states["d1"])
        assertEquals(SyncState.SYNCED, store.states["d2"])
    }

    @Test fun `driver not linked keeps pending, flags the profile and skips that driver`() = runTest {
        val store = Store().apply { add("d1"); add("d2"); add("d3", "u2") }
        val remote = Remote { user, _ -> if (user == "u1") HttpOutcome(403, "DRIVER_NOT_LINKED") else HttpOutcome(201, null) }
        assertEquals(SyncRunResult.SUCCESS, DriveSyncer(store, remote, NoopLogger).run())
        assertEquals(SyncState.PENDING, store.states["d1"])
        assertEquals(SyncState.PENDING, store.states["d2"])
        assertEquals(SyncState.SYNCED, store.states["d3"])
        assertTrue("u1" in store.reauth)
        assertEquals(2, remote.uploaded.size) // d2 skipped
    }

    @Test fun `car revoked stops the run`() = runTest {
        val store = Store().apply { add("d1"); add("d2") }
        val remote = Remote { _, _ -> HttpOutcome(401, "UNAUTHENTICATED") }
        assertEquals(SyncRunResult.STOPPED_CAR_REVOKED, DriveSyncer(store, remote, NoopLogger).run())
        assertTrue(store.revoked)
        assertEquals(1, remote.uploaded.size)
        assertEquals(SyncRunResult.STOPPED_CAR_REVOKED, DriveSyncer(store, remote, NoopLogger).run())
    }

    @Test fun `server errors and network failures retry and leave drives pending`() = runTest {
        val store = Store().apply { add("d1"); add("d2") }
        assertEquals(SyncRunResult.RETRY, DriveSyncer(store, Remote { _, _ -> HttpOutcome(503, null) }, NoopLogger).run())
        assertEquals(SyncState.PENDING, store.states["d1"])
        assertEquals(SyncRunResult.RETRY, DriveSyncer(store, Remote { _, _ -> throw IOException("offline") }, NoopLogger).run())
        assertEquals(SyncState.PENDING, store.states["d1"])
        assertEquals(SyncState.PENDING, store.states["d2"])
    }

    @Test fun `queued unlinks are sent first`() = runTest {
        val store = Store().apply { unlinks += "gone" }
        val remote = Remote { _, _ -> HttpOutcome(201, null) }
        DriveSyncer(store, remote, NoopLogger).run()
        assertEquals(listOf("gone"), store.completedUnlinks)
    }
}
