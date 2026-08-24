<script lang="ts">
	import {
		normalizeVariableDef,
		type ChoiceVariableMode,
		type VariableDef,
		type StoredVariableDef,
		type VariableRoll
	} from '$lib/utils/variableDefs';
	import Icon from './Icon.svelte';
	import InlinePopoverChip from './InlinePopoverChip.svelte';
	import { chipIndicatorColorForName } from './chipIndicatorColors';

	// Inline chip view over a `${name}` variable USAGE. Same
	// view-over-text discipline as ChoiceGroupChip: `raw` (the literal
	// `${name}` source text) is the only thing this chip renders FROM, and
	// removing/editing never touches anything but that exact span — the chip
	// never invents or mints an id, it just displays `name`.
	//
	// Unlike a choice-group chip, this one doesn't own any data to edit
	// in-place: a variable's value lives on the tab (one binding per name,
	// shared by every `${name}` anywhere in the prompt — the wire format has
	// no way to say "this occurrence only"), so every mutation here
	// (mode/pin, create) is really an edit to that shared DEFINITION, bubbled
	// up via callback props rather than handled locally.
	//
	// A choice variable has three modes, reconciling with how a phrasebook
	// chip's shuffle already behaves (chipParser.ts / richTextUtils.ts
	// `regenerateAutoChips`) —
	//   - shuffle (default): rolled once client-side at Generate; `roll`
	//     (passed in — RUN state, not part of the definition, see
	//     Tab.variableRolls) is what the chip displays, "$name · value",
	//     mirroring InlineChip.svelte's dot + picked-label styling for an
	//     phrasebook chip's own shuffle pick.
	//   - pin: always one specific option.
	//   - per-image: the advanced escape hatch — the backend rolls
	//     independently per image, so the chip can't show a single value; it
	//     shows a hint instead (see the rendered-prompt artifact card for what
	//     actually rolled).

	export let name: string;
	/** The variable's raw stored def (or legacy bare string), or `undefined`
	 *  when `name` has no entry at all — the warning state. */
	export let definition: StoredVariableDef | undefined = undefined;
	/** This tab's last roll for this variable (shuffle mode only) — run state,
	 *  read fresh each render, never mutated here. */
	export let roll: VariableRoll | undefined = undefined;
	export let disabled: boolean = false;
	export let onModeChange: ((mode: ChoiceVariableMode, pinnedIndex: number | null) => void) | undefined = undefined;
	export let onCreate: (() => void) | undefined = undefined;
	export let onOpenManager: (() => void) | undefined = undefined;
	export let onRemove: (() => void) | undefined = undefined;

	let open = false;

	let def: VariableDef | null;
	$: def = definition !== undefined ? normalizeVariableDef(definition) : null;
	$: isUndefined = def === null;
	$: isShuffleWithRoll = def?.type === 'choice' && def.mode === 'shuffle' && !!roll;
	$: isPerImage = def?.type === 'choice' && def.mode === 'per-image';

	// A stable-per-name color, the same visual language InlineChip.svelte uses
	// for a phrasebook chip's picked value (colored dot + label).
	$: indicatorColor = chipIndicatorColorForName(name);

	function handleCreate() {
		onCreate?.();
		open = false;
	}

	function handleModeSelectChange(raw: string) {
		if (!def || def.type !== 'choice') return;
		const mode = raw as ChoiceVariableMode;
		// Mirrors VariableManagerModal's setMode: switching into pin mode with
		// nothing pinned yet defaults to the first option.
		const pinnedIndex = mode === 'pin' ? (def.pinnedIndex ?? 0) : def.pinnedIndex;
		onModeChange?.(mode, pinnedIndex);
	}

	function handlePinnedIndexChange(raw: string) {
		if (!def || def.type !== 'choice') return;
		onModeChange?.('pin', parseInt(raw, 10));
	}

	function handleOpenManager() {
		onOpenManager?.();
		open = false;
	}
</script>

<InlinePopoverChip
	tone={isUndefined ? 'warning' : 'accent'}
	density="tight"
	{disabled}
	onremove={onRemove}
	bind:open
	class="variable-usage-chip"
	removeTitle="Remove this usage"
	popoverLabel={`Variable ${name}`}
>
	{#snippet label()}
		{#if isUndefined}
			<Icon name="warning" className="w-3 h-3 text-warning flex-shrink-0" />
		{/if}
		<span class="text-sm font-mono {isUndefined ? 'text-warning' : 'text-fg-subtle'}">$</span>
		<span class="text-sm font-mono whitespace-nowrap max-w-[10rem] truncate {isUndefined ? 'text-warning' : 'text-fg'}">{name}</span>

		{#if isShuffleWithRoll && roll}
			<span class="text-fg-subtle">&middot;</span>
			<span class="h-2 w-2 rounded-full {indicatorColor} flex-shrink-0"></span>
			<span class="text-sm font-medium text-fg whitespace-nowrap max-w-[8rem] truncate">{roll.value}</span>
		{:else if isPerImage}
			<Icon name="layers" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
		{/if}
	{/snippet}

	{#snippet popover()}
		{#if isUndefined}
			<p class="text-xs text-fg-muted">
				<span class="font-mono text-warning">${'{'}{name}{'}'}</span> isn't defined — it will expand to nothing.
			</p>
			<button
				type="button"
				class="mt-2 inline-flex items-center gap-1 rounded bg-surface-2 px-2 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-surface-3"
				on:click={handleCreate}
			>
				<Icon name="plus" className="h-3.5 w-3.5" />
				Create this variable
			</button>
		{:else if def}
			<div class="flex items-center justify-between gap-2">
				<span class="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.06em] text-fg-muted">
					{def.type === 'choice' ? 'Choice' : 'Text'}
				</span>
			</div>

			{#if def.type === 'text'}
				<p class="mt-2 break-words text-sm text-fg">
					{def.value || '(empty)'}
				</p>
			{:else}
				<ul class="mt-2 space-y-1">
					{#each def.options as option, index (index)}
						<li class="flex items-center gap-1.5 text-sm {def.mode === 'pin' && def.pinnedIndex === index ? 'text-fg font-medium' : 'text-fg-muted'}">
							{#if def.mode === 'pin' && def.pinnedIndex === index}
								<Icon name="check" className="h-3.5 w-3.5 flex-shrink-0 text-accent" />
							{:else if def.mode === 'shuffle' && roll?.optionIndex === index}
								<span class="h-2 w-2 flex-shrink-0 rounded-full {indicatorColor}"></span>
							{:else}
								<span class="h-3.5 w-3.5 flex-shrink-0"></span>
							{/if}
							<span class="truncate">{option || `Option ${index + 1}`}</span>
						</li>
					{/each}
				</ul>

				{#if def.mode === 'shuffle'}
					<p class="mt-1.5 text-2xs text-fg-subtle">
						{#if roll}
							Rolled <span class="text-fg">{roll.value}</span> for this generation. Re-rolls every time you click Generate.
						{:else}
							Rolls a new pick the next time you click Generate.
						{/if}
					</p>
				{:else if def.mode === 'per-image'}
					<p class="mt-1.5 text-2xs text-fg-subtle">
						Rolls independently for every image — check the result card to see what each image got.
					</p>
				{/if}

				<label class="mt-2 flex items-center gap-1.5 text-2xs text-fg-muted">
					<span class="flex-shrink-0 font-mono uppercase tracking-[0.06em]">Value</span>
					<select
						class="input min-w-0 flex-1 py-1 text-xs"
						value={def.mode}
						on:change={(e) => handleModeSelectChange(e.currentTarget.value)}
						aria-label={`How ${name} picks a value`}
					>
						<option value="shuffle">Shuffle (new pick each generation)</option>
						<option value="pin">Use one specific choice</option>
						<option value="per-image">Re-roll for every image (advanced)</option>
					</select>
				</label>

				{#if def.mode === 'pin'}
					<select
						class="input mt-1.5 w-full py-1 text-xs"
						value={def.pinnedIndex ?? 0}
						on:change={(e) => handlePinnedIndexChange(e.currentTarget.value)}
						aria-label={`Which option to use for ${name}`}
					>
						{#each def.options as option, index (index)}
							<option value={index}>{option || `Option ${index + 1}`}</option>
						{/each}
					</select>
				{/if}

				<p class="mt-1.5 text-2xs text-fg-subtle">Applies everywhere ${'{'}{name}{'}'} is used.</p>
			{/if}

			<button
				type="button"
				class="mt-2.5 inline-flex items-center gap-1 rounded px-1.5 py-1 text-2xs font-medium text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
				on:click={handleOpenManager}
			>
				<Icon name="braces" className="h-3.5 w-3.5" />
				Edit in Variables
			</button>
		{/if}
	{/snippet}
</InlinePopoverChip>
