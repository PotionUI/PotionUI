<script lang="ts">
	import { fly } from 'svelte/transition';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { notifications } from '$lib/stores/notifications';
	import { nsfwFilterStore, type NsfwFilterMode } from '$lib/stores/nsfwFilter';
	import Icon from './Icon.svelte';

	let open = false;
	let menuEl: HTMLDivElement;
	let avatarBroken = false;

	$: user = $authStore.user;
	$: unreadCount = $notifications.unreadCount;
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

	function toggle() {
		open = !open;
	}

	function close() {
		open = false;
	}

	function openNotifications() {
		notifications.openPanel();
		close();
	}

	function openSettings() {
		close();
		goto('/settings');
	}

	function handleLogout() {
		close();
		authStore.logout();
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
			{#if unreadCount > 0}
				<span
					class="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-signal ring-2 ring-canvas"
					aria-hidden="true"
				></span>
			{/if}
		</button>

		<!-- Popover menu -->
		{#if open}
			<div
				class="absolute left-full bottom-0 ml-2 z-[60] min-w-[220px] bg-surface-2 border border-line-strong
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
					<div class="min-w-0">
						<p class="text-sm font-medium text-fg truncate">{user.username}</p>
						<p class="text-2xs font-medium text-fg-subtle uppercase tracking-wide">
							{user.account_type}
						</p>
					</div>
				</div>

				<!-- Items -->
				<div class="p-1">
					<button
						type="button"
						role="menuitem"
						on:click={openNotifications}
						class="{itemClass} text-fg-muted hover:text-fg hover:bg-surface-3"
					>
						<Icon name="bell" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
						<span>Notifications</span>
						{#if unreadCount > 0}
							<span
								class="ml-auto text-2xs font-mono tabular-nums px-1.5 py-0.5 rounded-full bg-signal/15 text-signal"
							>
								{unreadCount}
							</span>
						{/if}
					</button>

					<button
						type="button"
						role="menuitem"
						on:click={openSettings}
						class="{itemClass} text-fg-muted hover:text-fg hover:bg-surface-3"
					>
						<Icon name="settings" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
						<span>Settings</span>
					</button>
				</div>

				<!-- Sensitive content mode -->
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

				<div class="p-1 border-t border-line">
					<button
						type="button"
						role="menuitem"
						on:click={handleLogout}
						class="{itemClass} text-fg-muted hover:text-danger hover:bg-surface-3"
					>
						<Icon name="logout" className="w-4 h-4 shrink-0" strokeWidth={1.5} />
						<span>Logout</span>
					</button>
				</div>
			</div>
		{/if}
	</div>
{/if}
