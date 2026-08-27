<script lang="ts">
	import { onMount } from 'svelte';
	import { authStore } from '$lib/stores/auth';
	import { api } from '$lib/services/api/index';
	import type { McpToken, McpTokenCreated } from '$lib/services/api/mcp';
	import { themeStore, type ThemePref } from '$lib/stores/theme';
	import { nsfwFilterStore, type NsfwFilterMode } from '$lib/stores/nsfwFilter';
	import { notifications } from '$lib/stores/notifications';
	import type { NotificationTypePref } from '$lib/services/api/notifications';
	import { Badge, Button, Card, Input, PageContainer, PageHeader, Spinner, Switch, Alert } from '$lib/components/ui';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import { resolveAvatarFileFromMediaItem } from './avatarMediaPick';
	import { validatePasswordChange, type PasswordChangeErrors } from '$lib/utils/passwordValidation';
	import { copyText } from '$lib/utils/clipboard';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';

	$: user = $authStore.user;

	// Unwraps the backend's error_response() detail shape (see auth store) so
	// 400/429 responses surface their real message instead of "[object Object]".
	// Pydantic 422 validation errors carry a list detail; use its first message.
	function extractApiErrorMessage(error: any): string | undefined {
		const detail = error?.response?.data?.detail;
		if (typeof detail === 'string') return detail;
		if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;
		if (detail && typeof detail.message === 'string') return detail.message;
		return error?.message;
	}

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
			avatarError = extractApiErrorMessage(err) || 'Failed to upload avatar.';
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
			avatarError = extractApiErrorMessage(err) || 'Failed to remove avatar.';
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
				passwordServerError = extractApiErrorMessage(err) || 'Failed to change password.';
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

	$: groupedPrefs = prefTypes.reduce<Record<string, NotificationTypePref[]>>((acc, t) => {
		(acc[t.category] ??= []).push(t);
		return acc;
	}, {});

	// ── MCP ───────────────────────────────────────────────────────────────
	// `mcpEnabled` (GET /api/mcp/status) is the AND of the global and per-user
	// admin flags. Even when it's false, existing tokens stay listable and
	// revocable - only minting a new one and the connection instructions are
	// gated, per the maintainer's call: revoking should never be blocked by
	// the same flag that blocks new connections.
	let mcpLoading = true;
	let mcpLoadError: string | null = null;
	let mcpEnabled = true;
	let mcpTokens: McpToken[] = [];
	let mcpCreateName = '';
	let mcpCreating = false;
	let mcpCreateError: string | null = null;
	let mcpRevealedToken: McpTokenCreated | null = null;
	let mcpCopied = false;
	let mcpConfigCopied = false;
	let mcpRevokingId: string | null = null;

	$: mcpUrl = typeof window !== 'undefined' ? `${window.location.origin}/api/mcp` : '/api/mcp';
	$: mcpConfigSnippet = `URL: ${mcpUrl}\nAuthorization: Bearer ${mcpRevealedToken?.token ?? '<your-token>'}`;

	async function loadMcpStatus() {
		try {
			const response = await api.getMcpStatus();
			if (response.success && response.data) mcpEnabled = response.data.enabled;
		} catch {
			// Non-fatal: the token list below still loads and stays functional
			// even if the status check itself fails, so leave the notice off.
		}
	}

	async function loadMcpTokens() {
		mcpLoading = true;
		mcpLoadError = null;
		try {
			const response = await api.listMcpTokens();
			if (response.success) {
				mcpTokens = response.data ?? [];
			} else {
				mcpLoadError = response.message || 'Failed to load MCP tokens.';
			}
		} catch (err: any) {
			mcpLoadError = extractApiErrorMessage(err) || 'Failed to load MCP tokens.';
		} finally {
			mcpLoading = false;
		}
	}

	async function handleCreateMcpToken(event: Event) {
		event.preventDefault();
		const name = mcpCreateName.trim();
		if (!name) return;

		mcpCreating = true;
		mcpCreateError = null;
		try {
			const response = await api.createMcpToken(name);
			if (response.success && response.data) {
				mcpRevealedToken = response.data;
				mcpCreateName = '';
				await loadMcpTokens();
			} else {
				mcpCreateError = response.message || 'Failed to create token.';
			}
		} catch (err: any) {
			mcpCreateError = extractApiErrorMessage(err) || 'Failed to create token.';
		} finally {
			mcpCreating = false;
		}
	}

	async function handleRevokeMcpToken(tokenId: string, name: string) {
		if (
			!(await confirmDialog({
				title: `Revoke "${name}"?`,
				message: 'Any client using this token will immediately lose access. This cannot be undone.',
				variant: 'danger'
			}))
		)
			return;

		mcpRevokingId = tokenId;
		try {
			const response = await api.revokeMcpToken(tokenId);
			if (response.success) {
				if (mcpRevealedToken?.id === tokenId) mcpRevealedToken = null;
				await loadMcpTokens();
			} else {
				toasts.error(response.message || 'Failed to revoke token.');
			}
		} catch (err: any) {
			toasts.error(extractApiErrorMessage(err) || 'Failed to revoke token.');
		} finally {
			mcpRevokingId = null;
		}
	}

	async function copyMcpToken() {
		if (!mcpRevealedToken) return;
		const ok = await copyText(mcpRevealedToken.token);
		if (ok) {
			mcpCopied = true;
			setTimeout(() => (mcpCopied = false), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}

	async function copyMcpConfig() {
		const ok = await copyText(mcpConfigSnippet);
		if (ok) {
			mcpConfigCopied = true;
			setTimeout(() => (mcpConfigCopied = false), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}

	onMount(() => {
		if (!$notifications.prefsLoaded) notifications.loadPrefs();
		loadMcpStatus();
		loadMcpTokens();
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

	const icons: Record<string, string> = {
		logout: 'M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9'
	};

	function handleLogout() {
		authStore.logout();
	}
</script>

<div class="min-h-screen bg-canvas text-fg">
	<PageHeader title="Settings" description="Manage your account and application settings" sticky={false} />
	<PageContainer width="sm" class="space-y-6">

		<!-- User Profile Card -->
		{#if user}
			<Card>
				<h2 class="label mb-3">Account</h2>
				<div class="flex items-center gap-4">
					<!-- Avatar -->
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
					<!-- User Info -->
					<div class="flex-1 min-w-0">
						<p class="text-fg font-medium text-base truncate">{user.username}</p>
						{#if user.email}
							<p class="text-fg-muted text-sm truncate">{user.email}</p>
						{/if}
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

		<!-- Password -->
		{#if user}
			<Card>
				<h2 class="label mb-3">Password</h2>
				<form on:submit={handleChangePassword} class="space-y-3">
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
			</Card>
		{/if}

		<!-- Appearance -->
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

		<!-- Content -->
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

		<!-- Notifications -->
		<div id="notifications" class="scroll-mt-6">
			<Card>
				<h2 class="label mb-3">Notifications</h2>
				{#if !prefsLoaded}
					<div class="flex items-center justify-center py-10">
						<Spinner />
					</div>
				{:else}
					<!-- Sound toggle -->
					<div class="flex items-center justify-between gap-4 pb-3 border-b border-line">
						<div>
							<p class="text-fg text-sm font-medium">Sound</p>
							<p class="text-fg-subtle text-xs mt-0.5">Play a chime for new notifications.</p>
						</div>
						<Switch checked={sound} busy={soundBusy} onchange={toggleSound} label="Notification sound" />
					</div>

					<!-- Type toggles grouped by category -->
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

		<!-- MCP -->
		<Card>
			<h2 class="label mb-3">MCP</h2>
			<p class="text-fg-subtle text-xs mb-4">
				Connect an external AI client to PotionUI over MCP. It acts as you — same presets,
				same library, same permissions.
			</p>

			{#if mcpLoading}
				<div class="flex items-center justify-center py-8">
					<Spinner />
				</div>
			{:else}
				<div class="space-y-4">
					{#if !mcpEnabled}
						<Alert variant="info">
							An administrator has disabled MCP access. Existing tokens below can still be
							reviewed and revoked, but new connections won't be accepted until it's re-enabled.
						</Alert>
					{/if}

					{#if mcpLoadError}
						<Alert variant="danger">{mcpLoadError}</Alert>
					{/if}

					{#if mcpEnabled}
						{#if mcpRevealedToken}
							<div class="rounded border border-line-strong bg-surface-2 p-3 space-y-2">
								<p class="text-xs font-medium text-warning">
									Copy this token now — you won't see it again.
								</p>
								<div class="flex items-center gap-2">
									<code
										class="flex-1 min-w-0 truncate font-mono text-xs text-fg bg-canvas rounded px-2 py-1.5 border border-line"
									>
										{mcpRevealedToken.token}
									</code>
									<Button
										size="xs"
										variant="secondary"
										icon={mcpCopied ? 'check' : 'copy'}
										onclick={copyMcpToken}
									>
										{mcpCopied ? 'Copied' : 'Copy'}
									</Button>
								</div>
								<Button size="xs" variant="ghost" onclick={() => (mcpRevealedToken = null)}>Done</Button>
							</div>
						{/if}

						<form on:submit={handleCreateMcpToken} class="flex items-end gap-2">
							<div class="flex-1">
								<label for="mcp-token-name" class="label">New token name</label>
								<Input
									id="mcp-token-name"
									bind:value={mcpCreateName}
									placeholder="e.g. Claude Desktop"
									disabled={mcpCreating}
								/>
							</div>
							<Button
								type="submit"
								variant="primary"
								size="sm"
								loading={mcpCreating}
								disabled={mcpCreating || !mcpCreateName.trim()}
							>
								Create token
							</Button>
						</form>
						{#if mcpCreateError}
							<p class="text-xs text-danger">{mcpCreateError}</p>
						{/if}
					{/if}

					{#if mcpTokens.length === 0}
						<p class="text-xs text-fg-subtle py-4 text-center">No tokens yet.</p>
					{:else}
						<div class="divide-y divide-line rounded border border-line overflow-hidden">
							{#each mcpTokens as token (token.id)}
								<div
									class="flex items-center justify-between gap-3 px-3 py-2.5 {token.revoked_at ? 'opacity-50' : ''}"
								>
									<div class="min-w-0">
										<p class="text-sm text-fg truncate">{token.name}</p>
										<p class="font-mono text-2xs text-fg-subtle tabular-nums truncate">
											{token.token_prefix}&hellip;
											· created {timeAgo(token.created_at)}
											{#if token.last_used_at}
												· last used {timeAgo(token.last_used_at)}
											{:else}
												· never used
											{/if}
											{#if token.revoked_at}
												· revoked {timeAgo(token.revoked_at)}
											{/if}
										</p>
									</div>
									{#if token.revoked_at}
										<Badge variant="neutral" size="sm" class="flex-shrink-0">Revoked</Badge>
									{:else}
										<Button
											size="xs"
											variant="ghost"
											class="text-danger hover:text-danger flex-shrink-0"
											loading={mcpRevokingId === token.id}
											onclick={() => handleRevokeMcpToken(token.id, token.name)}
										>
											Revoke
										</Button>
									{/if}
								</div>
							{/each}
						</div>
					{/if}

					{#if mcpEnabled}
						<div>
							<p class="text-fg text-sm font-medium mb-1">Client configuration</p>
							<pre
								class="rounded border border-line bg-surface-2 p-3 font-mono text-2xs text-fg-muted whitespace-pre-wrap break-all">{mcpConfigSnippet}</pre>
							<Button
								size="xs"
								variant="ghost"
								class="mt-1.5"
								icon={mcpConfigCopied ? 'check' : 'copy'}
								onclick={copyMcpConfig}
							>
								{mcpConfigCopied ? 'Copied' : 'Copy config'}
							</Button>
						</div>
					{/if}
				</div>
			{/if}
		</Card>

		<!-- Logout -->
		<Card padding="none" class="overflow-hidden">
			<button
				on:click={handleLogout}
				class="w-full flex items-center gap-3 px-4 py-3 hover:bg-surface-2 transition-colors group text-left"
			>
				<div class="w-8 h-8 rounded-lg bg-danger/10 group-hover:bg-danger/20 flex items-center justify-center flex-shrink-0 transition-colors">
					<svg class="w-4 h-4 text-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d={icons.logout}
						/>
					</svg>
				</div>
				<span class="text-danger text-sm font-medium">Logout</span>
			</button>
		</Card>
	</PageContainer>
</div>
