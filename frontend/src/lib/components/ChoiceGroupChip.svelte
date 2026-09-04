<script lang="ts">
	import { parseGroupInner, serializeGroup, type ChoiceGroupSpec } from '$lib/utils/choiceGroups';
	import Icon from './Icon.svelte';
	import InlinePopoverChip from './InlinePopoverChip.svelte';
	import { chipIndicatorColorAt } from './chipIndicatorColors';

	// Inline chip view over a `{a|b|c}` dynamicprompts choice group. The chip
	// never owns the group's data — `raw` (the literal source
	// text, braces included) is the only source of truth, exactly the way an
	// phrasebook `#chip` is a view over `#category.path` text. Editing here
	// re-serializes straight back to `raw`-shaped text; `count`/`countMax`/
	// `separator` (the `N$$sep$$` prefix) are preserved untouched — this editor
	// only adds/removes/reweights options.

	export let raw: string;
	export let colorIndex: number = 0;
	export let disabled: boolean = false;
	export let onchange: ((newRaw: string) => void) | undefined = undefined;
	export let onremove: (() => void) | undefined = undefined;
	// See InlinePopoverChip's own doc comment — the segment composer's
	// `.chip.choice-chip` + `.choice-popover` anatomy from
	// prompt-segments-concept.html, additive alongside the default look.
	export let variant: 'default' | 'segment-composer' = 'default';

	let open = false;

	$: indicatorColor = chipIndicatorColorAt(colorIndex);
	$: summaryLine = spec ? spec.options.map((o) => o.text || '…').join(' · ') : raw;

	$: spec = parseGroupInner(raw.slice(1, -1));
	$: summary = spec ? spec.options.map((o) => o.text || '…').join(' | ') : raw;

	function applyOptions(next: ChoiceGroupSpec['options']) {
		if (!spec) return;
		const nextSpec: ChoiceGroupSpec = { ...spec, options: next };
		onchange?.(serializeGroup(nextSpec));
	}

	function updateOptionText(index: number, text: string) {
		if (!spec) return;
		applyOptions(spec.options.map((o, i) => (i === index ? { ...o, text } : o)));
	}

	function updateOptionWeight(index: number, weightStr: string) {
		if (!spec) return;
		const trimmed = weightStr.trim();
		const weight = trimmed === '' ? null : Number(trimmed);
		applyOptions(
			spec.options.map((o, i) => (i === index ? { ...o, weight: weight !== null && !Number.isNaN(weight) ? weight : null } : o))
		);
	}

	function addOption() {
		if (!spec) return;
		applyOptions([...spec.options, { text: '', weight: null }]);
	}

	function removeOption(index: number) {
		if (!spec) return;
		if (spec.options.length <= 1) {
			// Down to zero real options — remove the whole group instead of
			// leaving behind a degenerate {}.
			onremove?.();
			open = false;
			return;
		}
		applyOptions(spec.options.filter((_, i) => i !== index));
	}
</script>

<InlinePopoverChip
	{variant}
	kind="choice"
	tone="signal"
	density="default"
	{disabled}
	{onremove}
	bind:open
	canOpen={!!spec}
	class="choice-group-chip"
	removeTitle="Remove group"
	popoverLabel="Edit choice group"
	headerMark={'{ }'}
	headerTitle="Choice group"
	headerSubtitle={raw + ' · inline syntax'}
>
	{#snippet label()}
		<span class="w-2.5 h-2.5 rounded-full {indicatorColor} flex-shrink-0"></span>
		<span class="text-sm font-mono whitespace-nowrap max-w-[16rem] truncate">{summary}</span>
		<Icon name="chevron-down" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
	{/snippet}

	{#snippet composerLabel()}
		<span class="chip-mark">{'{ }'}</span>
		<span class="chip-label">{summaryLine}</span>
	{/snippet}

	{#snippet popover()}
		{#if variant === 'segment-composer'}
			<p class="section-label">Options · one is picked at random</p>
			<div class="choice-editor-list">
				{#each spec?.options ?? [] as option, index (index)}
					<div class="choice-edit-row">
						<input
							value={option.weight ?? ''}
							aria-label="Option weight"
							title="Weight (default 1)"
							on:input={(e) => updateOptionWeight(index, e.currentTarget.value)}
						/>
						<input
							value={option.text}
							aria-label="Option text"
							placeholder="option text"
							on:input={(e) => updateOptionText(index, e.currentTarget.value)}
						/>
						<button type="button" data-choice-remove title="Remove option" on:click={() => removeOption(index)}>
							<svg class="icon"><use href="#i-trash" /></svg>
						</button>
					</div>
				{/each}
			</div>
			<button type="button" class="small-button" style="margin-top: 8px" on:click={addOption}>
				<svg class="icon"><use href="#i-plus" /></svg>
				Add option
			</button>
			<p class="helper">Weights default to 1. The editor preserves advanced count and separator syntax when it already exists.</p>
		{:else}
			<p class="mb-2 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Options — one is picked at random</p>
			<div class="space-y-1.5">
				{#each spec?.options ?? [] as option, index (index)}
					<div class="flex items-center gap-1.5">
						<input
							type="text"
							class="input w-14 flex-shrink-0 py-1 text-center font-mono text-xs tabular-nums"
							placeholder="1"
							value={option.weight ?? ''}
							title="Weight (default 1)"
							on:input={(e) => updateOptionWeight(index, e.currentTarget.value)}
						/>
						<input
							type="text"
							class="input min-w-0 flex-1 py-1 text-sm"
							placeholder="option text"
							value={option.text}
							on:input={(e) => updateOptionText(index, e.currentTarget.value)}
						/>
						<button
							type="button"
							class="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-2 hover:text-danger"
							on:click={() => removeOption(index)}
							aria-label={`Remove option ${index + 1}`}
						>
							<Icon name="trash" className="h-3.5 w-3.5" />
						</button>
					</div>
				{/each}
			</div>
			<button
				type="button"
				class="mt-2 inline-flex items-center gap-1 rounded px-1.5 py-1 text-2xs font-medium text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
				on:click={addOption}
			>
				<Icon name="plus" className="h-3.5 w-3.5" />
				Add option
			</button>
		{/if}
	{/snippet}

	{#snippet footer()}
		{#if variant === 'segment-composer'}
			<button type="button" class="small-button danger" on:click={() => onremove?.()}>Remove group</button>
		{/if}
	{/snippet}
</InlinePopoverChip>
