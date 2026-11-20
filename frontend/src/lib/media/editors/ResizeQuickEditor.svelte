<script lang="ts">
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import ResizeOptions from '$lib/components/tool-tabs/image/ResizeOptions.svelte';
	import { describeRejection, type EditOperation } from '$lib/media/editOperations';
	import {
		deriveMediaSizeOptions,
		isKnownSize,
		parseCustomSize,
		pixelShare,
		preferredMediaSize,
		type MediaSize,
		type MediaSizeOption
	} from '$lib/media/mediaSizeOptions';
	import { resizeOutput } from '$lib/tool-tabs/image/payload';
	import type { ResizeParams } from '$lib/tool-tabs/image/steps';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource;
	export let busy: boolean = false;
	export let allowReplace: boolean = true;
	export let blockedReason: string | null = null;
	export let resourceNote: string | null = null;
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	let probedSize: MediaSize | null = null;
	let loadError: string | null = null;
	let params: ResizeParams | null = null;

	$: sourceSize = probedSize ?? { width: source.width ?? 0, height: source.height ?? 0 };
	$: known = isKnownSize(sourceSize);
	$: options = deriveMediaSizeOptions(sourceSize);
	$: tiers = groupByTier(options);
	$: if (params === null && options.length > 0) choose(preferredMediaSize(options));

	$: target = params && known ? resizeOutput(params, sourceSize) : null;
	$: custom = target ? parseCustomSize(sourceSize, String(target.width), String(target.height), false) : null;
	$: unchanged = !!target && target.width >= sourceSize.width && target.height >= sourceSize.height;
	$: operations = target && !unchanged && custom?.ok ? [resizeOperation(target)] : [];
	$: rejection =
		loadError ??
		blockedReason ??
		(!known ? 'Reading the size of this media…' : null) ??
		(custom && !custom.ok ? custom.error : null) ??
		(unchanged ? 'Pick a size smaller than the original.' : null) ??
		describeRejection(operations, source.kind);

	function groupByTier(list: MediaSizeOption[]): { tier: string; options: MediaSizeOption[] }[] {
		const groups: { tier: string; options: MediaSizeOption[] }[] = [];
		for (const option of list) {
			const group = groups.find((entry) => entry.tier === option.tier);
			if (group) group.options.push(option);
			else groups.push({ tier: option.tier, options: [option] });
		}
		return groups;
	}

	function resizeOperation(size: MediaSize): EditOperation {
		return { type: 'resize', width: size.width, height: size.height };
	}

	function choose(option: MediaSizeOption | null) {
		if (!option) return;
		params = { width: option.width, height: option.height, lock: true };
	}

	function isChosen(option: MediaSizeOption): boolean {
		return !!target && target.width === option.width && target.height === option.height;
	}

	function handleImageLoad(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		if (image.naturalWidth > 0 && image.naturalHeight > 0) {
			probedSize = { width: image.naturalWidth, height: image.naturalHeight };
		}
	}

	function handleLoadError() {
		if (!known) loadError = 'This media could not be opened for resizing.';
	}

	function save(mode: 'new' | 'replace') {
		if (rejection) return;
		commit({ via: 'operations', operations, mode });
	}

	function chipClass(active: boolean): string {
		return active
			? 'border-signal/60 bg-signal/10 text-signal'
			: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';
	}
</script>

<EditorShell
	title="Resize"
	fileName={source.fileName}
	icon="resize"
	widthClass="md:w-[min(64rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col md:flex-row items-stretch min-h-0">
		<div class="flex-1 min-w-0 flex items-center justify-center p-4 bg-canvas">
			{#if loadError}
				<p class="py-16 text-sm text-danger">{loadError}</p>
			{:else}
				<img
					src={source.url}
					alt={source.fileName}
					draggable="false"
					class="max-h-[420px] max-w-full rounded bg-surface-2 select-none"
					on:load={handleImageLoad}
					on:error={handleLoadError}
				/>
			{/if}
		</div>

		<div
			class="w-full md:w-72 shrink-0 flex flex-col gap-4 p-4 border-t md:border-t-0 md:border-l border-line bg-surface-1"
		>
			{#each tiers as group (group.tier)}
				<div>
					<p class="mb-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{group.tier}</p>
					<div class="grid grid-cols-2 gap-1.5">
						{#each group.options as option (option.value)}
							<button
								type="button"
								aria-pressed={isChosen(option)}
								class="h-12 px-2 flex flex-col items-start justify-center gap-0.5 rounded border transition-colors {chipClass(isChosen(option))}"
								on:click={() => choose(option)}
							>
								<span class="text-sm font-semibold">{option.label}</span>
								<span class="font-mono text-sm tabular-nums">{option.width}×{option.height}</span>
							</button>
						{/each}
					</div>
				</div>
			{/each}

			{#if known && params}
				<div class="h-px bg-line"></div>
				<ResizeOptions {params} input={sourceSize} scales={[]} onchange={(next) => (params = next)} />
				{#if target && !unchanged}
					<p class="text-sm text-fg-muted">{pixelShare(sourceSize, target)}% of the original pixels</p>
				{/if}
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			{busy}
			{allowReplace}
			note={resourceNote}
			blockedReason={rejection}
			{failureMessage}
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
