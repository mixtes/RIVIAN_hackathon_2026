package com.hackathon.drivescore.core.database

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters
import com.hackathon.drivescore.core.model.DriveSource
import com.hackathon.drivescore.core.model.SyncState

class Converters {
    @TypeConverter fun syncToString(v: SyncState): String = v.name
    @TypeConverter fun stringToSync(v: String): SyncState = SyncState.valueOf(v)
    @TypeConverter fun sourceToString(v: DriveSource): String = v.name
    @TypeConverter fun stringToSource(v: String): DriveSource = DriveSource.valueOf(v)
}

@Database(
    entities = [
        DriverProfileEntity::class,
        DriveEntity::class,
        DriveMistakeEntity::class,
        DriveEventEntity::class,
        ApiCacheEntity::class,
    ],
    version = 1,
    exportSchema = true,
)
@TypeConverters(Converters::class)
abstract class AppDatabase : RoomDatabase() {
    abstract fun driverProfiles(): DriverProfileDao
    abstract fun drives(): DriveDao
    abstract fun apiCache(): ApiCacheDao
}
