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

	let open = false;

	$: indicatorColor = chipIndicatorColorAt(colorIndex);

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
	tone="signal"
	density="default"
	{disabled}
	{onremove}
	bind:open
	canOpen={!!spec}
	class="choice-group-chip"
	removeTitle="Remove group"
	popoverLabel="Edit choice group"
>
	{#snippet label()}
		<span class="w-2.5 h-2.5 rounded-full {indicatorColor} flex-shrink-0"></span>
		<span class="text-sm font-mono whitespace-nowrap max-w-[16rem] truncate">{summary}</span>
		<Icon name="chevron-down" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
	{/snippet}

	{#snippet popover()}
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
	{/snippet}
</InlinePopoverChip>
