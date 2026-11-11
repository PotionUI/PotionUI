<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Input } from '$lib/components/ui';
	import { compileTextMatcher } from '$lib/utils/textMatch';
	import type { McpTool, McpToolsListing } from '$lib/services/api/mcpTools';
	import McpToolGroup from './McpToolGroup.svelte';
	import {
		FILTERS,
		bulkAction,
		buildGroups,
		changeForPolicy,
		changeForReset,
		changeForTools,
		changeForWritesOff,
		countTools,
		firstGroup,
		isEmptyChange,
		isGroupOpen,
		policyOptions,
		summaryText,
		type ToolFilter,
		type ToolGroup,
		type ToolsChange
	} from './mcpToolsView';

	let {
		listing,
		onChange,
		onChangeDefault
	}: {
		listing: McpToolsListing;
		onChange: (change: ToolsChange) => void;
		onChangeDefault?: () => void;
	} = $props();

	let query = $state('');
	let filter = $state<ToolFilter>('all');
	let chosen = $state<Record<string, boolean>>({});
	let searchWrap = $state<HTMLElement>();

	const level = $derived(listing.level);
	const view = $derived({ query, filter });
	const counts = $derived(countTools(listing.tools));
	const groups = $derived(buildGroups(listing.tools, view));
	const matcher = $derived(compileTextMatcher(query).matcher);
	const bulk = $derived(bulkAction(level));
	const bulkChange = $derived(bulk.id === 'match' ? changeForReset() : changeForWritesOff(level, listing.tools));
	const defaultOpen = $derived(firstGroup(listing.tools));

	function groupOpen(name: string): boolean {
		return isGroupOpen(name, view, chosen, defaultOpen);
	}

	function toggleOpen(name: string) {
		chosen = { ...chosen, [name]: !groupOpen(name) };
	}

	function focusSearch() {
		searchWrap?.querySelector('input')?.focus();
	}

	function toggleTool(tool: McpTool, on: boolean) {
		onChange(changeForTools(level, [tool], on));
	}

	function toggleGroup(group: ToolGroup, on: boolean) {
		onChange(changeForTools(level, group.toggleable, on));
	}

	function clearView() {
		query = '';
		filter = 'all';
		focusSearch();
	}
</script>

<div class="space-y-4" data-root={level}>
	<div class="flex flex-wrap items-center justify-between gap-3">
		<p class="font-mono text-md tabular-nums text-fg">{summaryText(level, counts)}</p>
		<Button size="xs" disabled={isEmptyChange(bulkChange)} onclick={() => onChange(bulkChange)}>{bulk.label}</Button>
	</div>

	{#if level !== 'key'}
		<div class="flex flex-wrap items-center justify-between gap-3 border-y border-line py-3">
			<p class="text-sm font-medium text-fg">New tools</p>
			<div class="flex flex-shrink-0 flex-wrap rounded border border-line-strong bg-surface-2 p-0.5" role="radiogroup" aria-label="New tools">
				{#each policyOptions(level) as option (option.id)}
					<button
						type="button"
						role="radio"
						aria-checked={listing.policy === option.id}
						class="whitespace-nowrap rounded-sm px-3 py-1 text-sm font-medium transition-colors duration-100 {listing.policy === option.id
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:text-fg'}"
						onclick={() => listing.policy !== option.id && onChange(changeForPolicy(option.id))}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>
	{/if}

	<div class="flex flex-wrap items-center gap-2">
		<div class="relative min-w-[10rem] flex-1" bind:this={searchWrap}>
			<span class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-fg-subtle">
				<Icon name="search" className="h-4 w-4" />
			</span>
			<Input
				type="search"
				bind:value={query}
				class="pl-9 pr-9 [&::-webkit-search-cancel-button]:appearance-none"
				placeholder="Search tools"
				aria-label="Search MCP tools"
				autocomplete="off"
			/>
			{#if query}
				<button
					type="button"
					class="absolute inset-y-0 right-1 my-auto flex h-7 w-7 items-center justify-center rounded text-fg-subtle transition-colors hover:text-fg"
					aria-label="Clear search"
					onclick={() => {
						query = '';
						focusSearch();
					}}
				>
					<Icon name="close" className="h-4 w-4" />
				</button>
			{/if}
		</div>
		<div class="flex w-full items-center gap-0.5 rounded border border-line bg-surface-2 p-0.5 sm:w-auto" role="group" aria-label="Filter tools">
			{#each FILTERS as item (item.id)}
				<button
					type="button"
					aria-pressed={filter === item.id}
					class="min-w-0 flex-1 truncate rounded px-2.5 py-1 text-sm font-medium transition-colors sm:flex-none {filter === item.id
						? 'bg-surface-3 text-fg'
						: 'text-fg-muted hover:text-fg'}"
					onclick={() => (filter = item.id)}
				>
					{item.label}
				</button>
			{/each}
		</div>
	</div>

	<div aria-live="polite">
		{#if groups.length}
			<div class="divide-y divide-line overflow-hidden rounded border border-line" data-list>
				{#each groups as group (group.name)}
					<McpToolGroup
						{group}
						{level}
						open={groupOpen(group.name)}
						{matcher}
						onToggleOpen={toggleOpen}
						onToggleTool={toggleTool}
						onToggleGroup={toggleGroup}
						{onChangeDefault}
					/>
				{/each}
			</div>
		{:else}
			<div class="rounded border border-line px-4 py-8 text-center">
				<p class="text-sm font-medium text-fg">{query.trim() ? `No tools match “${query.trim()}”` : 'No tools in this view'}</p>
				<p class="mt-1 text-sm text-fg-subtle">
					{!query.trim() && filter !== 'all' ? 'Switch the filter to see the rest.' : 'Try a tool name, a group, or a word from its description.'}
				</p>
				<Button size="xs" class="mt-3" onclick={clearView}>{query.trim() ? 'Clear search' : 'Show all tools'}</Button>
			</div>
		{/if}
	</div>
</div>
