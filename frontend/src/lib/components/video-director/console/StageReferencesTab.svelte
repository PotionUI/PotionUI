<script lang="ts">
	// References stage tab -- `references: 'per_shot'` OR `'whole'` shots (a
	// shot's tabs never include 'references' for a null capability). Per-shot
	// is the picker grid lifted from StageShot.svelte:336-369; 'whole' has no
	// per-shot selection at all, so it renders read-only -- the same
	// `.stage-cap` idiom the Selection variants use, captioned "This shot
	// inherits every reference in the pool", listing the pool with the same
	// thumbs and no checkboxes (replaces the deleted Rail "Refs" lane, which
	// used to be the only place a whole-film pool rendered at all).
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import { collectFormMediaOptions, formMediaOptionKeys, isSegmentFormMediaReference } from '$lib/utils/videoDirector';
	import { withShotReferences } from '../stage-rail/stageModel';
	import Icon from '$lib/components/Icon.svelte';

	let {
		doc,
		caps,
		formData,
		shotId,
		onDoc
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData: Record<string, unknown> | null | undefined;
		shotId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let referenceSegment = $derived(
		caps.segmentRouting ? doc.chain.segments.find((s) => s.id === shotId) : doc.timeline.shots.find((s) => s.id === shotId)?.segments[0]
	);
	let currentReferences = $derived(referenceSegment?.references ?? []);
	let referencePool = $derived(collectFormMediaOptions(formData).filter((o) => caps.referenceFields.includes(o.field)));
	let referencePoolKeys = $derived(formMediaOptionKeys(referencePool));

	function isReferenceSelected(field: string, path: string): boolean {
		return currentReferences.some((r) => isSegmentFormMediaReference(r) && r.form_media.field === field && r.form_media.path === path);
	}
	function toggleReference(field: string, path: string) {
		const next = isReferenceSelected(field, path)
			? currentReferences.filter((r) => !(isSegmentFormMediaReference(r) && r.form_media.field === field && r.form_media.path === path))
			: [...currentReferences, { form_media: { field, path } }];
		onDoc(withShotReferences(doc, caps, shotId, next));
	}
	function selectAllReferences() {
		onDoc(withShotReferences(doc, caps, shotId, []));
	}
</script>

{#if caps.references === 'whole'}
	<div class="ref-tab">
		<div class="stage-cap">This shot inherits every reference in the pool</div>
		{#if referencePool.length === 0}
			<p class="empty">No items on the reference pool yet — add some on the form's References tab.</p>
		{:else}
			<div class="grid">
				{#each referencePool as opt, i (referencePoolKeys[i])}
					<div class="item readonly">
						<div class="thumb">
							{#if opt.item.type === 'image' && opt.item.url}
								<img src={opt.item.url} alt="" />
							{:else}
								<Icon name={opt.item.type === 'video' ? 'video' : opt.item.type === 'audio' ? 'audio' : 'image'} className="icon" />
							{/if}
						</div>
						<span class="label">{opt.item.label || opt.item.name || opt.fieldLabel}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{:else}
	<div class="ref-tab">
		<div class="ref-head">
			<span class="fl">References for this shot</span>
			<button type="button" class="all-btn" onclick={selectAllReferences}>All</button>
		</div>
		{#if referencePool.length === 0}
			<p class="empty">No items on the reference pool yet — add some on the form's References tab.</p>
		{:else}
			<div class="grid">
				{#each referencePool as opt, i (referencePoolKeys[i])}
					{@const checked = isReferenceSelected(opt.field, opt.item.path)}
					<label class="item" class:checked>
						<input type="checkbox" {checked} onchange={() => toggleReference(opt.field, opt.item.path)} />
						<div class="thumb">
							{#if opt.item.type === 'image' && opt.item.url}
								<img src={opt.item.url} alt="" />
							{:else}
								<Icon name={opt.item.type === 'video' ? 'video' : opt.item.type === 'audio' ? 'audio' : 'image'} className="icon" />
							{/if}
						</div>
						<span class="label">{opt.item.label || opt.item.name || opt.fieldLabel}</span>
					</label>
				{/each}
			</div>
		{/if}
	</div>
{/if}

<style>
	.ref-tab {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.ref-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	.fl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
	}
	.stage-cap {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
	}
	.all-btn {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		background: none;
		border: none;
		color: rgb(var(--fg-subtle));
		cursor: pointer;
	}
	.all-btn:hover {
		color: rgb(var(--fg));
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
		cursor: pointer;
		box-shadow: inset 0 0 0 1px rgb(var(--line));
	}
	.item:hover {
		box-shadow: inset 0 0 0 1px rgb(var(--line-hover));
	}
	.item.checked {
		box-shadow: inset 0 0 0 1px rgb(var(--signal));
		background: rgb(var(--signal) / 0.1);
	}
	.item.readonly {
		cursor: default;
	}
	.item.readonly:hover {
		box-shadow: inset 0 0 0 1px rgb(var(--line));
	}
	.thumb {
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
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.thumb :global(.icon) {
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
</style>
