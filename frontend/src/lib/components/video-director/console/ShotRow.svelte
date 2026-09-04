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

	let {
		shot,
		checked,
		onToggleChecked,
		onActivate
	}: {
		shot: ConsoleShot;
		checked: boolean;
		onToggleChecked: (shotId: string) => void;
		onActivate: (shotId: string) => void;
	} = $props();

	let meta = $derived(badgeMeta(shot.badge));

	function runLabel(): string {
		const run = shot.run;
		if (!run) return '';
		if (run.kind === 'queued') return 'Queued';
		if (run.kind === 'generating') return `Generating ${run.percent}%`;
		if (run.kind === 'done') return `Done · ${run.time}`;
		return 'Failed';
	}
</script>

<div class="relative flex items-center gap-3.5 rounded-md border border-line bg-surface-1 px-3 py-2.5">
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

	<button
		type="button"
		class="h-[72px] w-32 flex-none rounded-md bg-cover bg-center shadow-raised {shot.thumb.url
			? ''
			: 'flex items-center justify-center border border-dashed border-line-strong bg-canvas'}"
		style={shot.thumb.url ? `background-image:url(${JSON.stringify(shot.thumb.url)})` : ''}
		aria-label="Expand {shot.title}"
		onclick={() => onActivate(shot.id)}
	>
		{#if !shot.thumb.url}
			<ConsoleIcon name="image" class="h-[18px] w-[18px] text-fg-disabled" />
		{/if}
	</button>

	<span class="flex-none font-mono text-[11px] text-fg-subtle">{shot.number}</span>

	<button type="button" class="flex min-w-0 flex-1 flex-col items-start gap-1" onclick={() => onActivate(shot.id)}>
		<span class="w-full truncate text-left text-[13.5px] font-medium text-fg">{shot.title}</span>
		<span class="flex items-center gap-1.5 font-mono text-[11px] text-fg-subtle">
			{shot.durationSeconds.toFixed(1)} s
			{#if shot.hasIcLora}
				<span class="inline-flex items-center rounded border border-line-strong px-[5px] py-px font-mono text-[9.5px] uppercase tracking-[0.04em] text-fg-subtle">
					IC-LoRA
				</span>
			{/if}
			<span class="text-fg-disabled"> · at {shot.startSeconds.toFixed(1)} s</span>
		</span>
	</button>

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
						: 'Part of continuous render'}
	</span>

	{#if checked}
		<span class="flex-none font-mono text-[10px] uppercase tracking-[0.04em] text-fg-muted">Selected</span>
	{/if}

	{#if shot.run}
		<span
			class="flex flex-none items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.04em] {shot.run.kind === 'queued'
				? 'text-fg-subtle'
				: shot.run.kind === 'generating'
					? 'text-signal'
					: shot.run.kind === 'done'
						? 'text-success'
						: 'text-danger'}"
		>
			{runLabel()}
			{#if shot.run.kind === 'failed'}
				<span class="cursor-pointer text-fg-muted underline normal-case">Retry</span>
			{/if}
		</span>
	{/if}

	<button
		type="button"
		class="flex h-[26px] w-[26px] flex-none items-center justify-center rounded text-fg-subtle hover:bg-surface-2 hover:text-fg"
		aria-label="Expand {shot.title}"
		onclick={() => onActivate(shot.id)}
	>
		<ConsoleIcon name="chevron-down" class="h-3.5 w-3.5" />
	</button>

	{#if shot.run?.kind === 'generating'}
		<div class="absolute inset-x-px bottom-0 h-[3px] overflow-hidden rounded-b-[5px] bg-surface-3">
			<span class="block h-full bg-signal" style="width:{shot.run.percent}%"></span>
		</div>
	{/if}
</div>
