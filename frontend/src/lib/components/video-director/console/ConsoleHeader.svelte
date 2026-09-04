<script lang="ts">
	// `.dh-row` + `.cap-strip` from console.html. Pixel geometry and copy come
	// from the template literally; only the semantic tokens back the colors.
	//
	// Maintainer ruling: no preset/model chip and no "Generate film" button in
	// the console -- the page's own Generate control outside the Director is
	// the only way to generate a film. Header is exactly: title · `n shots ·
	// t s` · readiness · capability strip; the right-side actions render ONLY
	// while shots are checked (Clear · `queued n` · disabled "Generate n
	// selected" -- per-shot generation is W3, the button never fires yet,
	// `queuedCount` stays 0 until W3 wires a real run queue). Nothing checked
	// -> no actions at all, not even a disabled one.
	import type { ConsoleHeader as ConsoleHeaderModel } from './consoleModel';
	import ConsoleIcon, { type ConsoleIconName } from './ConsoleIcon.svelte';

	let {
		header,
		checkedCount,
		queuedCount = 0,
		onGenerateSelected,
		onClearChecked
	}: {
		header: ConsoleHeaderModel;
		checkedCount: number;
		queuedCount?: number;
		onGenerateSelected: () => void;
		onClearChecked: () => void;
	} = $props();
</script>

<div class="flex flex-wrap items-center gap-2.5">
	<span class="text-sm font-semibold text-fg" data-testid="director-console-title">{header.title}</span>

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

	<div class="flex-1"></div>

	{#if checkedCount > 0}
		<div class="flex items-center gap-2.5">
			<button
				type="button"
				class="border-none bg-none p-0 text-xs text-fg-subtle underline decoration-line-strong hover:text-fg"
				onclick={onClearChecked}
			>
				Clear
			</button>
			<span class="whitespace-nowrap font-mono text-[10.5px] tabular-nums text-fg-subtle">queued {queuedCount}</span>
			<button
				type="button"
				class="inline-flex h-[27px] items-center justify-center gap-1.5 rounded bg-accent px-3 text-xs font-semibold text-accent-contrast disabled:cursor-not-allowed disabled:opacity-40"
				disabled
				title="Per-shot generation lands in the next wave"
				onclick={onGenerateSelected}
			>
				Generate {checkedCount} selected
			</button>
		</div>
	{/if}
</div>
