<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Kbd, Menu } from '$lib/components/ui';
	import { sourceEntries, sourceNoun, type MediaSource, type SourceIntent } from './mediaFieldSources';
	import type { MediaKind } from './mediaLoaderConfig';

	let {
		kind,
		offersDraw = false,
		offersKits = false,
		intent = 'add',
		current = null,
		align = 'left',
		onPick,
		trigger
	}: {
		kind: MediaKind | null;
		offersDraw?: boolean;
		offersKits?: boolean;
		intent?: SourceIntent;
		current?: MediaSource | null;
		align?: 'left' | 'right';
		onPick: (source: MediaSource) => void;
		trigger: Snippet<[{ toggle: () => void; open: boolean }]>;
	} = $props();

	let entries = $derived(sourceEntries({ kind, offersDraw, offersKits, intent }));
	let heading = $derived(intent === 'change' ? `Replace ${sourceNoun(kind)}` : 'Add media');
</script>

<Menu label={heading} width={240} {align} {trigger} menuAttrs={{ 'data-source-menu': intent }}>
	{#snippet children({ close })}
		{#if intent === 'change'}
			<p class="px-2.5 pb-1 pt-1.5 text-sm text-fg-subtle" data-source-heading>{heading}</p>
		{/if}
		{#each entries as entry (entry.source)}
			{@const active = current === entry.source}
			{#if entry.dividerBefore}
				<div class="my-1 h-px bg-line" role="separator"></div>
			{/if}
			<button
				type="button"
				role="menuitem"
				class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm transition-colors hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none {active
					? 'text-signal'
					: 'text-fg'}"
				data-source={entry.source}
				aria-current={active ? 'true' : undefined}
				onclick={() => {
					close();
					onPick(entry.source);
				}}
			>
				<Icon name={entry.icon} className="h-4 w-4 flex-shrink-0" />
				<span class="min-w-0 flex-1 truncate">{entry.label}</span>
				{#if active}
					<Icon name="check" className="h-4 w-4 flex-shrink-0" />
				{:else if entry.shortcut}
					<Kbd keys={entry.shortcut.split(' ')} />
				{:else if entry.hint}
					<span class="text-fg-subtle">{entry.hint}</span>
				{/if}
			</button>
		{/each}
	{/snippet}
</Menu>
