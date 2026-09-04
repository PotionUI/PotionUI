<script lang="ts">
	// `.cap-strip` from console.html, WITHOUT a title of its own -- maintainer
	// ruling (09-04, "I don't want to have two headers"): VideoDirectorEditor.
	// svelte's own `<header>` carries the ONE "Video Director" title and mounts
	// this as its informational strip -- `n shots · t s` · readiness ·
	// capability chips -- with a spacer before the chips so a flex-1 wrapper
	// around this component pushes them to the header's right edge, next to
	// the Variables button. The console itself (ShotConsole.svelte) renders no
	// header row at all any more; it starts at the film rows. No Generate
	// control of any kind here either (per the separate "there is no such
	// thing as 'Generate n selected'" ruling -- the generation panel always
	// decides about the generation; the checked-shot count/Clear readout
	// lives in ShotConsole.svelte, above the shot stack, not here).
	import type { ConsoleHeader as ConsoleHeaderModel } from './consoleModel';
	import ConsoleIcon, { type ConsoleIconName } from './ConsoleIcon.svelte';

	let {
		header
	}: {
		header: ConsoleHeaderModel;
	} = $props();
</script>

<div class="flex w-full flex-wrap items-center gap-2.5">
	<span class="font-mono text-[11px] tabular-nums text-fg-subtle">
		{header.shotCount} shot{header.shotCount === 1 ? '' : 's'} · {header.totalSeconds.toFixed(1)} s
	</span>

	<span
		class="inline-flex items-center gap-[5px] font-mono text-[10px] uppercase tracking-[0.04em] {header
			.readiness.ok
			? 'text-success'
			: 'text-warning'}"
	>
		<span class="h-1.5 w-1.5 flex-none rounded-full {header.readiness.ok ? 'bg-success' : 'bg-warning'}"></span>
		{header.readiness.text}
	</span>

	<div class="flex-1"></div>

	<div class="flex flex-wrap items-center gap-1.5">
		{#each header.capChips as chip (chip.text)}
			<span
				class="inline-flex h-[22px] items-center gap-[5px] whitespace-nowrap rounded border border-line-strong bg-surface-2 px-2 font-mono text-[10px] uppercase tracking-[0.04em] text-fg-muted"
			>
				{#if chip.icon}
					<ConsoleIcon name={chip.icon as ConsoleIconName} class="h-[11px] w-[11px]" />
				{/if}
				{chip.text}
			</span>
		{/each}
	</div>
</div>
