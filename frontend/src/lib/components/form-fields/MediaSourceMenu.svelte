<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Kbd, Menu } from '$lib/components/ui';
	import { sourceEntries, type MediaSource } from './mediaFieldSources';
	import type { MediaKind } from './mediaLoaderConfig';

	let {
		kind,
		offersDraw = false,
		offersKits = false,
		align = 'left',
		onPick,
		trigger
	}: {
		kind: MediaKind | null;
		offersDraw?: boolean;
		offersKits?: boolean;
		align?: 'left' | 'right';
		onPick: (source: MediaSource) => void;
		trigger: Snippet<[{ toggle: () => void; open: boolean }]>;
	} = $props();

	let entries = $derived(sourceEntries({ kind, offersDraw, offersKits }));
</script>

<Menu label="Add media" width={224} {align} {trigger} menuAttrs={{ 'data-source-menu': '' }}>
	{#snippet children({ close })}
		{#each entries as entry (entry.source)}
			<button
				type="button"
				role="menuitem"
				class="w-full px-3 py-1.5 text-sm text-left rounded transition-colors flex items-center gap-2 text-fg-muted hover:text-fg hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:text-fg"
				data-source={entry.source}
				onclick={() => {
					close();
					onPick(entry.source);
				}}
			>
				<Icon name={entry.icon} className="w-4 h-4 shrink-0" />
				<span class="flex-1 min-w-0 truncate">{entry.label}</span>
				{#if entry.shortcut}
					<Kbd keys={entry.shortcut.split(' ')} />
				{/if}
			</button>
		{/each}
	{/snippet}
</Menu>
