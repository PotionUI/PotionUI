<script lang="ts">
	import { Badge, Card } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { arxivUrl, statusBadgeVariant, statusLabel } from '$lib/utils/docsMeta';
	import type { DocRefs, TechniqueMeta } from '$lib/types/api';

	let {
		meta,
		refs,
		onNavigate
	}: {
		meta: TechniqueMeta;
		refs?: DocRefs | null;
		onNavigate?: (docId: string) => void;
	} = $props();

	let knobsExpanded = $state(false);
	const KNOB_COLLAPSE_THRESHOLD = 4;
	let knobs = $derived(meta.knobs ?? []);
	let visibleKnobs = $derived(
		knobsExpanded || knobs.length <= KNOB_COLLAPSE_THRESHOLD ? knobs : knobs.slice(0, KNOB_COLLAPSE_THRESHOLD)
	);

	// families[] is a list of family keys; resolve each to its refs.models
	// entry so the chip can link straight to that model's doc. A family with
	// no resolved ref (model doc not yet typed, or the reverse index hasn't
	// caught up) still renders as a plain, unlinked chip.
	let familyChips = $derived(
		meta.families.map((key) => ({
			key,
			ref: (refs?.models ?? []).find((m) => m.family_key === key) ?? null
		}))
	);

	function surfaceLabel(surface: string): string {
		return surface.charAt(0).toUpperCase() + surface.slice(1);
	}
</script>

<Card class="mb-6">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div class="min-w-0">
			<h1 class="text-lg font-semibold text-fg truncate">{meta.title}</h1>
			{#if meta.authors.length}
				<p class="mt-1 text-xs font-mono uppercase tracking-wide text-fg-subtle">
					{meta.authors.join(', ')}
				</p>
			{/if}
		</div>
		<div class="flex flex-shrink-0 items-center gap-2">
			<Badge variant={statusBadgeVariant(meta.status)} dot>{statusLabel(meta.status)}</Badge>
			<Badge variant="neutral">{meta.category_group}</Badge>
		</div>
	</div>

	{#if meta.paper || meta.reference_impl}
		<div class="mt-4 flex flex-wrap gap-x-6 gap-y-2 border-t border-line pt-4 text-sm">
			{#if meta.paper}
				<a
					href={meta.paper.arxiv ? arxivUrl(meta.paper.arxiv) : (meta.paper.url ?? undefined)}
					target="_blank"
					rel="noreferrer"
					class="inline-flex items-center gap-1.5 text-signal hover:underline"
				>
					<Icon name="book-open" className="w-4 h-4" />
					<span>{meta.paper.title ?? 'Paper'}</span>
					<Icon name="external-link" className="w-3 h-3 text-fg-subtle" />
				</a>
			{/if}
			{#if meta.reference_impl}
				<a
					href={meta.reference_impl.url ?? undefined}
					target="_blank"
					rel="noreferrer"
					class="inline-flex items-center gap-1.5 text-fg-muted hover:text-fg"
				>
					<Icon name="code" className="w-4 h-4" />
					<span>{meta.reference_impl.name ?? 'Reference implementation'}</span>
					{#if meta.reference_impl.license}
						<span class="font-mono text-xs uppercase tracking-wide text-fg-subtle"
							>· {meta.reference_impl.license}</span
						>
					{/if}
					<Icon name="external-link" className="w-3 h-3 text-fg-subtle" />
				</a>
			{/if}
		</div>
	{/if}

	{#if familyChips.length}
		<div class="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line pt-4">
			<span class="text-xs font-mono uppercase tracking-wide text-fg-subtle mr-1">Applies to</span>
			{#each familyChips as chip (chip.key)}
				{#if chip.ref}
					<button
						type="button"
						class="rounded px-2 py-0.5 text-xs font-medium bg-surface-2 text-fg-muted border border-line-strong hover:text-signal hover:border-signal/40 transition-colors"
						onclick={() => onNavigate?.(chip.ref!.doc_id)}
					>
						{chip.ref.title}
					</button>
				{:else}
					<span class="rounded px-2 py-0.5 text-xs font-medium bg-surface-2 text-fg-muted border border-line-strong">
						{chip.key}
					</span>
				{/if}
			{/each}
		</div>
	{/if}

	{#if knobs.length}
		<div class="mt-4 border-t border-line pt-4">
			<div class="overflow-x-auto">
				<table class="w-full text-left text-sm">
					<thead>
						<tr class="text-xs font-mono uppercase tracking-wide text-fg-subtle">
							<th class="pb-2 pr-4 font-medium">Knob</th>
							<th class="pb-2 pr-4 font-medium">Surface</th>
							<th class="pb-2 pr-4 font-medium">Default</th>
							<th class="pb-2 font-medium">Effect</th>
						</tr>
					</thead>
					<tbody>
						{#each visibleKnobs as knob (knob.key)}
							<tr class="border-t border-line">
								<td class="py-1.5 pr-4 font-mono text-fg whitespace-nowrap">{knob.key}</td>
								<td class="py-1.5 pr-4">
									<Badge variant="neutral" size="sm">{surfaceLabel(knob.surface)}</Badge>
								</td>
								<td class="py-1.5 pr-4 font-mono tabular-nums text-fg-muted whitespace-nowrap"
									>{knob.default}</td
								>
								<td class="py-1.5 text-fg-muted">{knob.effect}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if knobs.length > KNOB_COLLAPSE_THRESHOLD}
				<button
					type="button"
					class="mt-2 text-xs font-medium text-signal hover:underline"
					onclick={() => (knobsExpanded = !knobsExpanded)}
				>
					{knobsExpanded ? 'Show fewer' : `Show all ${knobs.length} knobs`}
				</button>
			{/if}
		</div>
	{/if}
</Card>
