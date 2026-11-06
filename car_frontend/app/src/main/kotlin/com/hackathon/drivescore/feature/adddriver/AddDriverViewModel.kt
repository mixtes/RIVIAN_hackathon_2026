package com.hackathon.drivescore.feature.adddriver

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hackathon.drivescore.core.model.Region
import com.hackathon.drivescore.core.network.ApiFailure
import com.hackathon.drivescore.core.network.CreateAccountRequest
import com.hackathon.drivescore.core.network.UserProfileDto
import com.hackathon.drivescore.data.DriverRepository
import com.hackathon.drivescore.data.MetaRepository
import com.hackathon.drivescore.domain.PinHasher
import com.hackathon.drivescore.service.SessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/** Error keys mapped to string resources by the screen. */
enum class FormError {
    REQUIRED, USERNAME_FORMAT, USERNAME_RESERVED, USERNAME_TAKEN, DISPLAY_NAME_LENGTH, PASSWORD_SHORT, PASSWORD_LONG,
    PASSWORD_EQUALS_USERNAME, PASSWORD_MISMATCH, INVALID_CREDENTIALS, CAR_FULL, OFFLINE, TOO_MANY_ATTEMPTS,
    SERVER, EMAIL_TAKEN, INVALID_VALUE, PIN_MISMATCH, PIN_FORMAT,
}

data class CreateForm(
    val username: String = "",
    val displayName: String = "",
    val password: String = "",
    val confirm: String = "",
    val regionCode: String = MetaRepository.defaultRegion(),
    val avatarColor: String = AVATAR_COLORS.first(),
    val errors: Map<String, FormError> = emptyMap(),
)

val AVATAR_COLORS = listOf("#4F7DF3", "#E8603C", "#2BB673", "#B57BEE", "#F5A524", "#E5484D", "#26C6DA", "#8D6E63")
private val RESERVED = setOf("admin", "root", "system", "me", "global")
private val USERNAME_RE = Regex("^[a-z0-9_.]{3,32}$")

sealed interface AddStep {
    data object Choice : AddStep
    data class SignIn(val username: String = "", val password: String = "", val error: FormError? = null) : AddStep
    data class Create(val form: CreateForm = CreateForm(), val generalError: FormError? = null) : AddStep
    /** [resetPin]: the user is already on this car (spec §6.10 "Reset PIN?"). */
    data class Welcome(val profile: UserProfileDto, val resetPin: Boolean) : AddStep
    data class ChoosePin(
        val profile: UserProfileDto,
        val first: String? = null,
        val entered: String = "",
        val error: FormError? = null,
        val trivialWarning: Boolean = false,
    ) : AddStep
    data class Done(val profile: UserProfileDto) : AddStep
}

data class AddDriverUi(val step: AddStep = AddStep.Choice, val busy: Boolean = false, val regions: List<Region> = emptyList())

@HiltViewModel
class AddDriverViewModel @Inject constructor(
    private val drivers: DriverRepository,
    private val meta: MetaRepository,
    private val session: SessionManager,
) : ViewModel() {
    private val _ui = MutableStateFlow(AddDriverUi())
    val ui: StateFlow<AddDriverUi> = _ui.asStateFlow()
    private var initialized = false

    fun init(prefillUsername: String?) {
        if (initialized) return
        initialized = true
        if (prefillUsername != null) _ui.update { it.copy(step = AddStep.SignIn(username = prefillUsername)) }
        viewModelScope.launch { _ui.update { it.copy(regions = meta.regions()) } }
    }

    private fun setStep(step: AddStep) = _ui.update { it.copy(step = step) }

    fun chooseSignIn() = setStep(AddStep.SignIn())
    fun chooseCreate() = setStep(AddStep.Create())
    fun back() = setStep(AddStep.Choice)

    fun updateSignIn(username: String? = null, password: String? = null) {
        val s = _ui.value.step as? AddStep.SignIn ?: return
        setStep(s.copy(username = username?.lowercase()?.trim() ?: s.username, password = password ?: s.password, error = null))
    }

    fun signIn() {
        val s = _ui.value.step as? AddStep.SignIn ?: return
        if (s.username.isBlank() || s.password.isEmpty()) { setStep(s.copy(error = FormError.REQUIRED)); return }
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = drivers.signIn(s.username, s.password)
            _ui.update { it.copy(busy = false) }
            r.onSuccess { setStep(AddStep.Welcome(it.profile, resetPin = it.alreadyOnCar)) }
                .onFailure { setStep(s.copy(password = "", error = mapError(it))) }
        }
    }

    fun updateForm(f: (CreateForm) -> CreateForm) {
        val s = _ui.value.step as? AddStep.Create ?: return
        val updated = f(s.form)
        setStep(s.copy(form = updated.copy(errors = validate(updated, onlyTouched = true)), generalError = null))
    }

    fun createAccount() {
        val s = _ui.value.step as? AddStep.Create ?: return
        val errors = validate(s.form, onlyTouched = false)
        if (errors.isNotEmpty()) { setStep(s.copy(form = s.form.copy(errors = errors))); return }
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val f = s.form
            val r = drivers.createAccount(
                CreateAccountRequest(f.username, f.password, f.displayName.trim(), f.regionCode, null, f.avatarColor),
            )
            _ui.update { it.copy(busy = false) }
            r.onSuccess { setStep(AddStep.ChoosePin(it)) }.onFailure { e ->
                val fieldErrors = serverFieldErrors(e)
                if (fieldErrors.isNotEmpty()) setStep(s.copy(form = f.copy(errors = fieldErrors)))
                else setStep(s.copy(generalError = mapError(e)))
            }
        }
    }

    fun welcomeContinue() {
        val s = _ui.value.step as? AddStep.Welcome ?: return
        setStep(AddStep.ChoosePin(s.profile))
    }

    fun pinDigit(c: Char) {
        val s = _ui.value.step as? AddStep.ChoosePin ?: return
        if (s.entered.length >= 6) return
        setStep(s.copy(entered = s.entered + c, error = null))
    }

    fun pinBackspace() {
        val s = _ui.value.step as? AddStep.ChoosePin ?: return
        setStep(s.copy(entered = s.entered.dropLast(1)))
    }

    /** Next on the first entry, or Confirm on the second. [overrideTrivial] = "Use anyway". */
    fun pinNext(overrideTrivial: Boolean = false) {
        val s = _ui.value.step as? AddStep.ChoosePin ?: return
        if (s.first == null) {
            if (!PinHasher.isValidPin(s.entered)) { setStep(s.copy(error = FormError.PIN_FORMAT)); return }
            if (PinHasher.isTrivial(s.entered) && !overrideTrivial) { setStep(s.copy(trivialWarning = true)); return }
            setStep(s.copy(first = s.entered, entered = "", trivialWarning = false))
        } else {
            if (s.entered != s.first) { setStep(AddStep.ChoosePin(s.profile, error = FormError.PIN_MISMATCH)); return }
            _ui.update { it.copy(busy = true) }
            viewModelScope.launch {
                drivers.savePin(s.profile, s.first)
                _ui.update { it.copy(busy = false, step = AddStep.Done(s.profile)) }
            }
        }
    }

    fun dismissTrivial() {
        val s = _ui.value.step as? AddStep.ChoosePin ?: return
        setStep(s.copy(trivialWarning = false, entered = ""))
    }

    fun startDrivingNow() {
        val s = _ui.value.step as? AddStep.Done ?: return
        viewModelScope.launch { session.onUnlocked(s.profile.id, s.profile.displayName) }
    }

    private fun validate(f: CreateForm, onlyTouched: Boolean): Map<String, FormError> {
        val e = mutableMapOf<String, FormError>()
        fun touched(v: String) = !onlyTouched || v.isNotEmpty()
        if (touched(f.username)) when {
            f.username.isEmpty() -> e["username"] = FormError.REQUIRED
            !USERNAME_RE.matches(f.username) -> e["username"] = FormError.USERNAME_FORMAT
            f.username in RESERVED -> e["username"] = FormError.USERNAME_RESERVED
        }
        if (touched(f.displayName) && f.displayName.trim().length !in 1..40) e["display_name"] = FormError.DISPLAY_NAME_LENGTH
        if (touched(f.password)) when {
            f.password.length < 8 -> e["password"] = FormError.PASSWORD_SHORT
            f.password.length > 128 -> e["password"] = FormError.PASSWORD_LONG
            f.password.equals(f.username, ignoreCase = true) -> e["password"] = FormError.PASSWORD_EQUALS_USERNAME
        }
        if (touched(f.confirm) && f.confirm != f.password) e["confirm"] = FormError.PASSWORD_MISMATCH
        if (!onlyTouched && f.regionCode.length != 2) e["region_code"] = FormError.REQUIRED
        return e
    }

    private fun serverFieldErrors(e: Throwable): Map<String, FormError> {
        val h = e as? ApiFailure.Http ?: return emptyMap()
        return when (h.code) {
            "USERNAME_TAKEN" -> mapOf("username" to FormError.USERNAME_TAKEN)
            "VALIDATION_ERROR" -> h.details.mapNotNull { d ->
                val field = d.field?.substringAfterLast('.') ?: return@mapNotNull null
                field to FormError.INVALID_VALUE
            }.toMap()
            else -> emptyMap()
        }
    }

    private fun mapError(e: Throwable): FormError = when (e) {
        is ApiFailure.Network -> FormError.OFFLINE
        is ApiFailure.Http -> when (e.code) {
            "INVALID_CREDENTIALS" -> FormError.INVALID_CREDENTIALS
            "CAR_DRIVER_LIMIT" -> FormError.CAR_FULL
            "TOO_MANY_ATTEMPTS" -> FormError.TOO_MANY_ATTEMPTS
            "USERNAME_TAKEN" -> FormError.USERNAME_TAKEN
            "EMAIL_TAKEN" -> FormError.EMAIL_TAKEN
            else -> if (e.status == 429) FormError.TOO_MANY_ATTEMPTS else FormError.SERVER
        }
        else -> FormError.SERVER
    }
}
