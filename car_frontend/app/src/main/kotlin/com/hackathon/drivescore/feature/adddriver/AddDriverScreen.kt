package com.hackathon.drivescore.feature.adddriver

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hackathon.drivescore.R
import com.hackathon.drivescore.app.LocalDrivingState
import com.hackathon.drivescore.core.common.Formatters
import com.hackathon.drivescore.core.designsystem.Avatar
import com.hackathon.drivescore.core.designsystem.CarColors
import com.hackathon.drivescore.core.designsystem.EmptyState
import com.hackathon.drivescore.core.designsystem.Keypad
import com.hackathon.drivescore.core.designsystem.PinDots
import com.hackathon.drivescore.core.designsystem.PrimaryButton
import com.hackathon.drivescore.core.designsystem.SecondaryButton
import com.hackathon.drivescore.core.designsystem.VSpace
import com.hackathon.drivescore.core.model.DrivingState
import com.hackathon.drivescore.core.model.Region

@Composable
fun FormError.text(): String = stringResource(
    when (this) {
        FormError.REQUIRED -> R.string.err_required
        FormError.USERNAME_FORMAT -> R.string.err_username_format
        FormError.USERNAME_RESERVED -> R.string.err_username_reserved
        FormError.USERNAME_TAKEN -> R.string.err_username_taken
        FormError.DISPLAY_NAME_LENGTH -> R.string.err_display_name
        FormError.PASSWORD_SHORT -> R.string.err_password_short
        FormError.PASSWORD_LONG -> R.string.err_password_long
        FormError.PASSWORD_EQUALS_USERNAME -> R.string.err_password_username
        FormError.PASSWORD_MISMATCH -> R.string.err_password_mismatch
        FormError.INVALID_CREDENTIALS -> R.string.err_invalid_credentials
        FormError.CAR_FULL -> R.string.car_full
        FormError.OFFLINE -> R.string.err_offline
        FormError.TOO_MANY_ATTEMPTS -> R.string.err_too_many_attempts
        FormError.SERVER -> R.string.err_server
        FormError.EMAIL_TAKEN -> R.string.err_email_taken
        FormError.INVALID_VALUE -> R.string.err_invalid_value
        FormError.PIN_MISMATCH -> R.string.err_pin_mismatch
        FormError.PIN_FORMAT -> R.string.err_pin_format
    },
)

/** Add Driver / Create Account (spec §6.10). Online and parked only. */
@Composable
fun AddDriverScreen(prefillUsername: String?, onDone: () -> Unit, vm: AddDriverViewModel = hiltViewModel()) {
    LaunchedEffect(prefillUsername) { vm.init(prefillUsername) }
    val ui by vm.ui.collectAsStateWithLifecycle()
    if (LocalDrivingState.current == DrivingState.MOVING) {
        Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            EmptyState(stringResource(R.string.available_when_parked))
            SecondaryButton(stringResource(R.string.back), onDone)
        }
        return
    }
    Box(Modifier.fillMaxSize().padding(32.dp)) {
        when (val step = ui.step) {
            AddStep.Choice -> Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                Text(stringResource(R.string.add_driver), style = MaterialTheme.typography.headlineLarge)
                VSpace(32.dp)
                Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                    PrimaryButton(stringResource(R.string.i_have_an_account), vm::chooseSignIn, Modifier.width(360.dp))
                    PrimaryButton(stringResource(R.string.create_account), vm::chooseCreate, Modifier.width(360.dp), color = CarColors.Green)
                }
                VSpace(24.dp)
                SecondaryButton(stringResource(R.string.cancel), onDone)
            }
            is AddStep.SignIn -> SignInStep(step, ui.busy, vm, onCancel = onDone)
            is AddStep.Create -> CreateStep(step, ui.busy, ui.regions, vm)
            is AddStep.Welcome -> Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                Avatar(initials(step.profile.displayName), step.profile.avatarColor, 140.dp)
                VSpace(16.dp)
                Text(stringResource(R.string.welcome_name, step.profile.displayName), style = MaterialTheme.typography.headlineLarge)
                Text("${Formatters.flagEmoji(step.profile.regionCode)} ${step.profile.regionCode} · @${step.profile.username}", style = MaterialTheme.typography.bodyLarge, color = CarColors.Muted)
                VSpace(32.dp)
                if (step.resetPin) Text(stringResource(R.string.already_on_car_reset_pin), style = MaterialTheme.typography.titleMedium)
                VSpace(16.dp)
                Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                    SecondaryButton(stringResource(R.string.cancel), onDone)
                    PrimaryButton(stringResource(if (step.resetPin) R.string.reset_pin else R.string.choose_pin), vm::welcomeContinue)
                }
            }
            is AddStep.ChoosePin -> ChoosePinStep(step, ui.busy, vm)
            is AddStep.Done -> Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                Avatar(initials(step.profile.displayName), step.profile.avatarColor, 140.dp)
                VSpace(16.dp)
                Text(stringResource(R.string.profile_ready, step.profile.displayName), style = MaterialTheme.typography.headlineLarge)
                VSpace(32.dp)
                Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                    SecondaryButton(stringResource(R.string.done), onDone)
                    PrimaryButton(stringResource(R.string.start_driving_now), { vm.startDrivingNow(); onDone() }, color = CarColors.Green)
                }
            }
        }
    }
}

private fun initials(name: String) = name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.take(2)
    .joinToString("") { it.first().uppercase() }.ifEmpty { "?" }

@Composable
private fun Field(
    value: String, onChange: (String) -> Unit, label: String, error: FormError?, password: Boolean = false,
    keyboardType: KeyboardType = KeyboardType.Text, capitalization: KeyboardCapitalization = KeyboardCapitalization.None,
) {
    var visible by remember { mutableStateOf(false) }
    OutlinedTextField(
        value = value, onValueChange = onChange, singleLine = true,
        label = { Text(label, fontSize = 20.sp) },
        textStyle = TextStyle(fontSize = 24.sp, color = CarColors.OnDark),
        isError = error != null,
        supportingText = error?.let { { Text(it.text(), fontSize = 18.sp) } },
        visualTransformation = if (password && !visible) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = if (password) KeyboardType.Password else keyboardType, capitalization = capitalization, autoCorrectEnabled = false),
        trailingIcon = if (password) { { TextButton(onClick = { visible = !visible }) { Text(stringResource(if (visible) R.string.hide else R.string.show)) } } } else null,
        modifier = Modifier.fillMaxWidth().heightIn(min = 76.dp),
    )
}

@Composable
private fun SignInStep(s: AddStep.SignIn, busy: Boolean, vm: AddDriverViewModel, onCancel: () -> Unit) {
    Column(Modifier.fillMaxWidth(0.6f).verticalScroll(rememberScrollState())) {
        Text(stringResource(R.string.sign_in), style = MaterialTheme.typography.headlineLarge)
        VSpace(24.dp)
        Field(s.username, { vm.updateSignIn(username = it) }, stringResource(R.string.username), null)
        VSpace(12.dp)
        Field(s.password, { vm.updateSignIn(password = it) }, stringResource(R.string.password), null, password = true)
        s.error?.let { VSpace(8.dp); Text(it.text(), color = CarColors.Red, style = MaterialTheme.typography.bodyLarge) }
        VSpace(24.dp)
        Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            SecondaryButton(stringResource(R.string.cancel), onCancel)
            PrimaryButton(stringResource(if (busy) R.string.signing_in else R.string.sign_in), vm::signIn, enabled = !busy)
        }
    }
}

@Composable
private fun CreateStep(s: AddStep.Create, busy: Boolean, regions: List<Region>, vm: AddDriverViewModel) {
    var pickRegion by remember { mutableStateOf(false) }
    val f = s.form
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Text(stringResource(R.string.create_account), style = MaterialTheme.typography.headlineLarge)
        VSpace(16.dp)
        Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            Column(Modifier.weight(1f)) {
                Field(f.username, { v -> vm.updateForm { it.copy(username = v.lowercase().trim()) } }, stringResource(R.string.username), f.errors["username"])
                Field(f.displayName, { v -> vm.updateForm { it.copy(displayName = v.take(40)) } }, stringResource(R.string.display_name), f.errors["display_name"], capitalization = KeyboardCapitalization.Words)
                Field(f.password, { v -> vm.updateForm { it.copy(password = v) } }, stringResource(R.string.password), f.errors["password"], password = true)
                Field(f.confirm, { v -> vm.updateForm { it.copy(confirm = v) } }, stringResource(R.string.confirm_password), f.errors["confirm"], password = true)
            }
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.region), style = MaterialTheme.typography.titleMedium)
                val name = regions.firstOrNull { it.code == f.regionCode }?.name ?: f.regionCode
                SecondaryButton("${Formatters.flagEmoji(f.regionCode)} $name", { pickRegion = true }, Modifier.fillMaxWidth())
                f.errors["region_code"]?.let { Text(it.text(), color = CarColors.Red) }
                VSpace(24.dp)
                Text(stringResource(R.string.avatar_color), style = MaterialTheme.typography.titleMedium)
                VSpace(8.dp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    AVATAR_COLORS.forEach { c ->
                        Box(
                            Modifier.size(64.dp).clip(CircleShape).background(CarColors.parse(c))
                                .border(if (c == f.avatarColor) 5.dp else 0.dp, Color.White, CircleShape)
                                .clickable { vm.updateForm { it.copy(avatarColor = c) } },
                        )
                    }
                }
            }
        }
        s.generalError?.let { VSpace(8.dp); Text(it.text(), color = CarColors.Red, style = MaterialTheme.typography.bodyLarge) }
        VSpace(16.dp)
        Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            SecondaryButton(stringResource(R.string.back), vm::back)
            PrimaryButton(stringResource(if (busy) R.string.creating else R.string.create_account), vm::createAccount, enabled = !busy, color = CarColors.Green)
        }
    }
    if (pickRegion) RegionPicker(regions, onPick = { code -> vm.updateForm { it.copy(regionCode = code) }; pickRegion = false }, onDismiss = { pickRegion = false })
}

@Composable
private fun RegionPicker(regions: List<Region>, onPick: (String) -> Unit, onDismiss: () -> Unit) {
    var q by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
        title = { Text(stringResource(R.string.region)) },
        text = {
            Column {
                OutlinedTextField(q, { q = it }, singleLine = true, label = { Text(stringResource(R.string.search)) }, modifier = Modifier.fillMaxWidth())
                val filtered = regions.filter { q.isBlank() || it.name.contains(q, true) || it.code.equals(q, true) }
                LazyColumn(Modifier.heightIn(max = 420.dp)) {
                    items(filtered, key = { it.code }) { r ->
                        Text(
                            "${Formatters.flagEmoji(r.code)}  ${r.name} (${r.code})",
                            style = MaterialTheme.typography.bodyLarge,
                            modifier = Modifier.fillMaxWidth().clickable { onPick(r.code) }.padding(vertical = 16.dp),
                        )
                    }
                }
            }
        },
    )
}

@Composable
private fun ChoosePinStep(s: AddStep.ChoosePin, busy: Boolean, vm: AddDriverViewModel) {
    Row(Modifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(stringResource(if (s.first == null) R.string.choose_pin_title else R.string.confirm_pin_title), style = MaterialTheme.typography.headlineMedium)
            Text(stringResource(R.string.pin_hint), style = MaterialTheme.typography.bodyMedium, color = CarColors.Muted)
            VSpace(24.dp)
            PinDots(maxOf(4, s.entered.length.coerceAtMost(6)), s.entered.length)
            s.error?.let { VSpace(12.dp); Text(it.text(), color = CarColors.Red, style = MaterialTheme.typography.bodyLarge) }
            VSpace(24.dp)
            PrimaryButton(
                stringResource(if (s.first == null) R.string.next else R.string.confirm),
                { vm.pinNext() }, enabled = !busy && s.entered.length >= 4,
            )
        }
        Keypad(onDigit = vm::pinDigit, onBackspace = vm::pinBackspace, enabled = !busy)
    }
    if (s.trivialWarning) {
        AlertDialog(
            onDismissRequest = vm::dismissTrivial,
            title = { Text(stringResource(R.string.trivial_pin_title)) },
            text = { Text(stringResource(R.string.trivial_pin_body)) },
            confirmButton = { TextButton(onClick = { vm.pinNext(overrideTrivial = true) }) { Text(stringResource(R.string.use_anyway)) } },
            dismissButton = { TextButton(onClick = vm::dismissTrivial) { Text(stringResource(R.string.change)) } },
        )
    }
}
