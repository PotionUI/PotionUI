<script lang="ts">
	import { onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import MaskLayer from '$lib/components/tool-tabs/image/MaskLayer.svelte';
	import MaskOptions from '$lib/components/tool-tabs/image/MaskOptions.svelte';
	import { Button } from '$lib/components/ui';
	import { loadImage } from '$lib/media/loadImage';
	import { MASK_BRUSH, STORED_MASK_CUTOFF } from '$lib/tool-tabs/image/mask';
	import { overlays } from '$lib/editors/overlays';
	import { activeOverlay, maskToolEntries, maskToolState, quickMaskHint } from '$lib/tool-tabs/image/maskOverlays';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource;
	export let existingMaskUrl: string | null = null;
	export let busy: boolean = false;
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	let layer: ReturnType<typeof MaskLayer> | undefined;
	let size: { width: number; height: number } | null = null;
	let loadError: string | null = null;
	let brush: number = MASK_BRUSH.default;
	let erase = false;
	let painted = existingMaskUrl !== null;
	let emptyNotice: string | null = null;
	const overlayRevision = overlays.revision;

	$: entries = $overlayRevision >= 0 ? maskToolEntries() : [];
	$: quickHint = quickMaskHint(activeOverlay(entries, $maskToolState));

	onMount(async () => {
		try {
			const image = await loadImage(source.url);
			size = { width: image.naturalWidth, height: image.naturalHeight };
		} catch {
			loadError = 'This image could not be opened for masking.';
		}
	});

	$: rejection = loadError ?? (painted ? null : 'Paint over the area to repaint.');

	function clearMask() {
		layer?.clear();
		painted = false;
		emptyNotice = null;
	}

	function save() {
		if (!layer) return;
		if (!layer.hasStrokes()) {
			painted = false;
			return;
		}
		const output = layer.renderMask(STORED_MASK_CUTOFF);
		if (!output) {
			emptyNotice = 'This browser could not open a drawing surface.';
			return;
		}
		emptyNotice = null;
		commit({ via: 'mask', dataUrl: output.toDataURL('image/png') });
	}
</script>

<EditorShell
	title="Create inpainting mask"
	fileName={source.fileName}
	icon="brush"
	widthClass="md:w-[min(60rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col md:flex-row items-stretch min-h-0">
		<div class="flex-1 min-w-0 flex items-center justify-center p-4 bg-canvas">
			{#if loadError}
				<p class="py-16 text-sm text-danger">{loadError}</p>
			{:else}
				<div class="relative max-h-[56vh] rounded overflow-hidden bg-surface-2" data-mask-stage>
					<img
						src={source.url}
						alt={source.fileName}
						draggable="false"
						class="block max-h-[56vh] max-w-full select-none"
					/>
					{#if size}
						<MaskLayer
							bind:this={layer}
							width={size.width}
							height={size.height}
							existing={existingMaskUrl}
							{brush}
							{erase}
							disabled={busy}
							onstroke={() => (painted = true)}
						/>
					{/if}
				</div>
			{/if}
		</div>

		<div
			class="w-full md:w-60 shrink-0 flex flex-col gap-4 p-4 border-t md:border-t-0 md:border-l border-line bg-surface-1"
		>
			<MaskOptions bind:brush bind:erase />

			<p class="text-sm text-fg-subtle" data-quick-hint>{quickHint}</p>

			<div class="flex-1"></div>

			<Button variant="secondary" size="sm" onclick={clearMask}>Clear mask</Button>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			allowReplace={false}
			{busy}
			blockedReason={rejection}
			reserveMessage
			failureMessage={emptyNotice ?? failureMessage}
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
