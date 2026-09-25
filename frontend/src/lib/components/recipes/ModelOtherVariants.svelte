<script lang="ts">
	import { onDestroy } from 'svelte';
	import { Badge } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import type { RecipeSlotVariants } from '$lib/services/api/recipes';
	import SlotVariantRow from './SlotVariantRow.svelte';
	import VariantAttributionNote from './VariantAttributionNote.svelte';
	import { collectAttributions } from './variantAttribution';
	import { SlotVariantDownloads } from './slotVariantDownloads.svelte';

	let { modelId }: { modelId: string } = $props();

	let slot = $state<RecipeSlotVariants | null>(null);
	let reloadKey = $state(0);
	const downloads = new SlotVariantDownloads(() => (reloadKey += 1));

	$effect(() => {
		const id = modelId;
		void reloadKey;
		let cancelled = false;
		api
			.getModelSlotVariants(id)
			.then((result) => {
				if (!cancelled) slot = result.slot ?? null;
			})
			.catch((error) => {
				if (cancelled) return;
				slot = null;
				logger.error('[ModelOtherVariants] Failed to load variants:', error);
			});
		return () => {
			cancelled = true;
		};
	});

	onDestroy(() => downloads.dispose());

	const siblings = $derived(slot ? slot.variants.filter((v) => v.id !== slot!.current_variant_id) : []);
	const attributions = $derived(collectAttributions(siblings));
</script>

{#if slot && siblings.length > 0}
	{@const current = slot}
	<DetailSection label="Other variants">
		{#snippet headerExtra()}
			<Badge size="sm" class="font-mono tabular-nums">{siblings.length}</Badge>
		{/snippet}
		<div class="space-y-3" data-model-other-variants>
			<p class="text-sm text-fg-muted">
				This file is one download option for {current.label} in the {current.recipe_name} recipe.
			</p>
			<div class="divide-y divide-line rounded-lg border border-line bg-surface-1">
				{#each siblings as variant (variant.id)}
					<SlotVariantRow
						{variant}
						suggested={variant.id === current.suggested_variant_id}
						reason={current.suggested_reason}
						showUploader
						download={downloads.stateFor(variant.id)}
						onDownload={() => downloads.start(current, variant.id)}
					/>
				{/each}
			</div>
			{#each attributions as attribution (attribution.uploader + '|' + (attribution.source_url ?? ''))}
				<VariantAttributionNote {attribution} />
			{/each}
		</div>
	</DetailSection>
{/if}
