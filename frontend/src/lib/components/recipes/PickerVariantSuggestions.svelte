<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import type { RecipeSlotVariants } from '$lib/services/api/recipes';
	import SlotVariantRow from './SlotVariantRow.svelte';
	import VariantAttributionNote from './VariantAttributionNote.svelte';
	import { collectAttributions } from './variantAttribution';
	import { SlotVariantDownloads } from './slotVariantDownloads.svelte';

	let {
		presetId,
		modelType,
		onInstalled = () => {}
	}: {
		presetId: string;
		modelType: string;
		onInstalled?: () => void;
	} = $props();

	let slots = $state<RecipeSlotVariants[]>([]);
	let expanded = $state<Record<string, boolean>>({});
	const downloads = new SlotVariantDownloads(() => onInstalled());

	$effect(() => {
		const pid = presetId;
		const type = modelType;
		let cancelled = false;
		api
			.getPresetSlotVariants(pid, type)
			.then((result) => {
				if (!cancelled) slots = result.slots ?? [];
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
				return { slot, suggested, others };
			})
			.filter((offer) => offer.suggested || offer.others.length > 0)
	);
</script>

{#each offers as offer (offer.slot.recipe_id + ':' + offer.slot.id)}
	{@const open = expanded[offer.slot.id] ?? !offer.suggested}
	<div class="border-b border-line bg-surface-1" data-picker-variant-suggestions={offer.slot.id}>
		<div class="flex items-center gap-2 px-3 pt-2.5 pb-1">
			<span class="text-xs font-semibold font-mono uppercase tracking-wide text-fg-subtle">Suggested</span>
			<span class="min-w-0 truncate text-xs text-fg-subtle">{offer.slot.label}</span>
		</div>
		{#if offer.suggested}
			<SlotVariantRow
				variant={offer.suggested}
				suggested
				reason={offer.slot.suggested_reason}
				download={downloads.stateFor(offer.suggested.id)}
				onDownload={() => downloads.start(offer.slot, offer.suggested!.id)}
			/>
		{/if}
		{#if offer.others.length > 0}
			<button
				type="button"
				class="flex w-full items-center gap-1.5 px-3 py-1.5 text-sm text-fg-muted hover:text-fg"
				aria-expanded={open}
				data-picker-other-variants-toggle
				onclick={(event) => {
					event.stopPropagation();
					expanded = { ...expanded, [offer.slot.id]: !open };
				}}
			>
				<Icon name={open ? 'chevron-down' : 'chevron-right'} className="w-3.5 h-3.5" />
				Other variants
				<span class="font-mono tabular-nums text-fg-subtle">{offer.others.length}</span>
			</button>
			{#if open}
				{#each offer.others as variant (variant.id)}
					<SlotVariantRow
						{variant}
						download={downloads.stateFor(variant.id)}
						onDownload={() => downloads.start(offer.slot, variant.id)}
					/>
				{/each}
			{/if}
		{/if}
		{#each collectAttributions([offer.suggested, ...offer.others].filter((v) => v !== null)) as attribution (attribution.uploader + '|' + (attribution.source_url ?? ''))}
			<div class="px-3 pb-2.5 pt-1">
				<VariantAttributionNote {attribution} />
			</div>
		{/each}
	</div>
{/each}
