<script lang="ts">
	import { Button } from '$lib/components/ui';
	import { removeKitRow, splitKitRows, withKitRows } from '$lib/kits/kitMarker';
	import { defaultKitField, kitFieldsOf, type PromptSpecLike } from '$lib/kits/promptKits';
	import { requestKitLoad } from '$lib/stores/kitLoadRequests';
	import { ensureKits } from '$lib/stores/kits';
	import type { KitRow } from '$lib/kits/types';
	import LoadedKitHost from './LoadedKitHost.svelte';

	let {
		formData,
		specs,
		fields: allowed = null,
		onChange
	}: {
		formData: Record<string, unknown> | null | undefined;
		specs: readonly PromptSpecLike[];
		fields?: readonly string[] | null;
		onChange: (field: string, value: unknown[]) => void;
	} = $props();

	let fields = $derived(kitFieldsOf(specs, formData).filter((entry) => !allowed || allowed.includes(entry.field)));
	let loaded = $derived(fields.flatMap((entry) => entry.rows.map((row) => ({ field: entry.field, row, accepted: entry.accepted }))));
	let target = $derived(defaultKitField(fields));

	$effect(() => {
		void ensureKits(loaded.map((entry) => entry.row.kit));
	});

	function remove(field: string, row: KitRow) {
		const entry = fields.find((candidate) => candidate.field === field);
		if (!entry) return;
		const value = formData?.[field];
		const media = splitKitRows(Array.isArray(value) ? (value as unknown[]) : []).media;
		onChange(field, withKitRows(media, removeKitRow(entry.rows, row.kit)));
	}
</script>

{#if fields.length > 0}
	<section class="mb-5" aria-label="Kits" data-director-kits>
		<div class="mb-1 flex items-baseline justify-between gap-3">
			<h3 class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">Kits</h3>
			<span class="font-mono text-sm tabular-nums text-fg-subtle">{loaded.length} loaded</span>
		</div>
		<p class="mb-2 text-sm text-fg-muted">The Director picks from these in every shot.</p>
		<div class="space-y-2">
			{#each loaded as entry (entry.row.kit)}
				<LoadedKitHost
					row={entry.row}
					accepted={entry.accepted}
					onRemove={(row) => remove(entry.field, row)}
					onChoose={() => requestKitLoad(entry.field)}
				/>
			{/each}
		</div>
		<div class="mt-2 flex items-center gap-2 rounded-lg border border-dashed border-line-strong bg-surface-1 p-1.5">
			<Button variant="secondary" size="xs" icon="layers" onclick={() => requestKitLoad(target)}>Load a kit</Button>
			<span class="text-sm text-fg-subtle">Cite one in a shot with <span class="font-mono text-fg-muted">@kits.</span></span>
		</div>
	</section>
{/if}
