<script lang="ts">
	import { Badge, Card } from '$lib/components/ui';
	import { groupTechniqueRefs, statusBadgeVariant, statusLabel } from '$lib/utils/docsMeta';
	import type { DocRefs, DocStatus, DocTechniqueRef, ModelMeta } from '$lib/types/api';

	let {
		meta,
		refs,
		status,
		onNavigate
	}: {
		meta: ModelMeta;
		refs?: DocRefs | null;
		status?: DocStatus | null;
		onNavigate?: (docId: string) => void;
	} = $props();

	let specRows = $derived(
		(
			[
				['Architecture', meta.spec.arch],
				['Parameters', meta.spec.params],
				['Latent format', meta.spec.latent],
				['VAE', meta.spec.vae],
				['Text encoder', meta.spec.te],
				['Guidance', meta.spec.guidance],
				['Shift', meta.spec.shift]
			] as [string, unknown][]
		)
			.filter(([, value]) => value !== null && value !== undefined && value !== '')
			.map(([label, value]) => [label, String(value)] as [string, string])
	);

	let grouped = $derived(groupTechniqueRefs(refs?.techniques));
</script>

{#snippet techniqueRow(t: DocTechniqueRef)}
	<button
		type="button"
		class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-fg-muted hover:bg-surface-2 hover:text-fg transition-colors"
		onclick={() => onNavigate?.(t.doc_id)}
	>
		<span class="truncate">{t.title}</span>
		<Badge variant={statusBadgeVariant(t.status)} size="sm">{statusLabel(t.status)}</Badge>
		<span class="ml-auto flex-shrink-0 text-xs font-mono uppercase tracking-wide text-fg-subtle">
			{t.category_group}
		</span>
	</button>
{/snippet}

<Card class="mb-6">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<h1 class="text-lg font-semibold text-fg truncate">{meta.title}</h1>
		<div class="flex flex-shrink-0 items-center gap-2">
			{#if status}
				<Badge variant={statusBadgeVariant(status)} dot>{statusLabel(status)}</Badge>
			{/if}
			<Badge variant="signal">{meta.spec.engine}</Badge>
		</div>
	</div>

	{#if meta.modes.length}
		<div class="mt-2 flex flex-wrap gap-1.5">
			{#each meta.modes as mode (mode)}
				<span class="rounded px-2 py-0.5 text-xs font-mono uppercase tracking-wide bg-surface-2 text-fg-muted border border-line-strong">
					{mode}
				</span>
			{/each}
		</div>
	{/if}

	{#if specRows.length}
		<dl class="mt-4 grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-line pt-4 sm:grid-cols-2">
			{#each specRows as [label, value] (label)}
				<div class="flex items-baseline justify-between gap-3 text-sm">
					<dt class="text-fg-subtle">{label}</dt>
					<dd class="font-mono tabular-nums text-fg truncate">{value}</dd>
				</div>
			{/each}
		</dl>
	{/if}
</Card>

{#if grouped.optimizations.length}
	<div class="mb-6">
		<h2 class="mb-2 text-xs font-mono uppercase tracking-wide text-fg-subtle">Optimizations</h2>
		<Card padding="sm">
			<div class="divide-y divide-line">
				{#each grouped.optimizations as t (t.slug)}
					{@render techniqueRow(t)}
				{/each}
			</div>
		</Card>
	</div>
{/if}

{#if grouped.quality.length}
	<div class="mb-6">
		<h2 class="mb-2 text-xs font-mono uppercase tracking-wide text-fg-subtle">Quality techniques</h2>
		<Card padding="sm">
			<div class="divide-y divide-line">
				{#each grouped.quality as t (t.slug)}
					{@render techniqueRow(t)}
				{/each}
			</div>
		</Card>
	</div>
{/if}
