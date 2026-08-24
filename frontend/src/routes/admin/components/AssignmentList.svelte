<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Input, Spinner, EmptyState } from '$lib/components/ui';

	let {
		items = [],
		getId,
		getSearchText,
		isAssigned,
		isToggling = () => false,
		onToggle,
		loading = false,
		searchPlaceholder = 'Search…',
		ariaLabel = 'Assignable list',
		emptyIcon = 'search',
		emptyTitle = 'Nothing here yet',
		emptyDescription = '',
		row
	}: {
		items?: any[];
		getId: (item: any) => string;
		getSearchText: (item: any) => string;
		isAssigned: (item: any) => boolean;
		isToggling?: (item: any) => boolean;
		onToggle: (item: any) => void;
		loading?: boolean;
		searchPlaceholder?: string;
		ariaLabel?: string;
		emptyIcon?: string;
		emptyTitle?: string;
		emptyDescription?: string;
		row: Snippet<[item: any]>;
	} = $props();

	let searchQuery = $state('');
	let filteredItems = $derived(
		searchQuery.trim()
			? items.filter((item) => getSearchText(item).toLowerCase().includes(searchQuery.trim().toLowerCase()))
			: items
	);
</script>

<div class="rounded-lg border border-line bg-surface-1 overflow-hidden">
	{#if items.length > 0}
		<div class="px-4 py-3 border-b border-line">
			<div class="relative">
				<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
				<Input bind:value={searchQuery} type="search" class="pl-9" placeholder={searchPlaceholder} aria-label={searchPlaceholder} />
			</div>
		</div>
	{/if}

	{#if loading}
		<div class="py-10 flex items-center justify-center"><Spinner size="md" /></div>
	{:else if items.length === 0}
		<EmptyState icon={emptyIcon} title={emptyTitle} description={emptyDescription} compact />
	{:else if filteredItems.length === 0}
		<div class="p-4">
			<EmptyState icon="search" title="No matches" description="Try a different search term." compact />
		</div>
	{:else}
		<div class="divide-y divide-line" aria-label={ariaLabel}>
			{#each filteredItems as item (getId(item))}
				{@const assigned = isAssigned(item)}
				{@const toggling = isToggling(item)}
				<div
					class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-surface-2 transition-colors {toggling ? 'opacity-60 pointer-events-none' : ''}"
					role="button"
					tabindex="0"
					onclick={() => onToggle(item)}
					onkeydown={(e) => { if (e.key === 'Enter') onToggle(item); }}
				>
					<span
						class="flex-shrink-0 flex items-center justify-center w-5 h-5 rounded-full {assigned ? 'bg-signal text-white' : 'border border-line-strong text-fg-subtle'}"
						title={assigned ? 'Added' : 'Not added'}
					>
						{#if toggling}
							<Spinner size="sm" />
						{:else}
							<Icon name={assigned ? 'check' : 'plus'} className="w-3 h-3" strokeWidth={3} />
						{/if}
					</span>
					<div class="min-w-0 flex-1">
						{@render row(item)}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
