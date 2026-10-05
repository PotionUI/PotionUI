<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import { countMatches, promptAxisValues } from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		axis,
		tabId,
		onChange
	}: {
		candidate?: AxisCandidate;
		axis: CompareAxis | null;
		tabId: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	type Saved = { find?: string; replace?: string | null };
	const saved = (() => (axis?.values ?? []).map((entry) => entry.value as Saved))();

	let find = $state(saved[0]?.find ?? '');
	let replacements = $state<Array<string | null>>(
		saved.length > 0 ? saved.map((entry) => entry.replace ?? null) : [null, '']
	);

	let promptText = $derived.by(() => {
		const tab = $tabsStore.tabs.find((entry) => entry.id === tabId);
		if (!tab) return '';
		const segments = (tab.promptSegments ?? []).filter((segment) => segment.enabled !== false);
		return segments.length > 0 ? segments.map((segment) => segment.content ?? '').join('\n') : (tab.prompt ?? '');
	});

	let matches = $derived(countMatches(promptText, find));

	function emit() {
		if (!find) {
			onChange([]);
			return;
		}
		const named = replacements.filter((entry) => entry === null || entry.trim() !== '');
		onChange(promptAxisValues(find, named));
	}

	function setReplacement(index: number, value: string) {
		replacements = replacements.map((entry, i) => (i === index ? value : entry));
		emit();
	}

	function removeReplacement(index: number) {
		replacements = replacements.filter((_, i) => i !== index);
		emit();
	}

	function addReplacement() {
		replacements = [...replacements, ''];
	}

	onMount(() => {
		if (find) emit();
	});
</script>

<div class="space-y-3">
	<div class="space-y-1">
		<div class="flex items-center justify-between">
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Find</span>
			{#if find}
				<span class="font-mono text-xs tabular-nums {matches > 0 ? 'text-signal' : 'text-warning'}" data-testid="match-count">
					{matches} {matches === 1 ? 'match' : 'matches'}
				</span>
			{/if}
		</div>
		<input class="input font-mono" type="text" aria-label="Find" placeholder="warm light" bind:value={find} oninput={emit} />
	</div>
	<div class="space-y-1.5">
		<span class="block font-mono text-2xs uppercase tracking-wide text-fg-subtle">Replace with</span>
		{#each replacements as entry, index (index)}
			<div class="flex items-center gap-2">
				<span class="w-4 text-right font-mono text-xs tabular-nums text-fg-subtle">{index + 1}</span>
				{#if entry === null}
					<div class="input flex-1 text-fg-muted">(keep original)</div>
				{:else}
					<input
						class="input flex-1 font-mono"
						type="text"
						aria-label="Replacement {index + 1}"
						value={entry}
						oninput={(event) => setReplacement(index, event.currentTarget.value)}
					/>
				{/if}
				{#if entry !== null}
					<button type="button" class="p-1 text-fg-subtle hover:text-fg" aria-label="Remove replacement {index + 1}" onclick={() => removeReplacement(index)}>
						<Icon name="close" className="h-3.5 w-3.5" />
					</button>
				{:else}
					<span class="w-6"></span>
				{/if}
			</div>
		{/each}
		<button
			type="button"
			class="flex w-full items-center justify-center gap-1.5 rounded border border-dashed border-line px-2 py-1.5 text-sm text-fg-muted hover:border-line-hover hover:text-fg"
			onclick={addReplacement}
		>
			<Icon name="plus" className="h-3.5 w-3.5" />
			Add replacement
		</button>
	</div>
</div>
