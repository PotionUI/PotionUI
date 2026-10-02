<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Waveform from '$lib/components/Waveform.svelte';
	import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
	import { Badge, IconButton, Input } from '$lib/components/ui';
	import { formatBytes } from '$lib/utils/format';
	import { resolvedTheme } from '$lib/stores/theme';
	import type { MediaTool, MediaToolGroup } from '$lib/tools/tools';
	import { DEFAULT_WAVEFORM_PALETTE, readWaveformPalette, waveformConfig } from './mediaFieldWaveform';
	import { metaChips, type MediaItemMetadata } from './mediaLoaderMeta';
	import type { ItemHandle } from './mediaFieldGroups';
	import type { MediaKind } from './mediaLoaderConfig';

	let {
		kind,
		url,
		name,
		metadata = null,
		multiple,
		handle = null,
		label = '',
		maxLabelLength = 64,
		hasMask = false,
		fromGeneration = false,
		toolGroups,
		uploading = null,
		dropHint = null,
		broken = $bindable(false),
		onLabel,
		onTool,
		onClearMask,
		onReplace,
		onRemove,
		onPreview
	}: {
		kind: MediaKind;
		url: string;
		name: string;
		metadata?: MediaItemMetadata | null;
		multiple: boolean;
		handle?: ItemHandle | null;
		label?: string;
		maxLabelLength?: number;
		hasMask?: boolean;
		fromGeneration?: boolean;
		toolGroups: MediaToolGroup[];
		uploading?: { name: string; size: number | null; progress: number } | null;
		dropHint?: string | null;
		broken?: boolean;
		onLabel?: (label: string) => void;
		onTool: (tool: MediaTool) => void;
		onClearMask?: () => void;
		onReplace?: () => void;
		onRemove: () => void;
		onPreview: () => void;
	} = $props();

	let toolsOpen = $state(false);
	let audioElement: HTMLAudioElement | null = $state(null);
	let palette = $state(DEFAULT_WAVEFORM_PALETTE);
	let chips = $derived(url && !broken ? metaChips(metadata, kind, name, { edited: false }) : []);
	let previewHeight = $derived(kind === 'audio' ? 'min-h-[104px]' : 'h-[168px]');

	$effect(() => {
		url;
		broken = false;
	});

	$effect(() => {
		const theme = $resolvedTheme;
		if (!theme || typeof requestAnimationFrame !== 'function') {
			palette = readWaveformPalette();
			return;
		}
		const frame = requestAnimationFrame(() => {
			palette = readWaveformPalette();
		});
		return () => cancelAnimationFrame(frame);
	});

	function markBroken() {
		broken = true;
	}
</script>

<div class="rounded-lg border border-line-strong overflow-hidden bg-canvas" data-media-inspector data-kind={kind}>
	<div class="relative bg-surface-2 {previewHeight} flex items-center justify-center dot-grid">
		{#if broken}
			<div class="flex flex-col items-center gap-1.5 text-center px-3" data-media-missing>
				<Icon name="warning" className="w-5 h-5 text-danger" />
				<p class="text-sm text-danger">File not found</p>
				<p class="font-mono text-xs text-fg-subtle">Replace it or remove it</p>
			</div>
		{:else if !url}
			<Icon name={kind} className="w-6 h-6 text-fg-subtle" />
		{:else if kind === 'image'}
			<button
				type="button"
				class="w-full h-full flex items-center justify-center"
				aria-label="View full size"
				onclick={onPreview}
			>
				<img
					src={url}
					alt={name}
					class="max-w-full max-h-full object-contain"
					onerror={markBroken}
				/>
			</button>
		{:else if kind === 'video'}
			<!-- svelte-ignore a11y_media_has_caption -->
			<video src={url} class="w-full h-full object-contain" controls preload="metadata" onerror={markBroken}>
				<track kind="captions" />
			</video>
		{:else}
			<div class="w-full px-3 py-3">
				<Waveform
					{audioElement}
					{url}
					config={waveformConfig(palette, 56)}
					onSeek={(time) => {
						if (audioElement) audioElement.currentTime = time;
					}}
				/>
				<audio bind:this={audioElement} src={url} class="w-full mt-1" controls preload="metadata" onerror={markBroken}>
					Your browser does not support the audio element.
				</audio>
			</div>
		{/if}

		{#if uploading}
			<div
				class="absolute inset-0 bg-canvas/85 flex flex-col items-center justify-center gap-2 px-3"
				data-media-uploading
			>
				<span class="spinner"></span>
				<span class="font-mono text-xs uppercase tracking-[0.06em] tabular-nums text-fg-muted">
					Uploading · {uploading.progress}%
				</span>
				<span class="max-w-full flex items-center gap-2 text-xs text-fg-muted">
					<span class="truncate">{uploading.name}</span>
					{#if uploading.size}
						<span class="shrink-0 font-mono tabular-nums text-fg-subtle">{formatBytes(uploading.size)}</span>
					{/if}
				</span>
				<div class="absolute inset-x-0 bottom-0 h-0.5 bg-line">
					<div class="h-full bg-signal transition-[width] duration-150" style="width: {uploading.progress}%"></div>
				</div>
			</div>
		{/if}

		{#if dropHint}
			<div
				class="absolute inset-0 pointer-events-none ring-2 ring-inset ring-signal bg-signal/10 flex items-center justify-center"
				data-media-drop-overlay
			>
				<span class="text-sm text-signal">{dropHint}</span>
			</div>
		{/if}
	</div>

	<div class="flex items-center gap-1.5 px-2 py-1.5 bg-surface-2 border-t border-line">
		{#if multiple}
			{#if handle}
				<Tooltip text={handle.tooltip} position="top">
					<span
						class="shrink-0 inline-flex items-center gap-1 font-mono text-xs tabular-nums text-fg-muted whitespace-nowrap"
						data-resource-handle={handle.tooltip ? handle.long : undefined}
						data-media-handle
					>
						<span class="hidden sm:inline">{handle.long}</span>
						<span class="sm:hidden">{handle.short}</span>
						{#if handle.uses > 0}
							<span class="text-fg" data-resource-uses>×{handle.uses}</span>
						{/if}
					</span>
				</Tooltip>
			{/if}
			<Input
				type="text"
				value={label}
				placeholder={name}
				maxlength={maxLabelLength}
				aria-label="Label"
				class="min-w-0 flex-1 !h-8 !py-0"
				oninput={(event: Event) => onLabel?.((event.currentTarget as HTMLInputElement).value)}
			/>
		{:else}
			<Icon name={kind} className="w-3.5 h-3.5 shrink-0 text-fg-subtle" strokeWidth={1.8} />
			<Tooltip text={name} position="top" wrapperClass="flex min-w-0 flex-1">
				<span class="min-w-0 truncate text-sm text-fg">{name}</span>
			</Tooltip>
		{/if}

		<ToolsMenu
			open={toolsOpen}
			scope="field"
			groups={toolGroups}
			variant="field"
			placement="down"
			onToggle={() => (toolsOpen = !toolsOpen)}
			onClose={() => (toolsOpen = false)}
			onPick={onTool}
		/>

		{#if !multiple && onReplace}
			<Tooltip text="Replace" position="top">
				<IconButton icon="refresh" label="Replace" size="sm" onclick={onReplace} />
			</Tooltip>
		{/if}
		<Tooltip text={multiple ? 'Remove this item' : 'Remove'} position="top" kbd="Del">
			<IconButton
				icon="trash"
				label={multiple ? 'Remove this item' : 'Remove'}
				size="sm"
				class="hover:!text-danger"
				onclick={onRemove}
			/>
		</Tooltip>
	</div>

	{#if chips.length > 0 || hasMask || broken || fromGeneration}
		<div class="flex flex-wrap items-center gap-1.5 px-2 py-1.5 border-t border-line bg-surface-1">
			{#if broken}
				<Badge variant="danger" class="font-mono tabular-nums">File not found</Badge>
			{/if}
			{#each chips as chip (chip.key)}
				<Badge class="font-mono tabular-nums">{chip.text}</Badge>
			{/each}
			{#if hasMask && kind === 'image' && onClearMask}
				<Tooltip text="Clear the mask" position="top">
					<button type="button" class="rounded" aria-label="Clear the mask" data-mask-chip onclick={onClearMask}>
						<Badge variant="signal" class="font-mono">
							MASK
							<Icon name="close" className="w-3 h-3" />
						</Badge>
					</button>
				</Tooltip>
			{/if}
			{#if fromGeneration}
				<Badge variant="signal" class="font-mono">
					<Icon name="clock" className="w-3 h-3" />
					from generation
				</Badge>
			{/if}
		</div>
	{/if}
</div>

<style>
	.spinner {
		border: 2px solid rgb(var(--line-strong));
		border-top-color: rgb(var(--signal));
		border-radius: 50%;
		width: 24px;
		height: 24px;
		animation: spin 0.9s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
