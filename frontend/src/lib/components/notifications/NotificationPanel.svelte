<script lang="ts">
	import { fly, fade } from 'svelte/transition';
	import { goto } from '$app/navigation';
	import { notifications } from '$lib/stores/notifications';
	import Button from '$lib/components/ui/Button.svelte';
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import NotificationItem from './NotificationItem.svelte';

	let loadingMore = $state(false);

	let open = $derived($notifications.panelOpen);
	let items = $derived($notifications.items);
	let unreadCount = $derived($notifications.unreadCount);

	function close() {
		notifications.closePanel();
	}

	function goToSettings() {
		close();
		goto('/settings#notifications');
	}

	async function loadMore() {
		loadingMore = true;
		try {
			await notifications.loadMore();
		} finally {
			loadingMore = false;
		}
	}
</script>

{#if open}
	<!-- Scrim -->
	<div
		class="fixed inset-0 z-[9990] bg-canvas/60 backdrop-blur-sm"
		transition:fade={{ duration: 150 }}
		onclick={close}
		role="presentation"
	></div>

	<!-- Panel -->
	<aside
		class="fixed top-0 right-0 z-[9991] h-screen w-full max-w-sm flex flex-col bg-surface-1 border-l border-line-strong shadow-overlay rounded-l-xl"
		transition:fly={{ x: 320, duration: 200 }}
		aria-label="Notifications"
	>
		<!-- Header -->
		<header class="flex items-center justify-between px-4 h-header border-b border-line flex-shrink-0">
			<div class="flex items-center gap-2">
				<h2 class="text-sm font-semibold text-fg">Notifications</h2>
				{#if unreadCount > 0}
					<span
						class="inline-flex items-center justify-center min-w-4 h-4 px-1 rounded bg-signal/15 text-signal text-2xs font-medium tabular-nums"
					>
						{unreadCount}
					</span>
				{/if}
			</div>
			<div class="flex items-center gap-1">
				<Button
					variant="ghost"
					size="xs"
					disabled={unreadCount === 0}
					onclick={() => notifications.markAllRead()}
				>
					Mark all read
				</Button>
				<Button
					variant="ghost"
					size="xs"
					disabled={items.length === 0}
					onclick={() => notifications.clear()}
				>
					Clear
				</Button>
				<IconButton icon="settings" label="Notification settings" size="sm" onclick={goToSettings} />
				<IconButton icon="close" label="Close notifications" size="sm" onclick={close} />
			</div>
		</header>

		<!-- List -->
		<div class="flex-1 overflow-y-auto">
			{#if items.length === 0}
				<div class="flex flex-col items-center justify-center h-full px-6 text-center gap-3">
					<div class="text-fg-subtle">
						<svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="1.5"
								d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
							/>
						</svg>
					</div>
					<p class="text-sm text-fg-muted">You're all caught up</p>
					<p class="text-xs text-fg-subtle">New notifications will appear here.</p>
				</div>
			{:else}
				{#each items as item (item.id)}
					<NotificationItem
						notification={item}
						onMarkRead={(id) => notifications.markRead(id)}
						onRemove={(id) => notifications.remove(id)}
					/>
				{/each}

				<div class="p-3 flex justify-center">
					<Button variant="secondary" size="sm" loading={loadingMore} onclick={loadMore}>
						Load more
					</Button>
				</div>
			{/if}
		</div>
	</aside>
{/if}
