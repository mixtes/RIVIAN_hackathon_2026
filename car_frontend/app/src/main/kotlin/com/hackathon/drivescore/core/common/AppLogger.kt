package com.hackathon.drivescore.core.common

import android.util.Log

/** Thin logging facade so pure-JVM unit tests don't touch android.util.Log. Never log PINs/tokens/passwords. */
interface AppLogger {
    fun d(tag: String, msg: String)
    fun w(tag: String, msg: String, t: Throwable? = null)
    fun e(tag: String, msg: String, t: Throwable? = null)
}

object AndroidLogger : AppLogger {
    override fun d(tag: String, msg: String) { Log.d(tag, msg) }
    override fun w(tag: String, msg: String, t: Throwable?) { Log.w(tag, msg, t) }
    override fun e(tag: String, msg: String, t: Throwable?) { Log.e(tag, msg, t) }
}

object NoopLogger : AppLogger {
    override fun d(tag: String, msg: String) = Unit
    override fun w(tag: String, msg: String, t: Throwable?) = Unit
    override fun e(tag: String, msg: String, t: Throwable?) = Unit
}
