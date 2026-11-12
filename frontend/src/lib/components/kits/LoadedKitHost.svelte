<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import type { AcceptedKinds } from '$lib/kits/kitCounts';
	import type { KitDetail, KitRow } from '$lib/kits/types';
	import { kitDirectory } from '$lib/stores/kits';
	import LoadedKitRow from './LoadedKitRow.svelte';

	let {
		row,
		accepted = null,
		compact = false,
		onRemove,
		onChoose
	}: {
		row: KitRow;
		accepted?: AcceptedKinds;
		compact?: boolean;
		onRemove: (row: KitRow) => void;
		onChoose?: () => void;
	} = $props();

	let known = $derived($kitDirectory.has(row.kit));
	let kit = $derived<KitDetail | null>($kitDirectory.get(row.kit) ?? null);
	let name = $derived(row.snapshot?.name ?? 'This kit');
</script>

{#if kit}
	<LoadedKitRow {kit} {row} {accepted} {compact} onRemove={() => onRemove(row)} />
{:else if known}
	<div class="flex items-start gap-3 rounded-lg border border-dashed border-danger/50 bg-danger/5 p-3" data-kit-deleted={row.kit}>
		<span class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded bg-danger/10 text-danger">
			<Icon name="warning" className="h-4 w-4" />
		</span>
		<div class="min-w-0 flex-1">
			<p class="text-base font-semibold text-fg">{name} was deleted</p>
			<p class="mt-0.5 text-sm text-fg-muted">Chips in the prompt that point to it are left out until you pick another kit.</p>
			<div class="mt-2 flex flex-wrap items-center gap-2">
				{#if onChoose}<Button variant="secondary" size="xs" onclick={onChoose}>Choose another kit</Button>{/if}
				<Button variant="ghost" size="xs" onclick={() => onRemove(row)}>Remove from this session</Button>
			</div>
		</div>
	</div>
{:else}
	<div class="h-[68px] animate-pulse rounded-lg border border-line bg-surface-1" aria-hidden="true"></div>
{/if}
