<script lang="ts">
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import { collectFormMediaOptions, formMediaOptionKeys } from '$lib/utils/videoDirector';
	import { shotReferenceOverview, withMarkerAppendedToShot, type ShotReferenceEntry } from '$lib/utils/shotReferences';
	import Icon from '$lib/components/Icon.svelte';
	import MediaThumb from '$lib/components/media/MediaThumb.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, IconButton } from '$lib/components/ui';
	import StageCard from '../stage-rail/StageCard.svelte';

	let {
		doc,
		caps,
		formData,
		shotId,
		onDoc,
		promptResources = []
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData: Record<string, unknown> | null | undefined;
		shotId: string;
		onDoc: (next: VideoDirectorValue) => void;
		promptResources?: PromptResourceSpec[];
	} = $props();

	let referencePool = $derived(collectFormMediaOptions(formData).filter((o) => caps.referenceFields.includes(o.field)));
	let referencePoolKeys = $derived(formMediaOptionKeys(referencePool));
	let overview = $derived(shotReferenceOverview(doc, caps, shotId, formData, promptResources));
	let poolSize = $derived(overview.used.length + overview.unused.length);

	const WAVE_BARS = [30, 55, 80, 45, 95, 60, 35, 70, 90, 50, 25, 65, 85, 40, 60, 30];

	function insert(entry: ShotReferenceEntry) {
		onDoc(withMarkerAppendedToShot(doc, caps, shotId, entry.marker));
	}
</script>

{#snippet tile(entry: ShotReferenceEntry, size: 'lg' | 'sm')}
	<div class="thumb {size}" class:audio={entry.kind === 'audio'}>
		{#if entry.kind === 'audio'}
			<div class="wave" aria-hidden="true">
				{#each WAVE_BARS as height, i (i)}
					<span style="height: {height}%"></span>
				{/each}
			</div>
			<Icon name="audio" className="kind-icon" />
		{:else}
			<MediaThumb url={entry.url} kind={entry.kind} name={entry.name} className="w-full h-full" rounded={false} iconClassName="kind-icon" />
		{/if}
	</div>
{/snippet}

{#if caps.references === 'whole'}
	<div class="ref-tab">
		<div class="stage-cap">This shot inherits every reference in the pool</div>
		{#if referencePool.length === 0}
			<p class="empty">No items on the reference pool yet — add some on the form's References tab.</p>
		{:else}
			<div class="grid">
				{#each referencePool as opt, i (referencePoolKeys[i])}
					<div class="item readonly">
						<div class="pool-thumb">
							<MediaThumb
								url={opt.item.url}
								kind={opt.item.type}
								name={opt.item.label || opt.item.name || opt.fieldLabel}
								className="w-full h-full"
								rounded={false}
								iconClassName="icon"
							/>
						</div>
						<span class="label">{opt.item.label || opt.item.name || opt.fieldLabel}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{:else}
	<div class="flex flex-col gap-3" data-testid="shot-references">
		{#if poolSize === 0}
			<p class="text-xs text-fg-subtle">No items on the reference pool yet — add some on the form's References tab.</p>
		{:else}
			<StageCard>
				{#snippet title()}
					<span class="text-xs font-semibold text-fg">Used in this shot</span>
					<span class="font-mono text-xs tabular-nums text-fg-subtle">{overview.used.length} of {poolSize}</span>
				{/snippet}
				{#if overview.used.length === 0}
					<p class="text-xs leading-5 text-fg-muted" data-testid="shot-references-empty">
						This shot's prompt cites no reference, so it is generated without any. Type @ in the prompt or insert one below.
					</p>
				{:else}
					<div class="used-grid" data-testid="shot-references-used">
						{#each overview.used as entry (`${entry.field}:${entry.itemKey}`)}
							<div class="flex min-w-0 flex-col gap-1.5" data-testid="shot-reference-used" data-marker={entry.marker}>
								{@render tile(entry, 'lg')}
								<div class="flex items-center justify-between gap-2">
									{#if entry.handle}<Badge variant="signal">{entry.handle}</Badge>{/if}
									<span class="font-mono text-xs tabular-nums text-fg-muted" data-testid="shot-reference-count">×{entry.count}</span>
								</div>
								<Tooltip text={entry.name} position="bottom" wrapperClass="flex min-w-0">
									<span class="truncate text-xs text-fg">{entry.name}</span>
								</Tooltip>
							</div>
						{/each}
					</div>
				{/if}
			</StageCard>

			{#if overview.unused.length > 0}
				<StageCard>
					{#snippet title()}
						<span class="text-xs font-semibold text-fg-muted">Not used</span>
						<span class="font-mono text-xs tabular-nums text-fg-subtle">{overview.unused.length}</span>
					{/snippet}
					<div class="flex flex-col gap-1.5" data-testid="shot-references-unused">
						{#each overview.unused as entry (`${entry.field}:${entry.itemKey}`)}
							<div class="unused-row" data-testid="shot-reference-unused" data-marker={entry.marker}>
								{@render tile(entry, 'sm')}
								<Tooltip text={entry.name} position="bottom" wrapperClass="flex min-w-0 flex-1">
									<span class="truncate text-xs text-fg-muted">{entry.name}</span>
								</Tooltip>
								<Tooltip text="Insert reference into this shot's prompt" position="top">
									<IconButton icon="plus" label="Insert @{entry.name}" size="sm" onclick={() => insert(entry)} />
								</Tooltip>
							</div>
						{/each}
					</div>
				</StageCard>
			{/if}
		{/if}
	</div>
{/if}

<style>
	.ref-tab {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.stage-cap {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
	}
	.empty {
		font-size: 12px;
		color: rgb(var(--fg-subtle));
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
		gap: 8px;
	}
	.item {
		display: flex;
		align-items: center;
		gap: 8px;
		border-radius: 4px;
		padding: 6px 8px;
		box-shadow: inset 0 0 0 1px rgb(var(--line));
	}
	.pool-thumb {
		width: 32px;
		height: 32px;
		flex: none;
		border-radius: 4px;
		overflow: hidden;
		background: rgb(var(--surface-2));
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.pool-thumb :global(.icon) {
		width: 14px;
		height: 14px;
		color: rgb(var(--fg-subtle));
	}
	.label {
		font-size: 12px;
		color: rgb(var(--fg));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.used-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(9rem, 10rem));
		gap: 12px;
	}
	.unused-row {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
		opacity: 0.72;
		transition: opacity 120ms ease;
	}
	.unused-row:hover,
	.unused-row:focus-within {
		opacity: 1;
	}
	.thumb {
		position: relative;
		flex: none;
		border-radius: 4px;
		overflow: hidden;
		background: rgb(var(--surface-2));
		box-shadow: inset 0 0 0 1px rgb(var(--line));
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.thumb.lg {
		width: 100%;
		aspect-ratio: 1 / 1;
	}
	.thumb.sm {
		width: 3rem;
		height: 3rem;
	}
	.thumb :global(.kind-icon) {
		position: relative;
		width: 20px;
		height: 20px;
		color: rgb(var(--fg-muted));
	}
	.thumb.lg :global(.kind-icon) {
		width: 28px;
		height: 28px;
	}
	.wave {
		position: absolute;
		inset: 20% 10%;
		display: flex;
		align-items: center;
		gap: 3%;
	}
	.wave span {
		flex: 1;
		border-radius: 1px;
		background: rgb(var(--fg-subtle) / 0.35);
	}
	.thumb.audio :global(.kind-icon) {
		color: rgb(var(--fg));
	}
</style>
