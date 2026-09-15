<script lang="ts">
	import { fly, fade } from 'svelte/transition';
	import { goto } from '$app/navigation';
	import { notifications, groupByDay } from '$lib/stores/notifications';
	import Button from '$lib/components/ui/Button.svelte';
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import NotificationItem from './NotificationItem.svelte';

	let loadingMore = $state(false);
	let selectedCategory = $state('all');

	let open = $derived($notifications.panelOpen);
	let items = $derived($notifications.items);
	let unreadCount = $derived($notifications.unreadCount);
	let categories = $derived(Array.from(new Set($notifications.prefTypes.map((t) => t.category))));
	let filteredItems = $derived(
		selectedCategory === 'all' ? items : items.filter((i) => i.category === selectedCategory)
	);
	let groupedItems = $derived(groupByDay(filteredItems));

	$effect(() => {
		if (open && !$notifications.prefsLoaded) notifications.loadPrefs();
	});

	function categoryLabel(category: string): string {
		return category.charAt(0).toUpperCase() + category.slice(1);
	}

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
	<div
		class="fixed inset-0 z-[9990] bg-canvas/60 backdrop-blur-sm"
		transition:fade={{ duration: 150 }}
		onclick={close}
		role="presentation"
	></div>

	<aside
		class="fixed top-0 right-0 z-[9991] h-screen w-full max-w-sm flex flex-col bg-surface-1 border-l border-line-strong shadow-overlay rounded-l-xl"
		transition:fly={{ x: 320, duration: 200 }}
		aria-label="Notifications"
	>
		<header class="flex items-center gap-2 px-3 h-header border-b border-line flex-shrink-0">
			<Icon name="bell" className="w-3.5 h-3.5 text-fg-muted" strokeWidth={1.7} />
			<h2 class="text-sm font-semibold text-fg">Notifications</h2>
			{#if unreadCount > 0}
				<span
					class="inline-flex items-center justify-center min-w-4 h-4 px-1 rounded bg-signal/15 text-signal text-2xs font-medium tabular-nums"
				>
					{unreadCount}
				</span>
			{/if}
			<div class="flex-1"></div>
			<div class="flex items-center gap-1">
				<Tooltip text="Mark all read" position="bottom">
					<IconButton
						icon="check"
						label="Mark all read"
						size="sm"
						disabled={unreadCount === 0}
						onclick={() => notifications.markAllRead()}
					/>
				</Tooltip>
				<Tooltip text="Clear" position="bottom">
					<IconButton
						icon="trash"
						label="Clear notifications"
						size="sm"
						disabled={items.length === 0}
						onclick={() => notifications.clear()}
					/>
				</Tooltip>
				<Tooltip text="Notification settings" position="bottom">
					<IconButton icon="settings" label="Notification settings" size="sm" onclick={goToSettings} />
				</Tooltip>
				<Tooltip text="Close" position="bottom">
					<IconButton icon="close" label="Close notifications" size="sm" onclick={close} />
				</Tooltip>
			</div>
		</header>

		{#if items.length > 0 && categories.length > 0}
			<div
				class="flex items-center gap-1 px-2.5 py-2 border-b border-line overflow-x-auto flex-shrink-0"
				role="group"
				aria-label="Filter by kind"
			>
				<button
					type="button"
					class="px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors
						{selectedCategory === 'all' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:text-fg'}"
					aria-pressed={selectedCategory === 'all'}
					onclick={() => (selectedCategory = 'all')}
				>
					All
				</button>
				{#each categories as category (category)}
					<button
						type="button"
						class="px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors
							{selectedCategory === category ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:text-fg'}"
						aria-pressed={selectedCategory === category}
						onclick={() => (selectedCategory = category)}
					>
						{categoryLabel(category)}
					</button>
				{/each}
			</div>
		{/if}

		<div class="flex-1 overflow-y-auto">
			{#if items.length === 0}
				<div
					class="flex flex-col items-center justify-center h-full px-6 py-10 text-center gap-2 bg-[radial-gradient(rgb(var(--line-strong))_1px,transparent_1px)] bg-[length:14px_14px]"
				>
					<p class="text-sm text-fg-muted">You're all caught up</p>
					<p class="text-xs text-fg-subtle">New notifications appear here</p>
				</div>
			{:else if groupedItems.length === 0}
				<p class="px-4 py-8 text-xs text-fg-subtle text-center">No notifications in this category</p>
			{:else}
				{#each groupedItems as group (group.bucket)}
					<div class="px-3 pt-2 pb-1 font-mono text-2xs uppercase tracking-wide text-fg-subtle">
						{group.bucket}
					</div>
					{#each group.items as item (item.id)}
						<NotificationItem
							notification={item}
							onMarkRead={(id) => notifications.markRead(id)}
							onRemove={(id) => notifications.remove(id)}
						/>
					{/each}
				{/each}
			{/if}
		</div>

		{#if items.length > 0}
			<div class="p-3 flex justify-center border-t border-line flex-shrink-0">
				<Button variant="secondary" size="sm" loading={loadingMore} onclick={loadMore}>
					Load more
				</Button>
			</div>
		{/if}
	</aside>
{/if}
