<script lang="ts">
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import { describeRejection, type EditOperation, type ResizeQuality } from '$lib/media/editOperations';
	import {
		deriveMediaSizeOptions,
		isKnownSize,
		matchingSide,
		parseCustomSize,
		pixelShare,
		preferredMediaSize,
		SOURCE_TIER,
		type MediaSize,
		type MediaSizeOption
	} from '$lib/media/mediaSizeOptions';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource;
	export let busy: boolean = false;
	export let allowReplace: boolean = true;
	export let blockedReason: string | null = null;
	export let resourceNote: string | null = null;
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	const CUSTOM = 'custom';
	const QUALITIES: { key: ResizeQuality; label: string }[] = [
		{ key: 'high', label: 'High' },
		{ key: 'balanced', label: 'Balanced' },
		{ key: 'small', label: 'Small file' }
	];

	let probedSize: MediaSize | null = null;
	let loadError: string | null = null;
	let selected: string | null = null;
	let customWidth = '';
	let customHeight = '';
	let quality: ResizeQuality = 'balanced';

	$: isVideo = source.kind === 'video';
	$: sourceSize = probedSize ?? { width: source.width ?? 0, height: source.height ?? 0 };
	$: known = isKnownSize(sourceSize);
	$: options = deriveMediaSizeOptions(sourceSize);
	$: tiers = groupByTier(options);
	$: if (selected === null && options.length > 0) selectOption(preferredMediaSize(options));

	$: custom = selected === CUSTOM ? parseCustomSize(sourceSize, customWidth, customHeight, isVideo) : null;
	$: picked = options.find((option) => option.value === selected) ?? null;
	$: target = custom ? (custom.ok ? custom.size : null) : picked ? { width: picked.width, height: picked.height } : null;
	$: unchanged =
		(!custom && picked?.tier === SOURCE_TIER) ||
		(!!target && target.width >= sourceSize.width && target.height >= sourceSize.height);

	$: operations = target && !unchanged ? [resizeOperation(target, quality, isVideo)] : [];
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

	function resizeOperation(size: MediaSize, chosen: ResizeQuality, video: boolean): EditOperation {
		return video
			? { type: 'resize', width: size.width, height: size.height, quality: chosen }
			: { type: 'resize', width: size.width, height: size.height };
	}

	function selectOption(option: MediaSizeOption | null) {
		if (!option) return;
		selected = option.value;
	}

	function chooseCustom() {
		if (selected !== CUSTOM && target) {
			customWidth = String(target.width);
			customHeight = String(target.height);
		}
		selected = CUSTOM;
	}

	function typeWidth(event: Event) {
		customWidth = (event.currentTarget as HTMLInputElement).value;
		customHeight = matchingSide(sourceSize, 'width', customWidth);
	}

	function typeHeight(event: Event) {
		customHeight = (event.currentTarget as HTMLInputElement).value;
		customWidth = matchingSide(sourceSize, 'height', customHeight);
	}

	function handleImageLoad(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		if (image.naturalWidth > 0 && image.naturalHeight > 0) {
			probedSize = { width: image.naturalWidth, height: image.naturalHeight };
		}
	}

	function handleVideoMetadata(event: Event) {
		const video = event.currentTarget as HTMLVideoElement;
		if (video.videoWidth > 0 && video.videoHeight > 0) {
			probedSize = { width: video.videoWidth, height: video.videoHeight };
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
			{:else if isVideo}
				<video
					src={source.url}
					class="max-h-[420px] max-w-full rounded bg-surface-2"
					controls
					muted
					preload="metadata"
					on:loadedmetadata={handleVideoMetadata}
					on:error={handleLoadError}
				></video>
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
					<p class="mb-2 font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">{group.tier}</p>
					<div class="grid grid-cols-2 gap-1.5">
						{#each group.options as option (option.value)}
							<button
								type="button"
								aria-pressed={selected === option.value}
								class="h-12 px-2 flex flex-col items-start justify-center gap-0.5 rounded border transition-colors {chipClass(
									selected === option.value
								)}"
								on:click={() => selectOption(option)}
							>
								<span class="text-xs font-semibold">{option.label}</span>
								<span class="font-mono text-xs tabular-nums">{option.width}×{option.height}</span>
							</button>
						{/each}
					</div>
				</div>
			{/each}

			{#if known}
				<div>
					<button
						type="button"
						aria-pressed={selected === CUSTOM}
						class="w-full h-8 px-2 rounded border text-xs text-left transition-colors {chipClass(
							selected === CUSTOM
						)}"
						on:click={chooseCustom}
					>
						Custom size
					</button>
					{#if selected === CUSTOM}
						<div class="mt-2 flex items-center gap-1.5">
							<input
								type="text"
								inputmode="numeric"
								aria-label="Width"
								value={customWidth}
								on:input={typeWidth}
								class="input w-0 flex-1 h-8 px-2 font-mono text-sm tabular-nums text-fg"
							/>
							<span class="text-xs text-fg-subtle">×</span>
							<input
								type="text"
								inputmode="numeric"
								aria-label="Height"
								value={customHeight}
								on:input={typeHeight}
								class="input w-0 flex-1 h-8 px-2 font-mono text-sm tabular-nums text-fg"
							/>
						</div>
						<p class="mt-1.5 text-xs text-fg-subtle">Type one side, the other follows the original shape.</p>
					{/if}
				</div>
			{/if}

			{#if isVideo}
				<div>
					<p class="mb-2 font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Quality</p>
					<div class="grid grid-cols-3 gap-1.5">
						{#each QUALITIES as option (option.key)}
							<button
								type="button"
								aria-pressed={quality === option.key}
								class="h-8 rounded border text-xs transition-colors {chipClass(quality === option.key)}"
								on:click={() => (quality = option.key)}
							>
								{option.label}
							</button>
						{/each}
					</div>
				</div>
			{/if}

			<div class="h-px bg-line"></div>

			<div class="flex flex-col gap-1.5">
				<div class="flex items-center justify-between">
					<span class="text-xs text-fg-subtle">Original</span>
					<span class="font-mono text-xs tabular-nums text-fg-muted">
						{known ? `${sourceSize.width}×${sourceSize.height}` : '—'}
					</span>
				</div>
				<div class="flex items-center justify-between">
					<span class="text-xs text-fg-subtle">Result</span>
					<span
						data-testid="resize-result"
						class="font-mono text-xs tabular-nums {target && !unchanged ? 'text-success' : 'text-fg-muted'}"
					>
						{target ? `${target.width}×${target.height}` : '—'}
					</span>
				</div>
				{#if target && !unchanged}
					<p class="text-xs text-fg-subtle">{pixelShare(sourceSize, target)}% of the original pixels</p>
				{/if}
			</div>
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
