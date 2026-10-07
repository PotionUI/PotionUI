<script lang="ts">
	import { fly } from 'svelte/transition';
	import { authStore } from '$lib/stores/auth';
	import { nsfwFilterStore } from '$lib/stores/nsfwFilter';
	import UserPanel from './accounts/UserPanel.svelte';

	let open = false;
	let menuEl: HTMLDivElement;
	let avatarBroken = false;

	$: user = $authStore.user;
	$: if (user?.avatar_url) avatarBroken = false;
	$: showAvatarImage = !!user?.avatar_url && !avatarBroken;

	nsfwFilterStore.init();

	function toggle() {
		open = !open;
	}

	function close() {
		open = false;
	}

	function onWindowClick(e: MouseEvent) {
		if (open && menuEl && !menuEl.contains(e.target as Node)) close();
	}

	function onWindowKey(e: KeyboardEvent) {
		if (open && e.key === 'Escape') close();
	}
</script>

<svelte:window on:click={onWindowClick} on:keydown={onWindowKey} />

{#if user}
	<div class="relative" bind:this={menuEl}>
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

		{#if open}
			<div
				class="absolute left-full bottom-0 ml-2 z-overlay w-96 max-w-[calc(100vw-16px)] bg-surface-2 border border-line-strong
					rounded-xl shadow-floating overflow-hidden"
				role="menu"
				transition:fly={{ x: -4, duration: 120 }}
			>
				<div
					class="overflow-y-auto p-2"
					style="max-height: min(calc(100vh - 16px), 720px);"
					data-testid="user-menu-scroll"
				>
					<UserPanel onClose={close} />
				</div>
			</div>
		{/if}
	</div>
{/if}
