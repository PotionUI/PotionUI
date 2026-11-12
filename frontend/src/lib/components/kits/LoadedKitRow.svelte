<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, IconButton } from '$lib/components/ui';
	import { usageNote, usageOf, type AcceptedKinds } from '$lib/kits/kitCounts';
	import { hrefWithKitsView } from '$lib/kits/libraryView';
	import type { KitDetail, KitRow } from '$lib/kits/types';
	import KitItemChip from './KitItemChip.svelte';
	import KitKindCounts from './KitKindCounts.svelte';
	import KitMosaic from './KitMosaic.svelte';
	import KitPath from './KitPath.svelte';

	let {
		kit,
		row = null,
		accepted = null,
		onRemove,
		compact = false
	}: {
		kit: KitDetail;
		row?: KitRow | null;
		accepted?: AcceptedKinds;
		onRemove?: (kit: KitDetail) => void;
		compact?: boolean;
	} = $props();

	let usage = $derived(usageOf(kit.items, accepted));
	let note = $derived(usageNote(usage));
	let skippedIds = $derived(new Set(usage.skipped.map((item) => item.id)));
	let gone = $derived(
		row?.snapshot ? row.snapshot.items.filter((before) => !kit.items.some((item) => item.id === before.id)) : []
	);
	let removed = $derived(gone.length);
	let href = $derived(
		hrefWithKitsView(new URL('/library', 'http://local'), { active: true, kitId: kit.id })
	);
</script>

<article class="overflow-hidden rounded-lg border border-line bg-surface-1" data-loaded-kit={kit.id}>
	<div class="flex items-center gap-3 p-2.5">
		<div class="h-14 w-14 flex-shrink-0 overflow-hidden rounded">
			<KitMosaic items={kit.preview_items} class="h-14 w-14" />
		</div>
		<div class="min-w-0 flex-1">
			<div class="flex min-w-0 items-center gap-2">
				<span class="truncate text-base font-semibold text-fg">{kit.name}</span>
				{#if kit.label}<Badge size="sm">{kit.label}</Badge>{/if}
			</div>
			<KitPath handle={kit.handle} class="block truncate text-sm text-fg-muted" />
			<div class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-fg-muted">
				<KitKindCounts counts={kit.counts} />
				<span class="text-fg-subtle" aria-hidden="true">·</span>
				{#if removed > 0}
					<span class="inline-flex items-center gap-1 text-sm text-danger">
						<Icon name="warning" className="h-3.5 w-3.5" />{removed} removed
					</span>
				{:else}
					<span class="inline-flex items-center gap-1 text-sm {note.tone === 'success' ? 'text-success' : 'text-warning'}" data-kit-usage-note>
						{#if note.tone === 'warning'}<Icon name="warning" className="h-3.5 w-3.5" />{/if}{note.text}
					</span>
				{/if}
			</div>
		</div>
		<a
			{href}
			class="inline-flex min-h-8 min-w-8 items-center justify-center rounded text-fg-muted hover:bg-surface-2 hover:text-fg"
			aria-label="Open {kit.name} in the Library"
			target="_blank"
			rel="noopener"
		>
			<Icon name="external-link" className="h-4 w-4" />
		</a>
		{#if onRemove}
			<IconButton icon="close" label="Remove {kit.name}" size="sm" onclick={() => onRemove(kit)} />
		{/if}
	</div>
	{#if !compact && (kit.items.length > 0 || gone.length > 0)}
		<div class="flex flex-wrap gap-1.5 border-t border-line bg-surface-2/30 p-2" data-loaded-kit-items>
			{#each kit.items as item (item.id)}
				<KitItemChip {item} skipped={skippedIds.has(item.id)} />
			{/each}
			{#each gone as before (before.id)}
				<span
					class="inline-flex h-8 items-center gap-1.5 rounded border border-danger/40 bg-danger/10 px-2 font-mono text-sm text-danger"
					data-kit-item-gone={before.name}
				>
					<span class="line-through">{before.name}</span>
					<Icon name="warning" className="h-3 w-3" />
				</span>
			{/each}
		</div>
	{/if}
</article>
