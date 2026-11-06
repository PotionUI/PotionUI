<script lang="ts">
	import { Badge } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { PaneRow } from '$lib/components/pane';
	import { indexingStatusStore } from '$lib/models-location/indexingStatus.svelte';
	import { digestPercent, digestPhase } from '$lib/models-location/indexingDisplay';
	import type { ModelRoot } from '$lib/services/api/models';
	import PathText from './PathText.svelte';
	import type { FoldersController } from './controller.svelte';
	import { STATE_COLOR, formatCount, rootFiles, rootMissing } from './derive';

	let { ctrl }: { ctrl: FoldersController } = $props();

	const ov = $derived(ctrl.overview);
	const roots = $derived(ctrl.sortedRoots);
	const effective = $derived(ctrl.effective);
	const digests = $derived(indexingStatusStore.status?.digests ?? null);
	const phase = $derived(digestPhase(indexingStatusStore.status));
	const pct = $derived(digests ? digestPercent(digests) : 0);

	const issues = (r: ModelRoot) => (r.state !== 'online' ? 1 : 0) + rootMissing(r).length;
</script>

<div class="border-t border-line" data-folders-tree>
	<div>
		<div class="px-3 pb-1 pt-2 font-mono text-[12px] uppercase tracking-[0.07em] text-fg-subtle">Across folders</div>
		<div class="space-y-0.5 p-2 pt-0" role="listbox" aria-label="Across folders">
			<PaneRow icon="download" title="Downloads & priority" selected={effective === 'routing'} onclick={() => (ctrl.selected = 'routing')} />
			{#if ov && ov.unplaced.length}
				<PaneRow icon="inbox" title="Found outside folders" selected={effective === 'suggested'} onclick={() => (ctrl.selected = 'suggested')}>
					{#snippet trailing()}<Badge variant="neutral" size="sm" class="ml-2 font-mono tabular-nums">{ov.unplaced.length}</Badge>{/snippet}
				</PaneRow>
			{/if}
			<PaneRow icon="database" title="Indexing" selected={effective === 'indexing'} onclick={() => (ctrl.selected = 'indexing')}>
				{#snippet trailing()}
					{#if phase === 'running' || phase === 'paused' || phase === 'waiting'}
						<span class="ml-2 font-mono text-[12px] tabular-nums text-signal">{pct}%</span>
					{:else if phase === 'not_started' || phase === 'choice'}
						<Badge variant="warning" size="sm" class="ml-2 font-mono tabular-nums">{digests?.pending_total}</Badge>
					{/if}
				{/snippet}
			</PaneRow>
		</div>
	</div>
	<div class="border-t border-line">
		<div class="flex items-center justify-between px-3 pb-1 pt-2">
			<span class="font-mono text-[12px] uppercase tracking-[0.07em] text-fg-subtle">Folders</span>
			<span class="font-mono text-[12px] tabular-nums text-fg-subtle">{roots.length}</span>
		</div>
		<div class="space-y-0.5 p-2 pt-0" role="listbox" aria-label="Folders">
			{#each roots as r (r.id)}
				<PaneRow dot={STATE_COLOR[r.state]} selected={effective === r.id} onclick={() => (ctrl.selected = r.id)}>
					{#snippet children()}
						<div class="flex min-w-0 items-center gap-1.5">
							<span class="truncate text-sm font-medium text-fg">{r.label}</span>
						</div>
						<PathText text={r.path} />
					{/snippet}
					{#snippet trailing()}
						{#if issues(r) > 0}
							<Tooltip text={r.state !== 'online' ? `${r.label} is ${r.state}` : 'A folder is missing on disk'} position="right">
								<Badge variant={r.state === 'offline' ? 'danger' : 'warning'} size="sm" class="ml-2 font-mono tabular-nums">{issues(r)}</Badge>
							</Tooltip>
						{:else}
							<span class="ml-2 flex-shrink-0 font-mono text-[12px] tabular-nums text-fg-subtle">{formatCount(rootFiles(r))}</span>
						{/if}
					{/snippet}
				</PaneRow>
			{/each}
			<PaneRow icon="folder-plus" onclick={() => ctrl.openAdd()}>
				{#snippet children()}<span class="text-sm text-fg-muted">Add folder</span>{/snippet}
			</PaneRow>
		</div>
	</div>
</div>
