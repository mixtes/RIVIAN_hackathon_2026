package com.hackathon.drivescore.app

import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Whether any app UI is visible (alerts go to heads-up notifications otherwise, spec §9.3). */
class AppForeground {
    private val _inForeground = MutableStateFlow(false)
    val inForeground: StateFlow<Boolean> = _inForeground.asStateFlow()

    /** Must be called on the main thread (Application.onCreate). */
    fun attach() {
        ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) { _inForeground.value = true }
            override fun onStop(owner: LifecycleOwner) { _inForeground.value = false }
        })
    }
}
