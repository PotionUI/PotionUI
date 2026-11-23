<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import {
		normalizeVariableDef,
		optionText,
		type ChoiceVariableMode,
		type VariableDef,
		type StoredVariableDef,
		type VariableRoll,
		type VariablesMap,
		type ConditionReaders,
		isVariableLinked,
		isWhenTarget,
		numberChipLabel,
		numberRangeLabel,
usageRollKey
	} from '$lib/utils/variableDefs';
	import { filterOptionIndices, showOptionFilter } from '$lib/utils/variableOptionList';
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
	export let rolls: Record<string, VariableRoll> = {};
	export let variables: VariablesMap = {};
	export let readers: ConditionReaders = {};
	export let usage: { ordinal: number; total: number } = { ordinal: 1, total: 1 };
	export let onLinkedChange: ((linked: boolean) => void) | undefined = undefined;
	export let disabled: boolean = false;
	export let onModeChange: ((mode: ChoiceVariableMode, pinnedIndex: number | null) => void) | undefined = undefined;
	export let onCreate: (() => void) | undefined = undefined;
	export let onOpenManager: (() => void) | undefined = undefined;
	export let onRemove: (() => void) | undefined = undefined;
	// See InlinePopoverChip's own doc comment — the segment composer's
	// `.chip.variable-chip` + `.variable-popover` anatomy from
	// prompt-segments-concept.html, additive alongside the default look.
	export let variant: 'default' | 'segment-composer' = 'default';

	export let startOpen: boolean = false;
	export let onOpenChange: ((open: boolean) => void) | undefined = undefined;

	let open = startOpen;
	$: onOpenChange?.(open);

	let roll: VariableRoll | undefined;
	let filterQuery = '';
	let expanded: Record<number, boolean> = {};

	let def: VariableDef | null;
	$: def = definition !== undefined ? normalizeVariableDef(definition) : null;
	$: linked = !!def && def.type !== 'text' && isVariableLinked(name, variables, readers);
	$: whenTarget = !!def && def.type !== 'text' && isWhenTarget(name, variables);
	$: multiUsage = usage.total > 1;
	$: roll = rolls[usageRollKey(name, linked ? 1 : usage.ordinal)];
	$: optionTexts = def?.type === 'choice' ? def.options.map((o) => optionText(o)) : [];
	$: visibleIndices = filterOptionIndices(optionTexts, filterQuery);
	$: usagesLockReason = whenTarget
		? 'Other options depend on this variable, so every usage shares one roll.'
		: def && def.type !== 'text' && def.mode !== 'shuffle'
			? 'Pin and Per image apply to every usage.'
			: '';
	$: isUndefined = def === null;
	$: isShuffleWithRoll = def?.type === 'choice' && def.mode === 'shuffle' && !!roll;
	$: isEmptyRoll = def?.type === 'choice' && def.mode === 'shuffle' && !!roll && roll.value === '';
	$: isPerImage = def?.type === 'choice' && def.mode === 'per-image';
	$: numberLabel = def?.type === 'number' ? numberChipLabel(def) : '';

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

	function handleOptionClick(index: number) {
		expanded = { ...expanded, [index]: !expanded[index] };
		if (def?.type === 'choice' && def.mode === 'pin') onModeChange?.('pin', index);
	}

	function isCurrentOption(index: number): boolean {
		if (!def || def.type !== 'choice') return false;
		if (def.mode === 'pin') return def.pinnedIndex === index;
		return def.mode === 'shuffle' && roll?.optionIndex === index;
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
	{variant}
	kind="variable"
	tone={isUndefined || isEmptyRoll ? 'warning' : 'accent'}
	density="tight"
	{disabled}
	onremove={onRemove}
	bind:open
	class="variable-usage-chip"
	removeTitle="Remove this usage"
	popoverLabel={`Variable ${name}`}
	headerMark="$"
	headerTitle={name}
	headerSubtitle={def?.type === 'choice' ? 'Choice variable' : def?.type === 'number' ? 'Number variable' : def?.type === 'text' ? 'Text variable' : 'Undefined variable'}
>
	{#snippet label()}
		{#if isUndefined}
			<Icon name="warning" className="w-3 h-3 text-warning flex-shrink-0" />
		{/if}
		<span class="text-sm font-mono {isUndefined ? 'text-warning' : 'text-fg-subtle'}">$</span>
		<span class="text-sm font-mono whitespace-nowrap max-w-[10rem] truncate {isUndefined ? 'text-warning' : 'text-fg'}">{name}</span>

		{#if isEmptyRoll}
			<span class="text-fg-subtle">&middot;</span>
			<Icon name="warning" className="w-3 h-3 text-warning flex-shrink-0" />
			<span class="text-sm font-medium text-warning whitespace-nowrap">nothing</span>
		{:else if isShuffleWithRoll && roll}
			<span class="text-fg-subtle">&middot;</span>
			<span class="h-2 w-2 rounded-full {indicatorColor} flex-shrink-0"></span>
			<span class="text-sm font-medium text-fg whitespace-nowrap max-w-[8rem] truncate">{roll.value}</span>
		{:else if isPerImage}
			<Icon name="layers" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
		{:else if def?.type === 'number'}
			<span class="text-fg-subtle">&middot;</span>
			<span class="text-sm font-mono tabular-nums text-fg whitespace-nowrap" data-testid="number-chip-label">{numberLabel}</span>
			{#if def.mode === 'per-image'}
				<Icon name="layers" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
			{/if}
		{/if}
	{/snippet}

	{#snippet composerLabel()}
		{#if isUndefined}
			<Icon name="composer-info" className="icon" strokeWidth={1.7} />
		{/if}
		<span class="chip-context">{name}</span>
		{#if isEmptyRoll}
			<span class="chip-label">nothing</span>
		{:else if isShuffleWithRoll && roll}
			<span class="chip-label">{roll.value}</span>
		{:else if def?.type === 'text'}
			<span class="chip-label">{def.value || '(empty)'}</span>
		{:else if def?.type === 'number'}
			<span class="chip-label">{numberLabel}</span>
		{/if}
		{#if def?.type === 'choice' && def.mode !== 'shuffle'}
			<span class="chip-state">{def.mode === 'pin' ? 'Pinned' : 'Per image'}</span>
		{:else if def && def.type !== 'text' && multiUsage}
			<span class="chip-state">{linked ? `Linked ${usage.total}` : `${usage.ordinal}/${usage.total}`}</span>
		{/if}
	{/snippet}

	{#snippet popover()}
		{#if variant === 'segment-composer' && !isUndefined && def}
			{#if def.type === 'choice'}
				<div class="behavior">
					<div class="section-label">Value behavior</div>
					<div class="segmented">
						<button type="button" class="behavior-button" class:active={def.mode === 'shuffle'} on:click={() => handleModeSelectChange('shuffle')}>Shuffle</button>
						<button type="button" class="behavior-button" class:active={def.mode === 'pin'} on:click={() => handleModeSelectChange('pin')}>Pin</button>
						<button type="button" class="behavior-button" class:active={def.mode === 'per-image'} on:click={() => handleModeSelectChange('per-image')}>Per image</button>
					</div>
					<p class="helper">
						{#if def.mode === 'shuffle'}
							Rolls when Generate is clicked.
						{:else if def.mode === 'pin'}
							Always uses the pinned option, everywhere ${'{'}{name}{'}'} is used.
						{:else}
							Rolls independently for every image. Check the result card to see what each image got.
						{/if}
					</p>
				</div>
				{#if multiUsage || whenTarget}
					<div class="behavior">
						<div class="section-label">Usages · {usage.ordinal}/{usage.total}</div>
						<div class="segmented two">
							<button
								type="button"
								class="behavior-button"
								class:active={!linked}
								disabled={!!usagesLockReason}
								on:click={() => onLinkedChange?.(false)}>Independent</button
							>
							<button
								type="button"
								class="behavior-button"
								class:active={linked}
								disabled={!!usagesLockReason}
								on:click={() => onLinkedChange?.(true)}>Linked</button
							>
						</div>
						<p class="helper">
							{#if usagesLockReason}
								{usagesLockReason}
							{:else if linked}
								All {usage.total} usages share one roll.
							{:else}
								Each usage rolls its own value.
							{/if}
						</p>
					</div>
				{/if}
				<div class="behavior">
					<div class="section-label">
						{def.mode === 'pin' ? 'Pinned option' : 'Options'} · {def.options.length}
					</div>
					{#if showOptionFilter(def.options.length)}
						<label class="option-filter">
							<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0" />
							<input type="text" placeholder="Filter options" bind:value={filterQuery} aria-label={`Filter options of ${name}`} />
							{#if filterQuery.trim()}
								<span class="count">{visibleIndices.length} of {def.options.length}</span>
							{/if}
						</label>
					{/if}
					<div class="option-list" role={def.mode === 'pin' ? 'radiogroup' : 'list'} aria-label={`Options of ${name}`}>
						{#each visibleIndices as index (index)}
							<button
								type="button"
								class="option-row"
								class:current={isCurrentOption(index)}
								class:expanded={expanded[index]}
								role={def.mode === 'pin' ? 'radio' : undefined}
								aria-checked={def.mode === 'pin' ? isCurrentOption(index) : undefined}
								on:click={() => handleOptionClick(index)}
							>
								{#if def.mode === 'pin'}
									<span class="option-radio"></span>
								{:else}
									<span class="option-index">{index + 1}</span>
								{/if}
								<span class="option-text">{optionTexts[index] || `Option ${index + 1}`}</span>
								<span class="option-tag">{isCurrentOption(index) ? (def.mode === 'pin' ? 'pinned' : 'rolled') : ''}</span>
							</button>
						{:else}
							<div class="option-empty">No options match.</div>
						{/each}
					</div>
				</div>
			{:else if def.type === 'number'}
				<div class="section-label">Value</div>
				<p class="helper" data-testid="number-popover-note">
					{#if def.mode === 'pin'}
						Pinned to {numberChipLabel(def)}, the same on every image.
					{:else if def.mode === 'per-image'}
						Rolls a number from {numberRangeLabel(def)} for every image.
					{:else}
						Rolls a number from {numberRangeLabel(def)} when Generate is clicked.
					{/if}
				</p>
			{:else}
				<div class="section-label">Value</div>
				<p class="helper">{def.value || '(empty)'}</p>
			{/if}
		{:else if isUndefined}
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
					{def.type === 'choice' ? 'Choice' : def.type === 'number' ? 'Number' : 'Text'}
				</span>
			</div>

			{#if def.type === 'text'}
				<p class="mt-2 break-words text-sm text-fg">
					{def.value || '(empty)'}
				</p>
			{:else if def.type === 'number'}
				<p class="mt-2 text-sm text-fg" data-testid="number-popover-note">
					{#if def.mode === 'pin'}
						Pinned to <span class="font-mono tabular-nums">{numberChipLabel(def)}</span>
					{:else}
						<span class="font-mono tabular-nums">{numberRangeLabel(def)}</span>
						&middot; {def.mode === 'per-image' ? 'rolls per image' : 'rolls each generation'}
					{/if}
				</p>
				<p class="mt-1.5 text-2xs text-fg-subtle">Change the range or mode in Variables.</p>
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
							<span class="truncate">{optionText(option) || `Option ${index + 1}`}</span>
						</li>
					{/each}
				</ul>

				{#if def.mode === 'shuffle'}
					<p class="mt-1.5 text-2xs {isEmptyRoll ? 'text-warning' : 'text-fg-subtle'}">
						{#if isEmptyRoll && roll?.because}
							No option is eligible for <span class="font-mono">${roll.because.var} = {roll.because.value}</span>.
						{:else if isEmptyRoll}
							No option is eligible and none is marked Always — it will expand to nothing.
						{:else if roll}
							Rolled <span class="text-fg">{roll.value}</span> for this generation. Re-rolls every time you click Generate.
							{#if roll.because}
								<br />because <span class="font-mono text-fg">${roll.because.var} = {roll.because.value}</span>
							{/if}
							{#if roll.eligible !== undefined && roll.total !== undefined}
								<span class="text-fg-subtle"> · {roll.eligible} of {roll.total} eligible</span>
							{/if}
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
							<option value={index}>{optionText(option) || `Option ${index + 1}`}</option>
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

	{#snippet footer()}
		{#if variant === 'segment-composer'}
			<button type="button" class="small-button danger" use:tooltip={'Remove this usage'} on:click={() => onRemove?.()}
				><Icon name="composer-trash" className="icon" strokeWidth={1.7} />Remove usage</button
			>
			{#if !isUndefined && def}
				<button type="button" class="small-button" on:click={handleOpenManager}>
					<Icon name="composer-braces" className="icon" strokeWidth={1.7} />
					Edit in Variables…
				</button>
			{/if}
		{/if}
	{/snippet}
</InlinePopoverChip>
