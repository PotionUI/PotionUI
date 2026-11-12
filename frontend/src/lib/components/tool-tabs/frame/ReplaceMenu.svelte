<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton, Menu } from '$lib/components/ui';
	import type { SourceEntry, SourceEntryId } from '$lib/tool-tabs/sourceEntries';

	let {
		label,
		entries,
		disabled = false,
		phone = false,
		current = null,
		onselect,
		footer
	}: {
		label: string;
		entries: SourceEntry[];
		disabled?: boolean;
		phone?: boolean;
		current?: SourceEntryId | null;
		onselect: (id: SourceEntryId) => void;
		footer?: Snippet<[() => void]>;
	} = $props();
</script>

<Menu {label} width={220} align="left" preferred="down">
	{#snippet trigger({ open, toggle })}
		<Tooltip text={label} position="bottom">
			<IconButton icon="refresh" {label} size="sm" variant={phone ? 'overlay' : 'ghost'} {disabled} ariaExpanded={open} onclick={toggle} data-frame-replace />
		</Tooltip>
	{/snippet}
	{#snippet children({ close })}
		{#each entries as entry (entry.id)}
			<button
				type="button"
				role="menuitem"
				class="flex h-9 max-md:h-11 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none {current === entry.id
					? 'text-signal'
					: 'text-fg'}"
				data-replace-entry={entry.id}
				aria-current={current === entry.id ? 'true' : undefined}
				onclick={() => {
					close(true);
					onselect(entry.id);
				}}
			>
				<Icon name={entry.icon} className="h-4 w-4 flex-shrink-0" />
				<span class="min-w-0 flex-1 truncate">{entry.label}</span>
				{#if current === entry.id}<Icon name="check" className="h-4 w-4 flex-shrink-0" />{/if}
			</button>
		{/each}
		{#if footer}
			<div class="mt-1 border-t border-line px-2.5 pb-1 pt-2">{@render footer(() => close(true))}</div>
		{/if}
	{/snippet}
</Menu>
