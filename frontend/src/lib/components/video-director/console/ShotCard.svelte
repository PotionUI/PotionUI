<script lang="ts">
	// `.shot-card` -- the expanded, active shot. Card head carries every fact
	// (no per-shot Generate, no info band, per note09-decisions.md); the body
	// is entirely `children` -- lane (c)'s ShotRail and lane (d)'s ShotStage,
	// wired up by ShotConsole (lane a). The active-shot border is unconditional
	// (a card only renders here because it IS the active shot, so it's not a
	// prop) -- maintainer ruling (09-04): a subtle 1px signal-tinted border,
	// no glow -- the earlier full-strength `border-signal` + shadow ring read
	// as too loud/blue for what is just "which shot is expanded".
	import type { Snippet } from 'svelte';
	import type { ConsoleShot } from './consoleModel';
	import { badgeMeta, BADGE_TONE_CLASS } from './badgeMeta';
	import ConsoleIcon from './ConsoleIcon.svelte';

	let {
		shot,
		checked,
		onToggleChecked,
		onDuplicate,
		onRemove,
		onRetry,
		children
	}: {
		shot: ConsoleShot;
		checked: boolean;
		onToggleChecked: (shotId: string) => void;
		onDuplicate: (shotId: string) => void;
		onRemove: (shotId: string) => void;
		/** Resubmits just this shot (W3) -- only ever rendered for a 'failed' run. */
		onRetry?: (shotId: string) => void;
		children?: Snippet;
	} = $props();

	let meta = $derived(badgeMeta(shot.badge));
	let framesBold = $derived(shot.capFrames != null ? `${shot.frames} / ${shot.capFrames}` : `${shot.frames}`);
	let framesRest = $derived.by(() => {
		let s = ` frames · ${shot.fps} fps`;
		if (shot.newFrames != null) s += ` · ${shot.newFrames} new`;
		if (shot.fpsLocked) s += ' fixed';
		return s;
	});

	let menuOpen = $state(false);
	let menuRoot: HTMLDivElement | undefined = $state();

	function handleWindowClick(event: MouseEvent) {
		if (menuOpen && menuRoot && !menuRoot.contains(event.target as Node)) menuOpen = false;
	}

	function runLabel(): string {
		const run = shot.run;
		if (!run) return '';
		if (run.kind === 'queued') return 'Queued';
		if (run.kind === 'generating') return `Generating ${run.percent}%`;
		if (run.kind === 'done') return `Done · ${run.time}`;
		return 'Failed';
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div class="rounded-md border bg-surface-1" style="border-color: rgb(var(--signal) / 0.35)">
	<div class="flex flex-wrap items-center gap-2.5 border-b border-line px-3.5 py-2.5">
		<button
			type="button"
			class="flex h-4 w-4 flex-none items-center justify-center rounded-[3px] border {checked
				? 'border-fg-muted bg-surface-3'
				: 'border-line-strong bg-surface-2'}"
			aria-label={checked ? 'Selected for generation' : 'Select for generation'}
			onclick={() => onToggleChecked(shot.id)}
		>
			{#if checked}
				<ConsoleIcon name="check" class="h-2.5 w-2.5 text-fg" />
			{/if}
		</button>

		<span class="font-mono text-[11px] text-fg">{shot.number}</span>
		<span class="max-w-[220px] truncate text-[13px] font-semibold text-fg" title={shot.title}>{shot.title}</span>
		<span class="font-mono text-[11px] text-fg-subtle">{shot.durationSeconds.toFixed(1)} s</span>
		<span class="font-mono text-[11px] text-fg-subtle"><b class="font-normal text-fg-muted">{framesBold}</b>{framesRest}</span>

		<span class="inline-flex items-center gap-[5px] font-mono text-[10px] uppercase tracking-[0.04em] {BADGE_TONE_CLASS[meta.tone]}">
			<ConsoleIcon name={meta.icon} class="h-[11px] w-[11px]" />
			{shot.badge === 'independent'
				? 'Independent'
				: shot.badge === 'needs-previous'
					? 'Needs previous shot'
					: shot.badge === 'input-ready'
						? 'Input ready'
						: shot.badge === 'stale'
							? 'Stale dependency'
							: 'Part of continuous render'}
		</span>

		{#if checked}
			<span class="font-mono text-[10px] uppercase tracking-[0.04em] text-fg-muted">Selected</span>
		{/if}

		{#if shot.run}
			<span
				class="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.04em] {shot.run.kind === 'queued'
					? 'text-fg-subtle'
					: shot.run.kind === 'generating'
						? 'text-signal'
						: shot.run.kind === 'done'
							? 'text-success'
							: 'text-danger'}"
			>
				{runLabel()}
				{#if shot.run.kind === 'failed'}
					<button
						type="button"
						class="cursor-pointer border-none bg-none p-0 font-mono text-[10px] normal-case text-fg-muted underline"
						onclick={() => onRetry?.(shot.id)}
					>
						Retry
					</button>
				{/if}
			</span>
		{/if}

		<div class="flex-1"></div>

		<div class="relative" bind:this={menuRoot}>
			<button
				type="button"
				class="flex h-[26px] w-[26px] items-center justify-center rounded text-fg-subtle hover:bg-surface-2 hover:text-fg"
				aria-haspopup="menu"
				aria-expanded={menuOpen}
				aria-label="Shot actions"
				onclick={() => (menuOpen = !menuOpen)}
			>
				<ConsoleIcon name="more" class="h-3.5 w-3.5" />
			</button>
			{#if menuOpen}
				<div
					class="absolute right-0 top-full z-10 mt-1 w-40 rounded-lg border border-line-strong bg-surface-2 py-1 shadow-floating"
					role="menu"
				>
					<button
						type="button"
						class="block w-full px-3 py-1.5 text-left text-xs {shot.canDuplicate
							? 'text-fg hover:bg-surface-3'
							: 'cursor-not-allowed text-fg-disabled'}"
						disabled={!shot.canDuplicate}
						role="menuitem"
						onclick={() => {
							menuOpen = false;
							onDuplicate(shot.id);
						}}
					>
						Duplicate
					</button>
					<button
						type="button"
						class="block w-full px-3 py-1.5 text-left text-xs {shot.canRemove
							? 'text-danger hover:bg-surface-3'
							: 'cursor-not-allowed text-fg-disabled'}"
						disabled={!shot.canRemove}
						role="menuitem"
						onclick={() => {
							menuOpen = false;
							onRemove(shot.id);
						}}
					>
						Remove
					</button>
				</div>
			{/if}
		</div>
	</div>

	<div class="flex flex-col gap-3.5 p-3.5">
		{@render children?.()}
	</div>
</div>
