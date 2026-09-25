<script lang="ts">
	// `.shot-card` -- the expanded, active shot. Card head carries every fact
	// (no per-shot Generate, no info band, per note09-decisions.md); the body
	//
	import type { Snippet } from 'svelte';
	import type { ConsoleShot } from './consoleModel';
	import { badgeMeta, BADGE_TONE_CLASS } from './badgeMeta';
	import ConsoleIcon from './ConsoleIcon.svelte';
	import { Button } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';

	let {
		shot,
		checked,
		maxDurationSeconds,
		onToggleChecked,
		onDuplicate,
		onRemove,
		onRetry,
		onDuration,
		onFrames,
		onSetMax,
		onCollapse,
		children
	}: {
		shot: ConsoleShot;
		checked: boolean;
		/** `resolveShotDurationMax(doc, caps)` -- null hides the MAX button entirely (no cap of any kind). */
		maxDurationSeconds: number | null;
		onToggleChecked: (shotId: string) => void;
		onDuplicate: (shotId: string) => void;
		onRemove: (shotId: string) => void;
		/** Resubmits just this shot (W3) -- only ever rendered for a 'failed' run. */
		onRetry?: (shotId: string) => void;
		onDuration: (shotId: string, seconds: number) => void;
		onFrames: (shotId: string, frames: number) => void;
		onCollapse: (shotId: string) => void;
		onSetMax: (shotId: string) => void;
		children?: Snippet;
	} = $props();

	let meta = $derived(badgeMeta(shot.badge));
	// Wan only, and only when this rail had no timing profile to compute the
	// real emitted geometry from -- `frames`/`newFrames` are then a raw
	// `duration * fps` approximation, not the generator's real output. Every
	// other family is always `timingQualified: true` (see `ConsoleShot`'s
	// own doc comment), so this never renders for them.
	let timingUnknown = $derived(!shot.timingQualified);
	let atMax = $derived(maxDurationSeconds != null && Math.abs(shot.durationSeconds - maxDurationSeconds) < 0.05);
	let maxFrames = $derived(maxDurationSeconds != null ? Math.round(maxDurationSeconds * shot.fps) : 0);

	let menuOpen = $state(false);
	let menuRoot: HTMLDivElement | undefined = $state();

	function handleWindowClick(event: MouseEvent) {
		if (menuOpen && menuRoot && !menuRoot.contains(event.target as Node)) menuOpen = false;
	}

	function commitDuration(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const seconds = parseFloat(input.value);
		if (!Number.isFinite(seconds) || seconds <= 0) {
			input.value = shot.durationSeconds.toFixed(1);
			return;
		}
		onDuration(shot.id, seconds);
	}
	function handleDurationKeydown(e: KeyboardEvent) {
		const input = e.currentTarget as HTMLInputElement;
		if (e.key === 'Enter') {
			input.blur();
		} else if (e.key === 'Escape') {
			input.value = shot.durationSeconds.toFixed(1);
			input.blur();
		}
	}

	function commitFrames(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const frames = parseFloat(input.value);
		if (!Number.isFinite(frames) || frames <= 0) {
			input.value = String(shot.frames);
			return;
		}
		onFrames(shot.id, Math.round(frames));
	}
	function handleFramesKeydown(e: KeyboardEvent) {
		const input = e.currentTarget as HTMLInputElement;
		if (e.key === 'Enter') {
			input.blur();
		} else if (e.key === 'Escape') {
			input.value = String(shot.frames);
			input.blur();
		}
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

<div class="rounded-md border border-line bg-surface-1">
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="flex cursor-pointer flex-wrap items-center gap-2.5 border-b border-line px-3.5 py-2.5"
		role="button"
		tabindex="0"
		aria-label="Collapse {shot.title}"
		onclick={() => onCollapse(shot.id)}
		onkeydown={(e) => {
			if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
				e.preventDefault();
				onCollapse(shot.id);
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

		<span class="font-mono text-[11px] text-fg">{shot.number}</span>
		<span class="max-w-[220px] truncate text-[13px] font-semibold text-fg" title={shot.title}>{shot.title}</span>

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
							: shot.badge === 'unverified'
								? 'Dependency unverified'
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
						onclick={(e) => {
							e.stopPropagation();
							onRetry?.(shot.id);
						}}
					>
						Retry
					</button>
				{/if}
			</span>
		{/if}

		<div class="flex flex-1 items-center justify-end gap-3">
			<div class="flex items-center gap-1 font-mono text-xs text-fg-subtle">
				<input
					type="text"
					inputmode="decimal"
					class="h-6 w-14 rounded border border-line-strong bg-surface-2 px-1.5 text-right font-mono text-xs tabular-nums text-fg focus:outline-none focus:ring-1 focus:ring-signal"
					value={shot.durationSeconds.toFixed(1)}
					aria-label="Shot duration in seconds"
					onclick={(e) => e.stopPropagation()}
					onchange={commitDuration}
					onkeydown={handleDurationKeydown}
				/>
				<span>s</span>
				{#if maxDurationSeconds != null}
					<Tooltip text={`Set to the maximum this generator allows (${maxFrames} frames)`} position="top">
						<Button
							variant="secondary"
							size="sm"
							disabled={atMax}
							onclick={(e) => {
								e.stopPropagation();
								onSetMax(shot.id);
							}}
						>
							Max
						</Button>
					</Tooltip>
				{/if}
				<span>·</span>
				<input
					type="text"
					inputmode="numeric"
					class="h-6 w-12 rounded border border-line-strong bg-surface-2 px-1.5 text-right font-mono text-xs tabular-nums text-fg focus:outline-none focus:ring-1 focus:ring-signal"
					value={shot.frames}
					aria-label="Shot frame count"
					onclick={(e) => e.stopPropagation()}
					onchange={commitFrames}
					onkeydown={handleFramesKeydown}
				/>
				<span>{shot.capFrames != null ? `/ ${shot.capFrames} frames` : 'frames'}</span>
				{#if shot.newFrames != null}
					<span class="text-xs uppercase tracking-[0.04em]">+{shot.newFrames} new</span>
				{/if}
				{#if timingUnknown}
					<span
						class="text-xs uppercase tracking-[0.04em]"
						title="Motion latents unknown -- showing the requested length, not the generator's real output"
					>
						requested
					</span>
				{/if}
			</div>

			<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
			<div class="relative" bind:this={menuRoot} onclick={(e) => e.stopPropagation()}>
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
	</div>

	<div class="flex flex-col rounded-b-md bg-canvas">
		{@render children?.()}
	</div>
</div>
