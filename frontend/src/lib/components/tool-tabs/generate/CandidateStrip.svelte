<script lang="ts">
	import { Button, IconButton } from '$lib/components/ui';
	import type { GenerateResult } from '$lib/tool-tabs/image/generate/generateStep';

	let {
		results,
		chosen,
		phone = false,
		dock = false,
		canRetry = true,
		onchoose,
		onagain
	}: {
		results: readonly GenerateResult[];
		chosen: number;
		phone?: boolean;
		dock?: boolean;
		canRetry?: boolean;
		onchoose: (index: number) => void;
		onagain: () => void;
	} = $props();

	function keydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		if (target && (target.closest('textarea, input, [contenteditable="true"]') || event.ctrlKey || event.metaKey || event.altKey)) return;
		const index = Number(event.key) - 1;
		if (Number.isInteger(index) && index >= 0 && index < results.length) {
			event.preventDefault();
			onchoose(index);
		}
	}
</script>

<svelte:window onkeydown={keydown} />

<div class="pointer-events-auto flex items-center gap-2 {dock ? 'gap-3 text-fg' : `rounded-xl border border-scrim-on/10 bg-scrim/70 text-scrim-on backdrop-blur ${phone ? 'p-1.5' : 'p-2'}`}" role="group" aria-label="Results" data-candidate-strip>
	{#each results as result, index (result.patch.item_id)}
		<button
			type="button"
			class="relative flex-none overflow-hidden rounded transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal {dock ? 'h-9 w-9' : phone ? 'h-12 w-12' : 'h-14 w-14'} {index === chosen
				? 'shadow-[0_0_0_2px_rgb(var(--signal))]'
				: dock
					? 'shadow-[0_0_0_1px_rgb(var(--line-strong))] hover:shadow-[0_0_0_1px_rgb(var(--line-hover))]'
					: 'shadow-[0_0_0_1px_rgb(var(--on-scrim)/0.12)] hover:shadow-[0_0_0_1px_rgb(var(--on-scrim)/0.4)]'}"
			aria-pressed={index === chosen}
			aria-label="Result {index + 1}"
			onclick={() => onchoose(index)}
			data-candidate={index}
		>
			<img src={result.url} alt="" class="alpha-checker h-full w-full object-cover" draggable="false" />
			<span class="absolute bottom-[3px] left-[3px] rounded-sm px-[5px] font-mono text-sm leading-4 tabular-nums {index === chosen ? 'bg-signal-solid text-white' : 'bg-scrim/70 text-scrim-on'}">{index + 1}</span>
		</button>
	{/each}
	{#if dock}
		<Button variant="ghost" size="sm" icon="refresh" disabled={!canRetry} onclick={onagain} attrs={{ 'data-candidate-again': '' }}>Try again</Button>
	{:else}
	<span class="mx-0.5 h-8 w-px {dock ? 'bg-line-strong' : 'bg-scrim-on/15'}"></span>
	<IconButton icon="refresh" label="Try again" size="sm" variant="ghost" class={dock ? '' : 'text-scrim-on hover:bg-scrim-on/15'} disabled={!canRetry} onclick={onagain} data-candidate-again />
	{/if}
</div>
