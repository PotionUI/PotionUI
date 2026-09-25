<script lang="ts" generics="Item">
	import type { Snippet } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Input, EmptyState } from '$lib/components/ui';

	let {
		isOpen,
		title,
		description,
		items,
		getId,
		getSearchText,
		searchPlaceholder = 'Search…',
		confirmLabel = 'Assign',
		emptyTitle = 'Nothing to pick from',
		busy = false,
		onConfirm,
		onClose,
		row
	}: {
		isOpen: boolean;
		title: string;
		description?: string;
		items: readonly Item[];
		getId: (item: Item) => string;
		getSearchText: (item: Item) => string;
		searchPlaceholder?: string;
		confirmLabel?: string;
		emptyTitle?: string;
		busy?: boolean;
		onConfirm: (id: string) => void;
		onClose: () => void;
		row: Snippet<[Item]>;
	} = $props();

	let query = $state('');
	let selectedId = $state<string | null>(null);

	$effect(() => {
		if (isOpen) return;
		query = '';
		selectedId = null;
	});

	const filtered = $derived(
		query.trim() ? items.filter((item) => getSearchText(item).toLowerCase().includes(query.trim().toLowerCase())) : items
	);

	function handleConfirm() {
		if (!selectedId) return;
		onConfirm(selectedId);
	}
</script>

<BaseModal {isOpen} {title} subtitle={description ?? ''} size="md" on:close={onClose}>
	<div class="p-4 space-y-3">
		{#if items.length > 0}
			<div class="relative">
				<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
				<Input bind:value={query} type="search" class="pl-9" placeholder={searchPlaceholder} aria-label={searchPlaceholder} />
			</div>
		{/if}

		<div class="max-h-80 overflow-y-auto rounded-lg border border-line" role="listbox" aria-label={title}>
			{#if items.length === 0}
				<EmptyState icon="search" title={emptyTitle} compact />
			{:else if filtered.length === 0}
				<EmptyState icon="search" title="No matches" description="Try a different search term." compact />
			{:else}
				{#each filtered as item (getId(item))}
					{@const id = getId(item)}
					<button
						type="button"
						role="option"
						aria-selected={selectedId === id}
						class="flex w-full items-center gap-2.5 border-b border-line px-3.5 py-2.5 text-left last:border-b-0 {selectedId ===
						id
							? 'bg-signal/10'
							: 'hover:bg-surface-2'}"
						onclick={() => (selectedId = id)}
					>
						<span class="min-w-0 flex-1">{@render row(item)}</span>
						{#if selectedId === id}
							<Icon name="check" className="h-3.5 w-3.5 flex-shrink-0 text-signal" />
						{/if}
					</button>
				{/each}
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<div class="flex justify-end gap-3 px-6 py-4">
			<Button variant="secondary" onclick={onClose}>Cancel</Button>
			<Button variant="primary" loading={busy} disabled={!selectedId || busy} onclick={handleConfirm}>{confirmLabel}</Button>
		</div>
	</svelte:fragment>
</BaseModal>
