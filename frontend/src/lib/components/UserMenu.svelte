<script lang="ts">
	import { fly } from 'svelte/transition';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { nsfwFilterStore, type NsfwFilterMode } from '$lib/stores/nsfwFilter';
	import { autoOrganizeCounts, autoOrganizeHref } from '$lib/stores/autoOrganizeCounts';
	import { readLastSubject } from '$lib/organize/lastSubject';
	import Icon from './Icon.svelte';
	import MenuUsage from '$lib/plans/components/MenuUsage.svelte';
	import { limits, storageUsed } from '$lib/plans/store';
	import { accountsStore } from '$lib/stores/accounts';
	import { MAX_ACCOUNTS, type StoredAccount } from '$lib/stores/accountRegistry';
	import {
		logoutAccount,
		logoutActiveAccount,
		logoutEverywhere,
		refreshAccountList,
		signInAgain,
		startAddAccount,
		switchAccount
	} from '$lib/stores/accountActions';
	import AccountRows from './accounts/AccountRows.svelte';

	let open = false;
	let menuEl: HTMLDivElement;
	let avatarBroken = false;

	$: user = $authStore.user;
	// Reset the broken-image fallback whenever the avatar URL itself changes
	// (upload/remove mint a new filename, so a stale broken flag never sticks).
	$: if (user?.avatar_url) avatarBroken = false;
	$: showAvatarImage = !!user?.avatar_url && !avatarBroken;

	nsfwFilterStore.init();

	const nsfwFilterOptions: { value: NsfwFilterMode; label: string }[] = [
		{ value: 'blur', label: 'Blur' },
		{ value: 'show', label: 'Show' },
		{ value: 'hide', label: 'Hide' }
	];

	$: otherAccounts = $accountsStore.accounts.filter((a) => a.userId !== user?.id);
	$: accountTotal = Math.max($accountsStore.accounts.length, 1);
	$: canAdd = accountTotal < MAX_ACCOUNTS;

	$: counts = Object.values($autoOrganizeCounts).filter((c) => c !== null);
	$: autoActive = counts.reduce((sum, c) => sum + c.active, 0);
	$: autoAttention = counts.some((c) => c.needsAttention > 0);

	function toggle() {
		open = !open;
		if (open) refreshAccountList();
		if (open && counts.length === 0) void autoOrganizeCounts.load();
	}

	function close() {
		open = false;
	}

	function openSettings() {
		close();
		goto('/settings');
	}

	function openAutoOrganize() {
		close();
		goto(autoOrganizeHref(readLastSubject()));
	}

	function openPlan() {
		close();
		goto('/settings#plan');
	}

	function handleLogout() {
		close();
		logoutActiveAccount();
	}

	function handleLogoutAll() {
		close();
		void logoutEverywhere();
	}

	function handleAddAccount() {
		close();
		startAddAccount();
	}

	function handleSwitch(account: StoredAccount) {
		close();
		void switchAccount(account);
	}

	function handleSignInAgain(account: StoredAccount) {
		close();
		signInAgain(account);
	}

	function onWindowClick(e: MouseEvent) {
		if (open && menuEl && !menuEl.contains(e.target as Node)) close();
	}

	function onWindowKey(e: KeyboardEvent) {
		if (open && e.key === 'Escape') close();
	}

	const itemClass =
		'flex items-center gap-2.5 w-full px-2.5 py-2 rounded text-sm text-left transition-colors';
</script>

<svelte:window on:click={onWindowClick} on:keydown={onWindowKey} />

{#if user}
	<div class="relative" bind:this={menuEl}>
		<!-- Avatar trigger -->
		<button
			type="button"
			on:click={toggle}
			class="relative w-8 h-8 rounded-full transition-all hover:opacity-90
				{open ? 'ring-2 ring-signal ring-offset-2 ring-offset-canvas' : ''}"
			aria-haspopup="menu"
			aria-expanded={open}
			aria-label="Account menu"
		>
			<span
				class="w-full h-full rounded-full overflow-hidden flex items-center justify-center
					text-accent-contrast text-sm font-semibold
					{showAvatarImage ? '' : 'bg-accent'}"
			>
				{#if showAvatarImage}
					<img
						src={user.avatar_url}
						alt=""
						class="w-full h-full object-cover"
						on:error={() => (avatarBroken = true)}
					/>
				{:else}
					{user.username.charAt(0).toUpperCase()}
				{/if}
			</span>
		</button>

		<!-- Popover menu -->
		{#if open}
			<div
				class="absolute left-full bottom-0 ml-2 z-overlay min-w-[220px] bg-surface-2 border border-line-strong
					rounded-xl shadow-floating overflow-hidden"
				role="menu"
				transition:fly={{ x: -4, duration: 120 }}
			>
				<!-- Account header -->
				<div class="px-3 py-2.5 border-b border-line flex items-center gap-2.5">
					<div
						class="relative w-9 h-9 rounded-full overflow-hidden flex items-center justify-center
							text-accent-contrast text-sm font-semibold flex-shrink-0
							{showAvatarImage ? '' : 'bg-accent'}"
					>
						{#if showAvatarImage}
							<img
								src={user.avatar_url}
								alt=""
								class="w-full h-full object-cover"
								on:error={() => (avatarBroken = true)}
							/>
						{:else}
							{user.username.charAt(0).toUpperCase()}
						{/if}
					</div>
					<div class="min-w-0 flex-1">
						<p class="text-sm font-medium text-fg truncate">{user.username}</p>
						<p class="text-2xs font-medium text-fg-subtle uppercase tracking-wide">
							{user.account_type}
						</p>
					</div>
					<span
						class="flex shrink-0 items-center gap-1 font-mono text-2xs uppercase tracking-wide text-signal"
						data-testid="user-menu-active"
					>
						<Icon name="check" className="w-3.5 h-3.5" strokeWidth={1.5} />
						Active
					</span>
				</div>

				{#if otherAccounts.length > 0}
					<div class="border-b border-line p-1" data-testid="user-menu-accounts">
						<p
							class="flex items-center justify-between px-2.5 pb-1 pt-1.5 font-mono text-2xs uppercase tracking-wide text-fg-subtle"
						>
							<span>Other accounts</span>
							<span class="tabular-nums">{accountTotal} / {MAX_ACCOUNTS}</span>
						</p>
						<AccountRows
							accounts={otherAccounts}
							onSwitch={handleSwitch}
							onSignInAgain={handleSignInAgain}
							onRemove={logoutAccount}
						/>
					</div>
				{/if}

				<div class="p-1 {otherAccounts.length > 0 ? '' : 'border-b border-line'}">
					<button
						type="button"
						role="menuitem"
						data-testid="user-menu-add-account"
						disabled={!canAdd}
						title={canAdd ? undefined : 'Remove an account first'}
						on:click={handleAddAccount}
						class="{itemClass} text-fg-muted hover:text-fg hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50"
					>
						<span
							class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-dashed border-line-strong"
						>
							<Icon name="plus" className="w-3.5 h-3.5" strokeWidth={1.5} />
						</span>
						<span>Add account</span>
					</button>
				</div>

				<!-- Items -->
				<div class="p-1">
					<button
						type="button"
						role="menuitem"
						on:click={openSettings}
						class="{itemClass} text-fg-muted hover:text-fg hover:bg-surface-3"
					>
						<Icon name="settings" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
						<span>Settings</span>
					</button>
					<button
						type="button"
						role="menuitem"
						data-testid="user-menu-auto-organize"
						on:click={openAutoOrganize}
						class="{itemClass} text-fg-muted hover:text-fg hover:bg-surface-3"
					>
						<Icon name="wand" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
						<span class="flex-1">Auto-organize</span>
						{#if counts.length > 0}
							{#if autoAttention}
								<span
									class="h-1.5 w-1.5 shrink-0 rounded-full bg-warning"
									role="img"
									aria-label="Needs attention"
									data-testid="user-menu-auto-organize-attention"
								></span>
							{/if}
							<span
								class="shrink-0 font-mono text-2xs tabular-nums text-fg-subtle"
								data-testid="user-menu-auto-organize-count">{autoActive}</span
							>
						{/if}
					</button>
				</div>

				{#if $limits.length > 0 || $storageUsed !== null}
					<div class="px-2.5 pb-2.5 pt-1">
						<MenuUsage rows={$limits} storageBytes={$storageUsed} onOpen={openPlan} />
					</div>
				{/if}

				{#if !$nsfwFilterStore.restricted}
				<div class="px-2.5 py-2 border-t border-line">
					<p class="pb-1.5 text-2xs font-medium text-fg-subtle uppercase tracking-wide">
						Sensitive content
					</p>
					<div
						class="flex rounded border border-line-strong p-0.5 bg-surface-3"
						role="radiogroup"
						aria-label="Sensitive content"
					>
						{#each nsfwFilterOptions as option}
							<button
								type="button"
								role="radio"
								aria-checked={$nsfwFilterStore.mode === option.value}
								class="flex-1 px-2 py-1 text-2xs font-medium rounded-sm transition-colors duration-100
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
				{/if}

				<div class="p-1 border-t border-line">
					<button
						type="button"
						role="menuitem"
						data-testid="user-menu-logout"
						on:click={handleLogout}
						class="{itemClass} text-fg-muted hover:text-danger hover:bg-surface-3"
					>
						<Icon name="logout" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
						<span>Log out {user.username}</span>
					</button>
					{#if $accountsStore.accounts.length > 1}
						<button
							type="button"
							role="menuitem"
							data-testid="user-menu-logout-all"
							on:click={handleLogoutAll}
							class="{itemClass} text-fg-muted hover:text-danger hover:bg-surface-3"
						>
							<Icon name="group" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
							<span class="flex-1">Log out of all accounts</span>
							<span class="shrink-0 font-mono text-2xs tabular-nums text-fg-subtle">
								{$accountsStore.accounts.length}
							</span>
						</button>
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
