<script lang="ts">
	import { onDestroy } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import type { RecipeSlotVariants } from '$lib/services/api/recipes';
	import SlotVariantRow from './SlotVariantRow.svelte';
	import { collectAttributions } from './variantAttribution';
	import { SlotVariantDownloads } from './slotVariantDownloads.svelte';

	let {
		presetId,
		modelType,
		onInstalled = () => {},
		onSlotFilenames = () => {},
		onCount = () => {},
		onAttributions = () => {}
	}: {
		presetId: string;
		modelType: string;
		onInstalled?: () => void;
		onSlotFilenames?: (filenames: Set<string>) => void;
		onCount?: (count: number) => void;
		onAttributions?: (attributions: ReturnType<typeof collectAttributions>) => void;
	} = $props();

	let slots = $state<RecipeSlotVariants[]>([]);
	const downloads = new SlotVariantDownloads(() => onInstalled());

	$effect(() => {
		const pid = presetId;
		const type = modelType;
		let cancelled = false;
		api
			.getPresetSlotVariants(pid, type)
			.then((result) => {
				if (cancelled) return;
				const loaded = result.slots ?? [];
				slots = loaded;
				onSlotFilenames(new Set(loaded.flatMap((slot) => slot.variants.map((v) => v.filename))));
			})
			.catch((error) => {
				if (!cancelled) logger.error('[PickerVariantSuggestions] Failed to load variants:', error);
			});
		return () => {
			cancelled = true;
		};
	});

	onDestroy(() => downloads.dispose());

	const offers = $derived(
		slots
			.map((slot) => {
				const missing = slot.variants.filter((v) => !v.installed);
				const suggested = missing.find((v) => v.id === slot.suggested_variant_id) ?? null;
				const others = missing.filter((v) => v !== suggested);
				return { slot, suggested, rows: suggested ? [suggested, ...others] : others };
			})
			.filter((offer) => offer.rows.length > 0)
	);
	$effect(() => {
		onAttributions(collectAttributions(offers.flatMap((offer) => offer.rows)));
	});
	$effect(() => {
		onCount(offers.reduce((total, offer) => total + offer.rows.length, 0));
	});
</script>

{#each offers as offer (offer.slot.recipe_id + ':' + offer.slot.id)}
	<div class="border-b border-line bg-surface-1" data-picker-variant-suggestions={offer.slot.id}>
		{#if offers.length > 1}
			<div class="px-3 pt-2.5 pb-0.5">
				<span class="block truncate text-xs font-semibold font-mono uppercase tracking-wide text-fg-subtle">{offer.slot.label}</span>
			</div>
		{/if}
		{#each offer.rows as row (row.id)}
			<SlotVariantRow
				flat
				variant={row}
				suggested={row === offer.suggested}
				reason={offer.slot.suggested_reason}
				download={downloads.stateFor(row.id)}
				onDownload={() => downloads.start(offer.slot, row.id)}
			/>
		{/each}
	</div>
{/each}
