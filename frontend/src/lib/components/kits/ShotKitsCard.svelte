<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import MediaThumb from '$lib/components/media/MediaThumb.svelte';
	import { Badge, Button } from '$lib/components/ui';
	import StageCard from '$lib/components/video-director/stage-rail/StageCard.svelte';
	import { KIT_KIND_ICONS, KIT_KIND_LABELS } from '$lib/kits/kitCounts';
	import { kitFieldsOf, loadedKits, type PromptSpecLike } from '$lib/kits/promptKits';
	import { shotKitUsage, type ShotKitEntry } from '$lib/kits/shotKits';
	import { ensureKits, kitDirectory } from '$lib/stores/kits';
	import type { DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
	import { shotPromptSegments, withMarkerAppendedToShot } from '$lib/utils/shotReferences';

	let {
		doc,
		caps,
		shotId,
		formData,
		specs,
		onDoc
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		shotId: string;
		formData: Record<string, unknown> | null | undefined;
		specs: readonly PromptSpecLike[];
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	const SHOWN = 5;
	let expanded = $state(false);

	let fields = $derived(kitFieldsOf(specs, formData).filter((entry) => caps.referenceFields.includes(entry.field)));
	let loaded = $derived(loadedKits(fields, $kitDirectory));
	let usage = $derived(shotKitUsage(shotPromptSegments(doc, caps, shotId).map((segment) => segment.content), loaded));
	let total = $derived(usage.used.length + usage.unused.length);
	let visibleUnused = $derived(expanded ? usage.unused : usage.unused.slice(0, SHOWN));

	$effect(() => {
		void ensureKits(fields.flatMap((entry) => entry.rows.map((row) => row.kit)));
	});

	function insert(entry: ShotKitEntry) {
		onDoc(withMarkerAppendedToShot(doc, caps, shotId, entry.marker));
	}

	function thumb(entry: ShotKitEntry): string | null {
		return entry.item.thumbnail_url ?? (entry.item.kind === 'image' ? entry.item.url : null);
	}
</script>

{#if loaded.length > 0}
	<StageCard>
		{#snippet title()}
			<span class="text-sm font-semibold text-fg">Kits · used in this shot</span>
			<span class="font-mono text-sm tabular-nums text-fg-subtle">{usage.used.length} of {total}</span>
		{/snippet}
		{#if usage.used.length === 0}
			<p class="text-sm leading-5 text-fg-muted">This shot cites no kit item. Type <span class="font-mono">@kits.</span> in the prompt or insert one below.</p>
		{:else}
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-3" data-shot-kits-used>
				{#each usage.used as entry (entry.item.id)}
					<div class="flex min-w-0 flex-col gap-1.5" data-shot-kit-used={entry.path}>
						<div class="aspect-square overflow-hidden rounded bg-surface-2">
							{#if entry.item.kind === 'text' || entry.item.kind === 'audio'}
								<span class="flex h-full items-center justify-center text-fg-muted"><Icon name={KIT_KIND_ICONS[entry.item.kind]} className="h-6 w-6" /></span>
							{:else}
								<MediaThumb url={thumb(entry)} kind={entry.item.kind} name={entry.item.name} className="w-full h-full" rounded={false} iconClassName="icon" />
							{/if}
						</div>
						<div class="flex items-center justify-between gap-2">
							<Badge variant="signal">{KIT_KIND_LABELS[entry.item.kind]}</Badge>
							<span class="font-mono text-sm tabular-nums text-fg-muted">×{entry.count}</span>
						</div>
						<span class="truncate font-mono text-sm text-fg-muted">{entry.path}</span>
					</div>
				{/each}
			</div>
		{/if}
	</StageCard>
	{#if usage.unused.length > 0}
		<StageCard>
			{#snippet title()}
				<span class="text-sm font-semibold text-fg">Kits · not used</span>
				<span class="font-mono text-sm tabular-nums text-fg-subtle">{usage.unused.length}</span>
			{/snippet}
			<ul class="flex flex-col divide-y divide-line" data-shot-kits-unused>
				{#each visibleUnused as entry (entry.item.id)}
					<li class="flex items-center gap-3 py-2" data-shot-kit-unused={entry.path}>
						<span class="h-11 w-11 flex-shrink-0 overflow-hidden rounded bg-surface-2">
							{#if entry.item.kind === 'text' || entry.item.kind === 'audio'}
								<span class="flex h-full items-center justify-center text-fg-muted"><Icon name={KIT_KIND_ICONS[entry.item.kind]} className="h-4 w-4" /></span>
							{:else}
								<MediaThumb url={thumb(entry)} kind={entry.item.kind} name={entry.item.name} className="w-full h-full" rounded={false} iconClassName="icon" />
							{/if}
						</span>
						<span class="min-w-0 flex-1 truncate font-mono text-sm text-fg-subtle">{entry.path}</span>
						<Badge>{KIT_KIND_LABELS[entry.item.kind]}</Badge>
						<Button variant="ghost" size="xs" icon="plus" onclick={() => insert(entry)}>Insert</Button>
					</li>
				{/each}
			</ul>
			{#if usage.unused.length > SHOWN}
				<button type="button" class="mt-2 text-sm text-fg-muted hover:text-fg" onclick={() => (expanded = !expanded)}>
					{expanded ? 'Show fewer' : `Show ${usage.unused.length - SHOWN} more`}
				</button>
			{/if}
		</StageCard>
	{/if}
{/if}
