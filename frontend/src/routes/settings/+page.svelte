<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { authStore } from '$lib/stores/auth';
	import { api } from '$lib/services/api/index';
	import { themeStore, type ThemePref } from '$lib/stores/theme';
	import { nsfwFilterStore, type NsfwFilterMode } from '$lib/stores/nsfwFilter';
	import { notifications } from '$lib/stores/notifications';
	import type { NotificationTypePref } from '$lib/services/api/notifications';
	import { Button, Card, Input, Spinner, Switch, Alert } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import PlanUsageCard from '$lib/plans/components/PlanUsageCard.svelte';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import { resolveAvatarFileFromMediaItem } from './avatarMediaPick';
	import { passwordErrorsFromServer, validatePasswordChange, type PasswordChangeErrors } from '$lib/utils/passwordValidation';
	import { parseApiError } from '$lib/utils/apiErrors';
	import ApiKeysSection from '$lib/components/apikeys/ApiKeysSection.svelte';
	import AccountsSheet from '$lib/components/accounts/AccountsSheet.svelte';
	import { accountsStore } from '$lib/stores/accounts';
	import { refreshResumeSetup, showResumeSetup } from '$lib/stores/resumeSetup';
	import {
		availableSettingsSections,
		resolveSettingsSection,
		settingsSectionUrl,
		type SettingsSectionId
	} from './settingsSections';

	$: user = $authStore.user;
	$: isAdmin = user?.account_type === 'ADMIN';
	let resumeChecked = false;
	$: if (isAdmin && !resumeChecked) {
		resumeChecked = true;
		refreshResumeSetup();
	}
	let accountsSheetOpen = false;
	$: accountCount = Math.max($accountsStore.accounts.length, 1);
	$: hasLocalPassword = user?.has_local_password ?? true;

	// ── Avatar ────────────────────────────────────────────────────────────
	let avatarUploading = false;
	let avatarError: string | null = null;
	let avatarBroken = false;
	let avatarPickerOpen = false;
	let avatarPick: unknown = null;

	$: avatarUrl = user?.avatar_url ?? null;
	$: if (avatarUrl) avatarBroken = false;
	$: showAvatarImage = !!avatarUrl && !avatarBroken;

	function toggleAvatarPicker() {
		avatarError = null;
		avatarPickerOpen = !avatarPickerOpen;
	}

	async function handleAvatarPick(_name: string, value: unknown) {
		avatarPick = value;
		if (!value || typeof value !== 'object' || !user) return;
		const item = value as { url?: string; name?: string };
		if (!item.url) return;

		avatarError = null;
		avatarUploading = true;
		try {
			const resolved = await resolveAvatarFileFromMediaItem({ url: item.url, name: item.name });
			if (resolved.error || !resolved.file) {
				avatarError = resolved.error || 'Failed to load the selected image.';
				avatarPick = null;
				return;
			}

			const response = await api.uploadAvatar(user.id, resolved.file);
			if (response.success) {
				await authStore.refreshUser();
				avatarPick = null;
				avatarPickerOpen = false;
			} else {
				avatarError = response.message || 'Failed to upload avatar.';
			}
		} catch (err: any) {
			avatarError = parseApiError(err).message || 'Failed to upload avatar.';
		} finally {
			avatarUploading = false;
		}
	}

	async function removeAvatar() {
		if (!user) return;
		avatarError = null;
		avatarUploading = true;
		try {
			const response = await api.deleteAvatar(user.id);
			if (response.success) {
				await authStore.refreshUser();
			} else {
				avatarError = response.message || 'Failed to remove avatar.';
			}
		} catch (err: any) {
			avatarError = parseApiError(err).message || 'Failed to remove avatar.';
		} finally {
			avatarUploading = false;
		}
	}

	// ── Change password ──────────────────────────────────────────────────
	let currentPassword = '';
	let newPassword = '';
	let confirmPassword = '';
	let passwordErrors: PasswordChangeErrors = {};
	let passwordServerError: string | null = null;
	let passwordSubmitting = false;
	let passwordSuccess = false;

	$: passwordFieldsFilled = !!currentPassword && !!newPassword && !!confirmPassword;

	function clearPasswordFields() {
		currentPassword = '';
		newPassword = '';
		confirmPassword = '';
		passwordErrors = {};
	}

	async function handleChangePassword(event: Event) {
		event.preventDefault();
		passwordServerError = null;
		passwordSuccess = false;

		const { valid, errors } = validatePasswordChange({
			currentPassword,
			newPassword,
			confirmPassword
		});
		passwordErrors = errors;
		if (!valid) return;

		passwordSubmitting = true;
		try {
			const response = await api.changePassword(currentPassword, newPassword);
			if (response.success) {
				clearPasswordFields();
				passwordSuccess = true;
				setTimeout(() => (passwordSuccess = false), 4000);
			} else {
				passwordServerError = response.message || 'Failed to change password.';
			}
		} catch (err: any) {
			if (err?.response?.status === 429) {
				const retryAfter = err.response.headers?.['retry-after'];
				passwordServerError = retryAfter
					? `Too many attempts — try again in ${retryAfter}s.`
					: 'Too many attempts — try again shortly.';
			} else {
				const parsed = parseApiError(err);
				const mapped = passwordErrorsFromServer(parsed.fieldErrors);
				if (mapped) {
					passwordErrors = mapped;
				} else {
					passwordServerError = parsed.message || 'Failed to change password.';
				}
			}
		} finally {
			passwordSubmitting = false;
		}
	}

	nsfwFilterStore.init();

	// ── Notifications ────────────────────────────────────────────────────
	$: prefTypes = $notifications.prefTypes;
	$: prefsLoaded = $notifications.prefsLoaded;
	$: sound = $notifications.sound;
	let soundBusy = false;

	async function toggleSound(next: boolean) {
		soundBusy = true;
		try {
			await notifications.setSound(next);
		} finally {
			soundBusy = false;
		}
	}

	$: chatSound = $notifications.chat_sound;
	let chatSoundBusy = false;

	async function toggleChatSound(next: boolean) {
		chatSoundBusy = true;
		try {
			await notifications.setChatSound(next);
		} finally {
			chatSoundBusy = false;
		}
	}

	$: groupedPrefs = prefTypes.reduce<Record<string, NotificationTypePref[]>>((acc, t) => {
		(acc[t.category] ??= []).push(t);
		return acc;
	}, {});

	onMount(() => {
		if (!$notifications.prefsLoaded) notifications.loadPrefs();
	});

	const nsfwFilterOptions: { value: NsfwFilterMode; label: string }[] = [
		{ value: 'blur', label: 'Blur' },
		{ value: 'show', label: 'Show' },
		{ value: 'hide', label: 'Hide' }
	];

	const themeOptions: { value: ThemePref; label: string }[] = [
		{ value: 'system', label: 'System' },
		{ value: 'light', label: 'Light' },
		{ value: 'dark', label: 'Dark' }
	];

	$: sections = availableSettingsSections({
		contentRestricted: $nsfwFilterStore.restricted,
		notificationsEmpty: prefsLoaded && prefTypes.length === 0
	});
	$: activeSection = resolveSettingsSection($page.url, sections);
	let keyCount: number | null = null;

	function selectSection(id: SettingsSectionId) {
		void goto(settingsSectionUrl($page.url, id), { replaceState: true, keepFocus: true, noScroll: true });
	}
</script>

<LibraryShell
	title="Settings"
	persistKey="settings"
	count={activeSection === 'keys' ? keyCount : null}
	countLabel="keys"
	countLabelOne="key"
	{sections}
	section={activeSection}
	onSelectSection={selectSection}
>
	<div class="min-h-full bg-surface-2 p-4 sm:p-5">
		<div class="w-full max-w-6xl space-y-6">
			{#if isAdmin && $showResumeSetup}
				<Card class="flex items-center justify-between gap-3">
					<div class="min-w-0">
						<h2 class="text-sm font-semibold text-fg">Resume setup</h2>
						<p class="text-sm text-fg-muted mt-0.5">
							This instance isn't ready to generate yet. Pick the setup guide up where you left it.
						</p>
					</div>
					<Button variant="secondary" href="/setup">Resume setup</Button>
				</Card>
			{/if}
			{#if activeSection === 'account'}
				{#if user}
					<Card>
						<h2 class="label mb-3">Account</h2>
						<div class="flex items-center gap-4">
							<div class="relative flex-shrink-0">
								<button
									type="button"
									on:click={toggleAvatarPicker}
									disabled={avatarUploading}
									class="w-14 h-14 rounded-full overflow-hidden flex items-center justify-center
										text-accent-contrast text-xl font-bold transition-opacity hover:opacity-90
										disabled:opacity-60 disabled:cursor-not-allowed
										{showAvatarImage ? '' : 'bg-accent'}"
									aria-label="Change avatar"
								>
									{#if avatarUploading}
										<Spinner size="sm" />
									{:else if showAvatarImage}
										<img
											src={avatarUrl}
											alt=""
											class="w-full h-full object-cover"
											on:error={() => (avatarBroken = true)}
										/>
									{:else}
										{user.username.charAt(0).toUpperCase()}
									{/if}
								</button>
							</div>
							<div class="flex-1 min-w-0">
								<p class="text-fg font-medium text-base truncate">{user.username}</p>
								{#if user.email}
									<p class="text-fg-muted text-sm truncate">{user.email}</p>
								{/if}
								<p class="mt-0.5 font-mono text-xs uppercase tracking-wide text-fg-subtle md:hidden">
									{user.account_type} &middot; {accountCount} {accountCount === 1 ? 'account' : 'accounts'}
								</p>
								<div class="flex items-center gap-3 mt-2">
									<Button
										size="xs"
										variant="secondary"
										disabled={avatarUploading}
										onclick={toggleAvatarPicker}
									>
										Change avatar
									</Button>
									{#if avatarUrl}
										<button
											type="button"
											on:click={removeAvatar}
											disabled={avatarUploading}
											class="text-xs text-fg-subtle hover:text-danger transition-colors disabled:opacity-50"
										>
											Remove
										</button>
									{/if}
								</div>
								{#if avatarError}
									<p class="text-xs text-danger mt-1.5">{avatarError}</p>
								{/if}
							</div>
						</div>
						<div class="mt-3 md:hidden">
							<Button size="sm" variant="secondary" class="w-full" onclick={() => (accountsSheetOpen = true)}>
								Accounts
							</Button>
						</div>
						{#if accountsSheetOpen}
							<AccountsSheet onClose={() => (accountsSheetOpen = false)} />
						{/if}
						{#if avatarPickerOpen}
							<div class="mt-3">
								<MediaLoaderField
									name="avatar_pick"
									value={avatarPick}
									onChange={handleAvatarPick}
									config={{ accept: 'image/*' }}
									compact
									compactFullWidth
								/>
							</div>
						{/if}
					</Card>
				{/if}
				{#if user}
					<Card>
						<h2 class="label mb-3">Password</h2>
						{#if !hasLocalPassword}
							<div class="rounded-lg border border-line bg-surface-2 p-4">
								<div class="flex items-start gap-3">
									<Icon name="info" className="mt-0.5 h-4 w-4 flex-shrink-0 text-fg-subtle" />
									<div class="min-w-0 flex-1">
										<p class="text-sm font-medium text-fg">Password managed by your identity provider</p>
										<p class="mt-1 text-sm leading-relaxed text-fg-muted">This account signs in through single sign-on, so there is no PotionUI password to change. An administrator can set one if you also need to sign in with a username and password.</p>
									</div>
								</div>
							</div>
						{:else}
						<form on:submit={handleChangePassword} class="max-w-sm space-y-3">
							<div>
								<label for="current-password" class="label">Current password</label>
								<Input
									id="current-password"
									type="password"
									bind:value={currentPassword}
									invalid={!!passwordErrors.current}
									autocomplete="current-password"
									disabled={passwordSubmitting}
								/>
								{#if passwordErrors.current}
									<p class="text-xs text-danger mt-1">{passwordErrors.current}</p>
								{/if}
							</div>

							<div>
								<label for="new-password" class="label">New password</label>
								<Input
									id="new-password"
									type="password"
									bind:value={newPassword}
									invalid={!!passwordErrors.new}
									autocomplete="new-password"
									disabled={passwordSubmitting}
								/>
								{#if passwordErrors.new}
									<p class="text-xs text-danger mt-1">{passwordErrors.new}</p>
								{/if}
							</div>

							<div>
								<label for="confirm-password" class="label">Confirm new password</label>
								<Input
									id="confirm-password"
									type="password"
									bind:value={confirmPassword}
									invalid={!!passwordErrors.confirm}
									autocomplete="new-password"
									disabled={passwordSubmitting}
								/>
								{#if passwordErrors.confirm}
									<p class="text-xs text-danger mt-1">{passwordErrors.confirm}</p>
								{/if}
							</div>

							{#if passwordServerError}
								<Alert variant="danger">{passwordServerError}</Alert>
							{/if}

							{#if passwordSuccess}
								<Alert variant="success">Password changed successfully.</Alert>
							{/if}

							<Button
								type="submit"
								variant="primary"
								loading={passwordSubmitting}
								disabled={passwordSubmitting || !passwordFieldsFilled}
							>
								Change password
							</Button>
						</form>
						{/if}
					</Card>
				{/if}
			{:else if activeSection === 'plan'}
				<PlanUsageCard />
			{:else if activeSection === 'appearance'}
				<Card>
					<h2 class="label mb-3">Appearance</h2>
					<div class="flex items-center justify-between gap-4">
						<div>
							<p class="text-fg text-sm font-medium">Theme</p>
							<p class="text-fg-subtle text-xs mt-0.5">
								Dark is the primary theme; some admin screens are dark-only for now.
							</p>
						</div>
						<div class="flex rounded border border-line-strong p-0.5 bg-surface-2" role="radiogroup" aria-label="Theme">
							{#each themeOptions as option}
								<button
									role="radio"
									aria-checked={$themeStore.pref === option.value}
									class="px-3 py-1 text-xs font-medium rounded-sm transition-colors duration-100
										{$themeStore.pref === option.value
										? 'bg-signal/10 text-signal'
										: 'text-fg-muted hover:text-fg'}"
									on:click={() => themeStore.setPref(option.value)}
								>
									{option.label}
								</button>
							{/each}
						</div>
					</div>
				</Card>
			{:else if activeSection === 'content'}
				<Card>
					<h2 class="label mb-3">Content</h2>
					<div class="flex items-center justify-between gap-4">
						<div>
							<p class="text-fg text-sm font-medium">Sensitive media</p>
							<p class="text-fg-subtle text-xs mt-0.5">
								Blur click-to-reveal, show unfiltered, or hide gallery items the auto-tagger rated as NSFW.
							</p>
						</div>
						<div class="flex rounded border border-line-strong p-0.5 bg-surface-2" role="radiogroup" aria-label="Sensitive media">
							{#each nsfwFilterOptions as option}
								<button
									role="radio"
									aria-checked={$nsfwFilterStore.mode === option.value}
									class="px-3 py-1 text-xs font-medium rounded-sm transition-colors duration-100
										{$nsfwFilterStore.mode === option.value
										? 'bg-signal/10 text-signal'
										: 'text-fg-muted hover:text-fg'}"
									on:click={() => nsfwFilterStore.setMode(option.value)}
								>
									{option.label}
								</button>
							{/each}
						</div>
					</div>
				</Card>
			{:else if activeSection === 'notifications'}
				<div id="notifications" class="scroll-mt-6">
					<Card>
						<h2 class="label mb-3">Notifications</h2>
						{#if !prefsLoaded}
							<div class="flex items-center justify-center py-10">
								<Spinner />
							</div>
						{:else}
							<div class="flex items-center justify-between gap-4 pb-3 border-b border-line">
								<div>
									<p class="text-fg text-sm font-medium">Sound</p>
									<p class="text-fg-subtle text-xs mt-0.5">Play a chime for new notifications.</p>
								</div>
								<Switch checked={sound} busy={soundBusy} onchange={toggleSound} label="Notification sound" />
							</div>

							<div class="flex items-center justify-between gap-4 py-3 border-b border-line">
								<div>
									<p class="text-fg text-sm font-medium">Assistant replies</p>
									<p class="text-fg-subtle text-xs mt-0.5">Play a sound when the assistant finishes a reply.</p>
								</div>
								<Switch
									checked={chatSound}
									busy={chatSoundBusy}
									onchange={toggleChatSound}
									label="Assistant reply sound"
								/>
							</div>

							{#each Object.entries(groupedPrefs) as [category, types] (category)}
								<div class="pt-4 pb-1">
									<p class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">{category}</p>
								</div>
								{#each types as type (type.key)}
									<label
										class="flex items-start gap-3 -mx-4 px-4 py-2.5 cursor-pointer hover:bg-surface-2/50 transition-colors"
									>
										<input
											type="checkbox"
											checked={type.enabled}
											on:change={(e) =>
												notifications.setTypeEnabled(type.key, (e.currentTarget as HTMLInputElement).checked)}
											class="mt-0.5 h-4 w-4 rounded border-line-strong accent-signal cursor-pointer"
										/>
										<span class="min-w-0">
											<span class="block text-sm text-fg">{type.label}</span>
											{#if type.description}
												<span class="block text-xs text-fg-muted mt-0.5 leading-snug">
													{type.description}
												</span>
											{/if}
										</span>
									</label>
								{/each}
							{/each}

							{#if prefTypes.length === 0}
								<div class="py-10 text-center">
									<p class="text-sm text-fg-muted">No configurable notification types.</p>
								</div>
							{/if}
						{/if}
					</Card>
				</div>
			{:else if activeSection === 'keys'}
				<ApiKeysSection {isAdmin} onCount={(count) => (keyCount = count)} />
			{/if}
		</div>
	</div>
</LibraryShell>
