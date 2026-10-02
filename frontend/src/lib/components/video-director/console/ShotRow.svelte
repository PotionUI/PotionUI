<script lang="ts">
	// `.shot-row` -- the compact, collapsed shot: 128x72 thumb, checkbox,
	// number, title/duration, dependency badge, run state and a chevron that
	// activates (expands) the shot into a ShotCard. `run` is always null in
	// W1 (compileModel leaves it unset until W3 wires a runs map) but the
	// markup renders every state from `shot.run` so W3 only has to flip data,
	// per the brief.
	import type { ConsoleShot } from './consoleModel';
	import { badgeMeta, BADGE_TONE_CLASS } from './badgeMeta';
	import ConsoleIcon from './ConsoleIcon.svelte';
	import { isVideoExample } from '$lib/utils/presetMedia';
	import { nsfwFilterStore, isHiddenByMode, shouldBlurFile } from '$lib/stores/nsfwFilter';
	import { nsfwRevealStore } from '$lib/stores/nsfwReveal';

	let {
		shot,
		checked,
		onToggleChecked,
		onActivate,
		onRetry,
		retryLabel = 'Retry',
		retryNote = null
	}: {
		shot: ConsoleShot;
		checked: boolean;
		onToggleChecked: (shotId: string) => void;
		onActivate: (shotId: string) => void;
		/** Resubmits just this shot (W3) -- only ever rendered for a 'failed' run. */
		onRetry?: (shotId: string) => void;
		retryLabel?: string;
		retryNote?: string | null;
	} = $props();

	nsfwFilterStore.init();
	let outputThumb = $derived(shot.thumb.source === 'output' && !!shot.thumb.url);
	let flaggedFile = $derived({ nsfw: shot.thumb.flagged === true });
	let revealId = $derived(`shot:${shot.id}:${shot.thumb.url ?? ''}`);
	let thumbHidden = $derived(outputThumb && isHiddenByMode(flaggedFile, $nsfwFilterStore.mode));
	let thumbBlurred = $derived(outputThumb && shouldBlurFile(flaggedFile, $nsfwFilterStore.mode, $nsfwRevealStore.has(revealId)));
	let meta = $derived(badgeMeta(shot.badge));
	let thumbIsVideo = $derived(
		!!shot.thumb.url && shot.thumb.source === 'output' && isVideoExample({ src: shot.thumb.url.split('?')[0] })
	);

	let plainThumb = $derived(!!shot.thumb.url && !thumbIsVideo && !thumbBlurred);

	function runLabel(): string {
		const run = shot.run;
		if (!run) return '';
		if (run.kind === 'queued') return 'Queued';
		if (run.kind === 'generating') return `Generating ${run.percent}%`;
		if (run.kind === 'done') return `Done · ${run.time}`;
		return 'Failed';
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="relative flex cursor-pointer flex-wrap items-center gap-x-3.5 gap-y-2 rounded-md border border-line bg-surface-1 px-3 py-2.5 hover:border-line-hover"
	role="button"
	tabindex="0"
	aria-label="Expand {shot.title}"
	onclick={() => onActivate(shot.id)}
	onkeydown={(e) => {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			onActivate(shot.id);
		}
	}}
>
	<button
		type="button"
		class="flex h-4 w-4 flex-none items-center justify-center rounded-[3px] border {checked
			? 'border-fg-muted bg-surface-3'
			: 'border-line-strong bg-surface-2'}"
		aria-label={checked ? 'Selected for generation' : 'Select for generation'}
		onclick={(e) => {
			e.stopPropagation();
			onToggleChecked(shot.id);
		}}
	>
		{#if checked}
			<ConsoleIcon name="check" class="h-2.5 w-2.5 text-fg" />
		{/if}
	</button>

	{#if thumbHidden}
		<div
			class="row-thumb flex h-[72px] w-32 flex-none flex-col items-center justify-center gap-0.5 rounded-md border border-line-strong bg-surface-2 text-xs text-fg-subtle"
			data-thumb-hidden
		>
			<ConsoleIcon name="image" class="h-[18px] w-[18px]" />
			Hidden
		</div>
	{:else}
		<div
			class="row-thumb relative h-[72px] w-32 flex-none overflow-hidden rounded-md shadow-raised {plainThumb
				? 'bg-cover bg-center'
				: shot.thumb.url
					? ''
					: 'flex items-center justify-center border border-dashed border-line-strong bg-canvas'}"
			style={plainThumb ? `background-image:url(${JSON.stringify(shot.thumb.url)})` : ''}
			data-thumb-blurred={thumbBlurred ? '' : undefined}
		>
			{#if thumbIsVideo}
				<video
					src={shot.thumb.url}
					class="h-full w-full object-cover {thumbBlurred ? 'scale-110 blur-2xl' : ''}"
					muted
					playsinline
					preload={thumbBlurred ? 'none' : 'metadata'}
				>
					<track kind="captions" />
				</video>
			{:else if shot.thumb.url && thumbBlurred}
				<div
					class="h-full w-full scale-110 bg-cover bg-center blur-2xl"
					style="background-image:url({JSON.stringify(shot.thumb.url)})"
				></div>
			{:else if !shot.thumb.url}
				<ConsoleIcon name="image" class="h-[18px] w-[18px] text-fg-disabled" />
			{/if}
			{#if thumbBlurred}
				<button
					type="button"
					class="absolute inset-0 flex flex-col items-center justify-center gap-0.5 bg-canvas/40 text-xs font-medium text-fg"
					aria-label="Sensitive content, click to reveal"
					onclick={(e) => {
						e.stopPropagation();
						nsfwRevealStore.reveal(revealId);
					}}
				>
					Sensitive
				</button>
			{/if}
		</div>
	{/if}

	<span class="flex-none font-mono text-[11px] text-fg-subtle">{shot.number}</span>

	<div class="flex min-w-32 flex-1 flex-col items-start gap-1">
		<span class="w-full truncate text-left text-[13.5px] font-medium text-fg">{shot.title}</span>
		<span class="flex items-center gap-1.5 font-mono text-[11px] text-fg-subtle">
			{shot.durationSeconds.toFixed(1)} s
			{#if !shot.timingQualified}
				<span
					class="inline-flex items-center rounded border border-line-strong px-[5px] py-px font-mono text-[9.5px] uppercase tracking-[0.04em] text-fg-subtle"
					title="Motion latents unknown -- showing the requested length, not the generator's real output"
				>
					requested
				</span>
			{/if}
			{#if shot.hasIcLora}
				<span class="inline-flex items-center rounded border border-line-strong px-[5px] py-px font-mono text-[9.5px] uppercase tracking-[0.04em] text-fg-subtle">
					IC-LoRA
				</span>
			{/if}
			<span class="text-fg-disabled"> · at {shot.startSeconds.toFixed(1)} s</span>
		</span>
		{#if shot.run?.kind === 'failed' && shot.run.message}
			<span class="w-full truncate text-left text-xs text-danger" data-run-message>{shot.run.message}</span>
			{#if retryNote}<span class="w-full text-left text-xs text-fg-muted" data-retry-note>{retryNote}</span>{/if}
		{/if}
	</div>

	<span class="inline-flex flex-none items-center gap-[5px] font-mono text-[10px] uppercase tracking-[0.04em] {BADGE_TONE_CLASS[meta.tone]}">
		<ConsoleIcon name={meta.icon} class="h-[11px] w-[11px]" />
		{shot.badge === 'independent'
			? 'Independent'
			: shot.badge === 'needs-previous'
				? 'Needs previous shot'
				: shot.badge === 'input-ready'
					? 'Input ready'
					: shot.badge === 'stale'
						? 'Stale dependency'
						: shot.badge === 'unverified'
							? 'Dependency unverified'
							: 'Part of continuous render'}
	</span>

	{#if checked}
		<span class="flex-none font-mono text-[10px] uppercase tracking-[0.04em] text-fg-muted">Selected</span>
	{/if}

	{#if shot.run}
		<span
			class="flex flex-none items-center gap-2 font-mono text-xs tabular-nums {shot.run.kind === 'queued'
				? 'text-fg-subtle'
				: shot.run.kind === 'generating'
					? 'text-signal'
					: shot.run.kind === 'done'
						? 'text-success'
						: 'text-danger'}"
			data-run-state={shot.run.kind}
		>
			{runLabel()}
			{#if shot.run.kind === 'failed'}
				<button
					type="button"
					class="cursor-pointer rounded border border-line-strong bg-surface-2 px-2 py-0.5 font-sans text-xs font-medium text-fg hover:border-line-hover hover:bg-surface-3"
					onclick={(e) => {
						e.stopPropagation();
						onRetry?.(shot.id);
					}}
				>
					{retryLabel}
				</button>
			{/if}
		</span>
	{/if}

	<span class="flex h-[26px] w-[26px] flex-none items-center justify-center rounded text-fg-subtle">
		<ConsoleIcon name="chevron-down" class="h-3.5 w-3.5" />
	</span>

	{#if shot.run?.kind === 'generating'}
		<div class="absolute inset-x-px bottom-0 h-[3px] overflow-hidden rounded-b-[5px] bg-surface-3">
			<span class="block h-full bg-signal" style="width:{shot.run.percent}%"></span>
		</div>
	{/if}
</div>

<style>
	@container video-director (max-width: 30rem) {
		.row-thumb {
			width: 5rem;
			height: 45px;
		}
	}
</style>
