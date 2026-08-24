<script lang="ts">
	// A shot (chain) or timed prompt block (timeline), staged: prompt centre,
	// two gates flanking it, one labelled footer strip. Anatomy never changes
	// between routings -- only which footer cells and gate kinds render does.
	import type { VideoDirectorValue, DirectorCapabilities, DirectorKeyframe, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { LoraPickerItem } from '$lib/types/models';
	import type { StageShotModel, StageGate, StageGateWell } from './stageModel';
	import {
		withShotPromptSegments,
		withShotLoras,
		withShotReferences,
		withDuplicatedShot,
		withChainLeadingMedia,
		withChainTrailingMedia,
		withTimelineKeyframeMedia,
		withTrimShotToCap
	} from './stageModel';
	import {
		applyDirectorOperations,
		collectFormMediaOptions,
		formMediaOptionKeys,
		isSegmentFormMediaReference,
		resolveDirectorMediaDisplay
	} from '$lib/utils/videoDirector';
	import { selectRailObject } from './railSelection';
	import { mintId } from '../timelineCore';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import LoraPickerField from '$lib/components/form-fields/LoraPickerField.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';

	let {
		model,
		doc,
		caps,
		formData,
		presetId,
		onDoc
	}: {
		model: StageShotModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData: Record<string, unknown> | null | undefined;
		presetId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let showLoras = $state(false);
	let showReferencePicker = $state(false);
	let segment = $derived(model.routing === 'chain' ? doc.chain.segments.find((s) => s.id === model.id) : undefined);
	let stacks = $derived(segment?.loras ?? { high: [], low: [] });

	// References lives on whichever routing's segment list actually holds this
	// shot -- unlike `segment` above (chain-only, for the LoRA stacks), the
	// References cell/picker renders under both routings.
	let referenceSegment = $derived(
		model.routing === 'chain' ? doc.chain.segments.find((s) => s.id === model.id) : doc.timeline.segments.find((s) => s.id === model.id)
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
		onDoc(withShotReferences(doc, caps, model.id, next));
	}
	function selectAllReferences() {
		onDoc(withShotReferences(doc, caps, model.id, []));
	}

	function updatePromptSegments(segments: StageShotModel['promptSegments']) {
		onDoc(withShotPromptSegments(doc, caps, model.id, segments));
	}
	function patchLoras(partial: Partial<{ high: LoraPickerItem[]; low: LoraPickerItem[] }>) {
		onDoc(withShotLoras(doc, caps, model.id, { ...stacks, ...partial }));
	}
	function remove() {
		onDoc(applyDirectorOperations(doc, [{ op: 'remove_segment', id: model.id }], caps));
	}
	function duplicate() {
		onDoc(withDuplicatedShot(doc, caps, model.id));
	}
	function setDuration(seconds: number) {
		if (!Number.isFinite(seconds) || seconds <= 0) return;
		onDoc(applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: model.id, duration: seconds } }], caps));
	}
	function trimToCap() {
		if (model.trimToSeconds != null) onDoc(withTrimShotToCap(doc, caps, model.id, model.trimToSeconds));
	}

	function gateEdgeSeconds(edge: 'leading' | 'trailing'): number {
		return edge === 'leading' ? (model.footer.startSeconds ?? 0) : (model.footer.endSeconds ?? 0);
	}
	function gateRole(edge: 'leading' | 'trailing'): DirectorKeyframe['role'] {
		if (edge === 'leading' && model.isFirst) return 'first';
		if (edge === 'trailing' && model.isLast) return 'last';
		return 'free';
	}
	function handleWellChange(edge: 'leading' | 'trailing', value: DirectorMediaValue | null) {
		if (model.routing === 'chain') {
			onDoc(edge === 'leading' ? withChainLeadingMedia(doc, model.id, value) : withChainTrailingMedia(doc, model.id, value));
			return;
		}
		const id = mintId('tl-kf', doc.timeline.keyframes);
		onDoc(withTimelineKeyframeMedia(doc, id, gateRole(edge), gateEdgeSeconds(edge), value));
	}
</script>

{#snippet gateBox(gate: StageGate, edge: 'leading' | 'trailing')}
	{#if gate.kind === 'statement' && gate.label === 'End of video'}
		<div class="w-[190px] flex-shrink-0 self-stretch">
			<div
				class="flex h-full min-h-[100px] w-full flex-col items-center justify-center gap-1.5 rounded-lg border border-line"
				style="background: repeating-linear-gradient(-45deg, rgb(var(--canvas)) 0px, rgb(var(--canvas)) 6px, rgb(var(--surface-1) / 0.55) 6px, rgb(var(--surface-1) / 0.55) 12px)"
			>
				<span class="font-mono text-2xs uppercase tracking-wide text-fg-disabled">{gate.label}</span>
			</div>
		</div>
	{:else}
		<div class="w-[190px] flex-shrink-0 {gate.kind === 'well' ? 'flex flex-col self-stretch' : ''}">
			{#if gate.kind === 'well'}
				<div class="min-h-0 flex-1">
					<DirectorMediaSlot
						name={(gate as StageGateWell).slotName}
						value={gate.media}
						{formData}
						kind="image"
						fill
						onChange={(v) => handleWellChange(edge, v)}
						config={{ accept: 'image/*' }}
					/>
				</div>
				<div class="mt-1.5 text-2xs text-fg-subtle">{gate.helpText}</div>
			{:else if gate.kind === 'inherited'}
				<button
					type="button"
					class="flex h-[100px] w-full flex-col items-center justify-center gap-1.5 rounded-lg bg-canvas text-fg-subtle ring-1 ring-inset ring-line transition-colors hover:ring-signal"
					onclick={() => selectRailObject('seam', gate.seamId)}
				>
					<span class="font-mono text-2xs uppercase tracking-wide text-signal/80">Inherited</span>
					<span class="font-mono text-2xs tabular-nums">{gate.overlapFrames} f</span>
				</button>
				<div class="mt-1.5 text-2xs text-fg-subtle">{gate.helpText}</div>
			{:else if gate.kind === 'keyframe'}
				{@const display = resolveDirectorMediaDisplay(gate.media, formData)}
				{@const imageUrl = display.kind === 'embedded' || display.kind === 'form_ref' ? display.media.url : null}
				<button
					type="button"
					class="relative flex h-[100px] w-full flex-col items-center justify-center gap-1.5 overflow-hidden rounded-lg bg-canvas text-fg-subtle ring-1 ring-inset ring-line transition-colors hover:ring-signal"
					onclick={() => selectRailObject('keyframe', gate.keyframeId)}
				>
					{#if imageUrl}
						<img src={imageUrl} alt="" class="absolute inset-0 h-full w-full object-contain" />
						<div
							class="pointer-events-none absolute inset-x-0 bottom-0 h-10"
							style="background: linear-gradient(to top, rgb(var(--canvas) / 0.85), transparent)"
						></div>
						<span class="absolute inset-x-0 bottom-1 px-2 text-left font-mono text-2xs text-fg-subtle">{gate.label}</span>
					{:else}
						<Icon name="image" className="h-4 w-4" />
						<span class="text-2xs">{gate.label}</span>
					{/if}
				</button>
				{#if gate.helpText}<div class="mt-1.5 text-2xs text-fg-subtle">{gate.helpText}</div>{/if}
			{:else}
				<button
					type="button"
					class="flex h-[100px] w-full flex-col items-center justify-center gap-1.5 rounded-lg bg-canvas text-fg-subtle ring-1 ring-inset ring-surface-2 {gate.seamId ? 'cursor-pointer hover:ring-line-hover' : ''}"
					disabled={!gate.seamId}
					onclick={() => gate.seamId && selectRailObject('seam', gate.seamId)}
				>
					<span class="text-xs">{gate.label}</span>
				</button>
				{#if gate.helpText}<div class="mt-1.5 text-2xs text-fg-subtle">{gate.helpText}</div>{/if}
			{/if}
		</div>
	{/if}
{/snippet}

<div class="flex flex-col gap-3.5">
	<div class="flex items-center gap-2.5">
		<span class="rounded bg-signal px-1.5 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wide text-canvas">
			Shot {model.index + 1}
		</span>
		<span class="text-sm font-semibold text-fg">{model.label}</span>
		<div class="flex-1"></div>
		{#if model.canDuplicate}
			<IconButton icon="copy" label="Duplicate shot" size="sm" onclick={duplicate} />
		{/if}
		<IconButton icon="trash" label="Remove shot" size="sm" onclick={remove} />
	</div>

	<div class="flex items-start gap-5">
		{@render gateBox(model.leadingGate, 'leading')}

		<div class="min-w-0 flex-1">
			<div class="rounded-lg border border-line-strong bg-canvas p-3 shadow-well">
				<SegmentedPromptEditor
					segments={model.promptSegments}
					label="Shot direction"
					showPreview={false}
					compact
					placeholder={model.isFirst && model.isLast ? 'Describe the first shot…' : "Describe this shot's action, camera and composition…"}
					on:segmentsChange={(e) => updatePromptSegments(e.detail)}
				/>
			</div>
			{#if model.showTeachingCopy}
				<div class="mt-2.5 max-w-[62ch] border-t border-surface-2 pt-2.5 text-xs leading-5 text-fg-muted">
					{#if model.routing === 'chain'}
						Hang a start frame on this shot and it becomes image-to-video. Add a second shot and the two are joined into a chain.
					{:else}
						Hang a frame on either edge and this block interpolates to it. Add a second block for a second timed prompt.
					{/if}
				</div>
			{/if}
		</div>

		{@render gateBox(model.trailingGate, 'trailing')}
	</div>

	{#if model.overCap}
		<div class="flex items-center gap-3 rounded-md bg-danger/10 px-3 py-2 ring-1 ring-inset ring-danger/40">
			<Icon name="warning" className="h-4 w-4 flex-shrink-0 text-danger" />
			<span class="flex-1 text-xs text-fg">
				This shot is <span class="font-mono tabular-nums">{model.footer.frames}</span> frames. The preset generates at most
				<span class="font-mono tabular-nums">{model.footer.capFrames}</span> per shot.
			</span>
			{#if model.trimToSeconds != null}
				<button type="button" class="rounded bg-surface-2 px-2.5 py-1 text-xs text-fg hover:bg-surface-3" onclick={trimToCap}>
					Trim to {model.trimToSeconds.toFixed(2)} s
				</button>
			{/if}
		</div>
	{/if}

	<div class="flex flex-wrap items-stretch overflow-hidden rounded-lg border border-line-strong bg-canvas shadow-well">
		<div class="min-w-[120px] border-r border-surface-2 px-3.5 py-2">
			<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Shot type</div>
			<div class="mt-0.5 text-xs text-fg">{model.footer.shotTypeLabel}</div>
		</div>

		{#if model.routing === 'chain'}
			<div class="min-w-[96px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Duration</div>
				<input
					type="number"
					step="0.01"
					min="0.01"
					class="mt-0.5 w-16 bg-transparent font-mono text-xs tabular-nums text-fg outline-none"
					value={model.footer.durationSeconds.toFixed(2)}
					onchange={(e) => setDuration(parseFloat((e.currentTarget as HTMLInputElement).value))}
				/>
				<span class="font-mono text-2xs text-fg-subtle">s</span>
			</div>
		{:else}
			<div class="min-w-[96px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Start</div>
				<div class="mt-0.5 font-mono text-xs tabular-nums text-fg">{(model.footer.startSeconds ?? 0).toFixed(2)} s</div>
			</div>
			<div class="min-w-[96px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">End</div>
				<div class="mt-0.5 font-mono text-xs tabular-nums text-fg">{(model.footer.endSeconds ?? 0).toFixed(2)} s</div>
			</div>
		{/if}

		<div class="min-w-[104px] border-r border-surface-2 px-3.5 py-2">
			<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Frames</div>
			<div class="mt-0.5 font-mono text-xs tabular-nums {model.overCap ? 'text-danger' : 'text-fg'}">
				{model.footer.frames}
				{#if model.footer.capFrames != null}<span class="text-fg-subtle"> / {model.footer.capFrames}</span>{/if}
				{#if model.footer.newFrames != null}<span class="text-fg-subtle"> · {model.footer.newFrames} new</span>{/if}
			</div>
		</div>

		{#if model.routing === 'chain'}
			<div class="min-w-[110px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Seed</div>
				<div class="mt-0.5 font-mono text-xs tabular-nums text-fg-subtle">—</div>
			</div>
			<div class="min-w-[74px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Steps</div>
				<div class="mt-0.5 font-mono text-xs tabular-nums text-fg-subtle">—</div>
			</div>
			<div class="min-w-[74px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">CFG</div>
				<div class="mt-0.5 font-mono text-xs tabular-nums text-fg-subtle">—</div>
			</div>
		{/if}

		{#if model.footer.showLoras}
			<div class="min-w-0 flex-1 border-r border-surface-2 px-3.5 py-2">
				<button type="button" class="flex w-full items-center gap-1.5 text-left" onclick={() => (showLoras = !showLoras)} aria-expanded={showLoras}>
					<div>
						<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">LoRAs · this shot</div>
						<div class="mt-0.5 text-xs text-fg">{model.footer.loraSummary}</div>
					</div>
					<Icon name="chevron-down" className="ml-auto h-3.5 w-3.5 flex-shrink-0 text-fg-subtle transition-transform {showLoras ? 'rotate-180' : ''}" />
				</button>
			</div>
		{/if}

		{#if model.footer.references}
			<div class="min-w-[110px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">References</div>
				{#if model.footer.references.capability === 'whole'}
					<div class="mt-0.5 text-xs text-fg-subtle">Whole film</div>
				{:else}
					<button
						type="button"
						class="mt-0.5 font-mono text-xs tabular-nums text-fg underline decoration-line-strong decoration-dotted underline-offset-2 hover:text-signal"
						onclick={() => (showReferencePicker = !showReferencePicker)}
						aria-expanded={showReferencePicker}
					>
						{model.footer.references.selectedCount == null
							? `All (${model.footer.references.poolCount})`
							: `${model.footer.references.selectedCount} of ${model.footer.references.poolCount}`}
					</button>
				{/if}
			</div>
		{/if}

		{#if model.routing === 'chain' && model.footer.joinOutLabel}
			<div class="min-w-[150px] px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Join out</div>
				<div class="mt-0.5 text-xs text-fg">{model.footer.joinOutLabel}</div>
			</div>
		{/if}
	</div>

	{#if model.footer.references?.capability === 'per_shot' && showReferencePicker}
		<div class="space-y-2 rounded-lg border border-surface-2 bg-surface-1 p-3">
			<div class="flex items-center justify-between">
				<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">References for this shot</span>
				<button type="button" class="font-mono text-2xs text-fg-subtle hover:text-fg" onclick={selectAllReferences}>All</button>
			</div>
			{#if referencePool.length === 0}
				<p class="text-xs text-fg-subtle">No items on the reference pool yet -- add some on the form's References tab.</p>
			{:else}
				<div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
					{#each referencePool as opt, i (referencePoolKeys[i])}
						{@const checked = isReferenceSelected(opt.field, opt.item.path)}
						<label
							class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 ring-1 ring-inset {checked
								? 'bg-signal/10 ring-signal'
								: 'ring-line hover:ring-line-hover'}"
						>
							<input type="checkbox" {checked} onchange={() => toggleReference(opt.field, opt.item.path)} />
							<div class="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded bg-surface-2">
								{#if opt.item.type === 'image' && opt.item.url}
									<img src={opt.item.url} alt="" class="h-full w-full object-cover" />
								{:else}
									<Icon
										name={opt.item.type === 'video' ? 'video' : opt.item.type === 'audio' ? 'audio' : 'image'}
										className="h-3.5 w-3.5 text-fg-subtle"
									/>
								{/if}
							</div>
							<span class="truncate text-xs text-fg">{opt.item.label || opt.item.name || opt.fieldLabel}</span>
						</label>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	{#if model.footer.showLoras && showLoras}
		<div class="space-y-3 rounded-lg border border-surface-2 bg-surface-1 p-3">
			<LoraPickerField
				name="{model.id}-high"
				value={stacks.high}
				onChange={(_n, v) => patchLoras({ high: v as LoraPickerItem[] })}
				config={{ preset_id: presetId, title: 'HIGH-NOISE', configuration: { model_type: 'lora' } }}
			/>
			<LoraPickerField
				name="{model.id}-low"
				value={stacks.low}
				onChange={(_n, v) => patchLoras({ low: v as LoraPickerItem[] })}
				config={{ preset_id: presetId, title: 'LOW-NOISE', configuration: { model_type: 'lora' } }}
			/>
		</div>
	{/if}
</div>
