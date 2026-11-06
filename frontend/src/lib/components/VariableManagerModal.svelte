<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import SegmentModal from './modals/SegmentModal.svelte';
	import { Badge, Button } from '$lib/components/ui';
	import autosize from '$lib/actions/autosize';
	import Icon from './Icon.svelte';
	import Tooltip from './Tooltip.svelte';
	import VariableConditionChip from './VariableConditionChip.svelte';
	import NumberVariableEditor from './NumberVariableEditor.svelte';
	import PasteVariablesModal from './PasteVariablesModal.svelte';
	import { usableNamedRows, variableNameErrors } from '$lib/utils/variableNames';
	import { toasts } from '$lib/stores/toast';
	import { copyText } from '$lib/utils/clipboard';
	import { serializeVariables } from '$lib/utils/variablesTransfer';
	import { applyVariableEdits } from '$lib/utils/variableEdits';
	import {
		normalizeVariableDef,
		createTextVariable,
		createChoiceVariable,
		createNumberVariable,
		clampNumberValue,
		numberRangeLabel,
		optionText,
		optionWhen,
		dependencies,
		dependents,
		resolveVariableOrder,
		eligibleOptions,
		buildVariablesForSubmit,
		isVariableLinked,
		choiceLacksOption,
		usagesShareRoll,
		isWhenTarget,
		type ChoiceVariableMode,
		type ConditionReaders,
		type NumberVariableDef,
		type VariableDef,
		type VariableOption,
		type VariableOptionWhen,
		type VariablesMap
	} from '$lib/utils/variableDefs';

	// Manager for the tab's prompt variables. A variable has a TYPE: `text` (a
	// plain value) or `choice` (a managed list of options, edited as add/remove/edit
	// rows, never a raw `{a|b|c}` box). A choice variable shuffles (random option
	// per image) unless one option is pinned; either way it serializes to the same
	// `{a|b|c}` wire syntax a hand-typed group would (see utils/variableDefs.ts).
	//
	// Edits a local draft and commits the whole map back on every change — no
	// separate "save" step, matching the rest of the tab's live state.

	export let isOpen = false;
	export let variables: VariablesMap = {};
	export let usageCounts: Record<string, number> = {};
	export let readers: ConditionReaders = {};

	const dispatch = createEventDispatcher<{ close: void; change: VariablesMap }>();

	interface Row {
		key: string; // stable local key so bindings don't jump around as `name` is edited
		name: string;
		def: VariableDef;
	}

	const MODE_CHOICES: { mode: ChoiceVariableMode; label: string }[] = [
		{ mode: 'shuffle', label: 'Shuffle' },
		{ mode: 'pin', label: 'Pin' },
		{ mode: 'per-image', label: 'Per image' }
	];
	const USAGE_CHOICES = [
		{ linked: false, label: 'Independent' },
		{ linked: true, label: 'Linked' }
	];
	const TYPE_CHOICES: { type: 'text' | 'choice' | 'number'; label: string }[] = [
		{ type: 'text', label: 'Text' },
		{ type: 'choice', label: 'Choice' },
		{ type: 'number', label: 'Number' }
	];
	const TYPE_LABELS = { text: 'Text', choice: 'Choice', number: 'Number' } as const;
	const segmentedButton = 'rounded-sm px-2.5 py-1 text-sm font-medium transition-colors max-md:py-2.5';
	const segmentedOn = 'bg-surface-3 text-fg shadow-raised';
	const segmentedOff = 'text-fg-muted hover:text-fg';
	const eyebrow = 'font-mono text-sm uppercase tracking-[0.04em] text-fg-subtle';

	let rows: Row[] = [];
	let selectedKey: string | null = null;
	let listQuery = '';
	let mobileView: 'list' | 'detail' = 'list';
	let previousOpen = false;
	let nextKey = 0;
	let openedWith: VariablesMap = {};
	let committedEdits: VariablesMap = {};

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) {
			listQuery = '';
			mobileView = 'list';
			initialize();
		}
	}

	function handleCancel() {
		dispatch('change', applyVariableEdits(variables, committedEdits, openedWith));
		dispatch('close');
	}

	function initialize() {
		rows = Object.entries(variables).map(([name, stored]) => ({
			key: `v${nextKey++}`,
			name,
			def: normalizeVariableDef(stored)
		}));
		committedEdits = JSON.parse(JSON.stringify(Object.fromEntries(rows.map((row) => [row.name, row.def]))));
		openedWith = JSON.parse(JSON.stringify(committedEdits));
		if (rows.length === 0) rows = [{ key: `v${nextKey++}`, name: '', def: createTextVariable() }];
		selectedKey = rows[0].key;
	}

	let pasteModalOpen = false;

	async function copyVariables() {
		const count = Object.keys(liveVariables).length;
		const ok = await copyText(serializeVariables(liveVariables));
		if (ok) toasts.success(`Copied ${count} variable${count === 1 ? '' : 's'}`);
		else toasts.error('Could not copy to clipboard');
	}

	function importVariables(merged: VariablesMap) {
		rows = Object.entries(merged).map(([name, stored]) => ({
			key: `v${nextKey++}`,
			name,
			def: normalizeVariableDef(stored)
		}));
		if (rows.length === 0) rows = [{ key: `v${nextKey++}`, name: '', def: createTextVariable() }];
		selectedKey = rows[0].key;
		commit();
	}

	function handlePasteConfirm(event: CustomEvent<{ variables: VariablesMap; count: number }>) {
		importVariables(event.detail.variables);
		pasteModalOpen = false;
		toasts.success(`Imported ${event.detail.count} variable${event.detail.count === 1 ? '' : 's'}`);
	}

	// Per-row name-validation error, keyed by `row.key` (the `{#each}` below is keyed
	// by `row.key`). Renaming one row can flip the error state of a DIFFERENT row
	// (the one it now collides with) whose own object reference didn't change — this
	// is a `$:` statement (not a plain function called from `{@const}`) so Svelte's
	// dependency scan sees `rows` directly and recomputes every row's error whenever
	// ANY row's name changes; a function call would only recompute the row whose own
	// reference changed, leaving the collision partner's error stale.
	$: nameErrorByKey = variableNameErrors(rows);

	$: dirty = JSON.stringify(committedEdits) !== JSON.stringify(openedWith);
	$: variableCount = Object.keys(liveVariables).length;

	$: liveVariables = Object.fromEntries(
		usableNamedRows(rows).map((r) => [r.name.trim(), r.def])
	) as VariablesMap;

	$: selected = rows.find((r) => r.key === selectedKey) ?? null;

	$: visibleRows = (() => {
		const needle = listQuery.trim().toLowerCase();
		if (!needle) return rows;
		return rows.filter(
			(r) =>
				r.name.toLowerCase().includes(needle) ||
				(r.def.type === 'choice' && r.def.options.some((o) => optionText(o).toLowerCase().includes(needle)))
		);
	})();

	$: anyCondition = rows.some(
		(r) => r.def.type === 'choice' && r.def.options.some((o) => !!optionWhen(o))
	);
	$: anyNumber = rows.some((r) => r.def.type === 'number');
	$: showRollPreview = anyCondition || anyNumber;

	interface RollPreviewEntry {
		name: string;
		value: string;
		note: string;
		warn: boolean;
	}

	let rollPreview: RollPreviewEntry[] | null = null;
	$: if (!showRollPreview) rollPreview = null;

	function runRollPreview() {
		const { wireMap, rolls } = buildVariablesForSubmit(liveVariables);
		const resolved: Record<string, string> = {};
		const entries: RollPreviewEntry[] = [];

		for (const name of resolveVariableOrder(liveVariables)) {
			const stored = liveVariables[name];
			if (stored === undefined) continue;
			const def = normalizeVariableDef(stored);

			if (def.type === 'text') {
				resolved[name] = (def.value ?? '').trim();
				continue;
			}
			if (def.type === 'number') {
				const numberValue = wireMap[name];
				if (numberValue !== undefined && !numberValue.startsWith('{')) resolved[name] = numberValue;
				entries.push({
					name,
					value: numberValue !== undefined && numberValue.startsWith('{') ? 'per image' : (numberValue ?? ''),
					note: '',
					warn: numberValue === undefined
				});
				continue;
			}
			if (!def.options.some((o) => optionText(o).trim().length > 0)) continue;

			const value = wireMap[name];
			if (value !== undefined) resolved[name] = value;

			let note = '';
			let warn = false;

			if (def.mode === 'shuffle') {
				const conditionedTotal = def.options.filter(
					(o) => optionText(o).trim().length > 0 && !!optionWhen(o)
				).length;
				if (conditionedTotal > 0) {
					const roll = rolls[name];
					if (!roll || (roll.eligible ?? 0) === 0) {
						note = 'no option eligible and none marked Always — will expand to nothing';
						warn = true;
					} else {
						const conditionedMatched = eligibleOptions(def, resolved).filter((o) => !!optionWhen(o)).length;
						if (conditionedMatched === 0) {
							note = 'no conditioned match — fell back to Always';
							warn = true;
						} else {
							note = `${roll.eligible} of ${roll.total} eligible`;
						}
					}
				}
			}

			entries.push({ name, value: value ?? '', note, warn: warn || value === undefined });
		}

		rollPreview = entries;
	}

	function commit() {
		const next: VariablesMap = {};
		const usable = new Set(usableNamedRows(rows));
		for (const row of rows) {
			if (usable.has(row)) next[row.name.trim()] = row.def;
			else if (row.name in committedEdits && !(row.name in next)) next[row.name] = committedEdits[row.name];
		}
		const merged = applyVariableEdits(variables, committedEdits, next);
		committedEdits = JSON.parse(JSON.stringify(next));
		variables = merged;
		dispatch('change', merged);
	}

	function addRow() {
		const key = `v${nextKey++}`;
		rows = [...rows, { key, name: '', def: createTextVariable() }];
		listQuery = '';
		selectRow(key);
	}

	function selectRow(key: string) {
		selectedKey = key;
		mobileView = 'detail';
	}

	function rowMeta(row: Row): string {
		if (row.def.type === 'text') return 'text';
		if (row.def.type === 'number') {
			const numberUses = usageCounts[row.name.trim()] ?? 0;
			const lead = row.def.mode === 'pin' ? clampNumberValue(row.def) : numberRangeLabel(row.def);
			const numberParts = [lead, row.def.mode];
			if (numberUses > 0) numberParts.push(`${numberUses} use${numberUses === 1 ? '' : 's'}`);
			return numberParts.join(' · ');
		}
		const uses = usageCounts[row.name.trim()] ?? 0;
		const parts = [`${row.def.options.length} opt`, row.def.mode];
		if (uses > 0) parts.push(`${uses} use${uses === 1 ? '' : 's'}`);
		return parts.join(' · ');
	}

	function removeRow(key: string) {
		const removed = rows.find((r) => r.key === key);
		const removedAt = rows.findIndex((r) => r.key === key);
		rows = rows.filter((r) => r.key !== key);
		if (rows.length === 0) rows = [{ key: `v${nextKey++}`, name: '', def: createTextVariable() }];
		if (selectedKey === key) selectedKey = rows[Math.min(removedAt, rows.length - 1)].key;
		mobileView = 'list';

		if (removed?.name.trim()) {
			const removedName = removed.name.trim();
			let clearedCount = 0;
			rows = rows.map((r) => {
				if (r.def.type !== 'choice') return r;
				let changed = false;
				const options = r.def.options.map((o) => {
					const when = optionWhen(o);
					if (when?.var !== removedName) return o;
					changed = true;
					clearedCount += 1;
					return optionText(o);
				});
				return changed ? { ...r, def: { ...r.def, options } } : r;
			});
			if (clearedCount > 0) {
				toasts.warning(
					clearedCount === 1
						? `Removing $${removedName} cleared 1 condition that depended on it.`
						: `Removing $${removedName} cleared ${clearedCount} conditions that depended on it.`
				);
			}
		}

		commit();
	}

	function updateName(key: string, name: string) {
		rows = rows.map((r) => (r.key === key ? { ...r, name } : r));
		commit();
	}

	function setType(key: string, type: 'text' | 'choice' | 'number') {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type === type) return r;
			const def = type === 'text' ? createTextVariable() : type === 'number' ? createNumberVariable() : createChoiceVariable();
			return { ...r, def };
		});
		commit();
	}

	function updateNumberDef(key: string, def: NumberVariableDef) {
		rows = rows.map((r) => (r.key === key && r.def.type === 'number' ? { ...r, def } : r));
		commit();
	}

	function updateTextValue(key: string, value: string) {
		rows = rows.map((r) => (r.key === key && r.def.type === 'text' ? { ...r, def: { ...r.def, value } } : r));
		commit();
	}

	function updateOptionText(key: string, index: number, text: string) {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type !== 'choice') return r;
			return {
				...r,
				def: {
					...r.def,
					options: r.def.options.map((o, i) => {
						if (i !== index) return o;
						const when = optionWhen(o);
						return when ? { text, when } : text;
					})
				}
			};
		});
		commit();
	}

	function updateOptionWhen(key: string, index: number, when: VariableOptionWhen | undefined) {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type !== 'choice') return r;
			return {
				...r,
				def: {
					...r.def,
					options: r.def.options.map((o, i): VariableOption => {
						if (i !== index) return o;
						const text = optionText(o);
						return when ? { text, when } : text;
					})
				}
			};
		});
		commit();
	}

	function addOption(key: string) {
		rows = rows.map((r) =>
			r.key === key && r.def.type === 'choice' ? { ...r, def: { ...r.def, options: [...r.def.options, ''] } } : r
		);
		commit();
	}

	function removeOption(key: string, index: number) {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type !== 'choice') return r;
			const options = r.def.options.filter((_, i) => i !== index);
			// Keep the pin pointing at the same option (or clear it if that's the
			// one being removed) rather than silently repinning to whatever now
			// sits at the old index.
			let pinnedIndex = r.def.pinnedIndex;
			if (pinnedIndex !== null) {
				if (pinnedIndex === index) pinnedIndex = null;
				else if (pinnedIndex > index) pinnedIndex -= 1;
			}
			return { ...r, def: { ...r.def, options, pinnedIndex } };
		});
		commit();
	}

	function setMode(key: string, mode: ChoiceVariableMode) {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type !== 'choice') return r;
			// Switching into pin mode with nothing pinned yet defaults to the
			// first option, so the picker below always shows a real selection.
			const pinnedIndex = mode === 'pin' ? (r.def.pinnedIndex ?? 0) : r.def.pinnedIndex;
			return { ...r, def: { ...r.def, mode, pinnedIndex } };
		});
		commit();
	}

	function setLinked(key: string, linked: boolean) {
		rows = rows.map((r) => (r.key === key && r.def.type === 'choice' ? { ...r, def: { ...r.def, linked } } : r));
		commit();
	}

	function setPinnedIndex(key: string, pinnedIndex: number) {
		rows = rows.map((r) => (r.key === key && r.def.type === 'choice' ? { ...r, def: { ...r.def, pinnedIndex } } : r));
		commit();
	}
</script>

{#snippet footerTools()}
	<Tooltip text="Copy variables as JSON">
		<Button variant="ghost" size="sm" icon="copy" disabled={variableCount === 0} onclick={copyVariables}>Copy</Button>
	</Tooltip>
	<Tooltip text="Paste variables JSON">
		<Button variant="ghost" size="sm" icon="clipboard-list" onclick={() => (pasteModalOpen = true)}>Paste…</Button>
	</Tooltip>
	{#if showRollPreview}
		<span class="mx-1 h-5 w-px flex-shrink-0 bg-line-strong max-md:hidden" aria-hidden="true"></span>
		<Button variant="ghost" size="sm" icon="shuffle" onclick={runRollPreview}>Roll preview</Button>
		{#each rollPreview ?? [] as entry, i (entry.name)}
			{#if i > 0}<Icon name="chevron-right" className="h-3 w-3 flex-shrink-0 text-fg-subtle" />{/if}
			<span class="inline-flex min-w-0 flex-wrap items-center gap-1 font-mono text-sm">
				<span class="whitespace-nowrap text-fg-subtle">${entry.name}</span>
				<span class="break-words {entry.warn ? 'text-warning' : 'text-fg'}">{entry.value || '∅'}</span>
				{#if entry.note}
					<span class={entry.warn ? 'text-warning' : 'text-fg-subtle'}>{entry.note}</span>
				{/if}
			</span>
		{/each}
	{/if}
{/snippet}

<SegmentModal
	{isOpen}
	tier="L"
	icon="braces"
	tone="variable"
	title="Prompt variables"
	context={`${variableCount} variable${variableCount === 1 ? '' : 's'} · shared by every segment of this tab`}
	primaryLabel="Done"
	submit="ctrl"
	{dirty}
	discardMessage="Your edits to the variables are undone."
	footerStart={footerTools}
	onConfirm={() => dispatch('close')}
	onCancel={handleCancel}
>
	<div class="grid h-full min-h-0 grid-cols-[17.5rem_minmax(0,1fr)] max-md:grid-cols-1">
		<div class="min-h-0 flex-col border-r border-line max-md:border-r-0 {mobileView === 'list' ? 'flex' : 'hidden md:flex'}">
			<div class="space-y-2 border-b border-line p-3">
				<label class="input flex h-9 items-center gap-2 px-2.5 text-fg-subtle max-md:h-11">
					<Icon name="search" className="h-4 w-4 flex-shrink-0" />
					<input
						type="text"
						class="min-w-0 flex-1 bg-transparent text-base text-fg outline-none placeholder:text-fg-subtle"
						placeholder="Filter variables"
						bind:value={listQuery}
						aria-label="Filter variables"
						data-autofocus
					/>
					<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">
						{listQuery.trim() ? `${visibleRows.length} of ${rows.length}` : rows.length}
					</span>
				</label>
				<Button variant="secondary" size="sm" icon="plus" class="w-full max-md:h-11" onclick={addRow}>Add variable</Button>
			</div>
			<div class="min-h-0 flex-1 overflow-y-auto p-1.5" role="listbox" aria-label="Variables">
				{#each visibleRows as row (row.key)}
					{@const error = nameErrorByKey.get(row.key) ?? null}
					{@const used = usageCounts[row.name.trim()] ?? 0}
					<button
						type="button"
						role="option"
						aria-selected={row.key === selectedKey}
						class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded px-2.5 py-2 text-left transition-colors max-md:py-3 {row.key === selectedKey
							? 'bg-signal/10 shadow-[inset_2px_0_0_rgb(var(--signal))]'
							: 'hover:bg-surface-2'}"
						on:click={() => selectRow(row.key)}
					>
						<span class="min-w-0">
							<span class="flex min-w-0 items-center gap-1.5">
								<span class="truncate font-mono text-base {error ? 'text-danger' : 'text-fg'}">{row.name ? `$${row.name}` : 'unnamed'}</span>
								{#if used > 0}
									<Tooltip text={`Used in the prompt · ${used}`}>
										<span class="inline-flex flex-shrink-0 text-success" role="img" aria-label={`Used in the prompt ${used} ${used === 1 ? 'time' : 'times'}`} data-testid="variable-used-tick">
											<Icon name="check" className="h-3.5 w-3.5" strokeWidth={3} />
										</span>
									</Tooltip>
								{/if}
							</span>
							<span class="mt-0.5 block truncate font-mono text-sm tabular-nums text-fg-subtle">{rowMeta(row)}</span>
						</span>
						<Badge size="lg">{TYPE_LABELS[row.def.type]}</Badge>
					</button>
				{:else}
					<p class="px-2.5 py-3 text-sm text-fg-subtle">No variables match.</p>
				{/each}
			</div>
			<p class="border-t border-line px-3.5 py-2.5 text-sm text-fg-subtle">
				Use <code class="rounded bg-surface-2 px-1 font-mono text-fg">${'{'}name{'}'}</code> in any segment. A Choice variable picks a new option each time you click Generate, a Number rolls between its minimum and maximum. Shared by every segment of this tab, for the life of the session.
			</p>
		</div>

		<div class="min-h-0 min-w-0 overflow-y-auto {mobileView === 'detail' ? 'block' : 'hidden md:block'}">
			{#if selected}
				{@const row = selected}
				{@const error = nameErrorByKey.get(row.key) ?? null}
				<div class="space-y-4 p-5 max-md:p-4">
					<div class="flex flex-wrap items-center gap-2">
						<Button variant="ghost" size="sm" icon="chevron-left" class="md:hidden max-md:h-11" onclick={() => (mobileView = 'list')}>Variables</Button>
						<input
							type="text"
							class="input h-9 w-52 font-mono text-base max-md:h-11 max-md:flex-1 {error ? 'border-danger' : ''}"
							placeholder="name"
							value={row.name}
							on:input={(e) => updateName(row.key, e.currentTarget.value)}
							aria-invalid={!!error}
							aria-label="Variable name"
						/>
						<div class="inline-grid grid-cols-3 gap-0.5 rounded border border-line bg-surface-2 p-0.5" role="tablist" aria-label="Variable type">
							{#each TYPE_CHOICES as choice (choice.type)}
								<button
									type="button"
									role="tab"
									aria-selected={row.def.type === choice.type}
									class="{segmentedButton} {row.def.type === choice.type ? segmentedOn : segmentedOff}"
									on:click={() => setType(row.key, choice.type)}
								>
									{choice.label}
								</button>
							{/each}
						</div>
						{#if row.def.type === 'choice'}
							{@const deps = dependencies(row.name.trim(), liveVariables)}
							{@const usedBy = dependents(row.name.trim(), liveVariables)}
							{#if deps.length > 0}
								<Badge size="lg">after&nbsp;<span class="font-mono text-signal">{deps.map((d) => `$${d}`).join(', ')}</span></Badge>
							{/if}
							{#if usedBy.length > 0}
								<Badge size="lg">used by&nbsp;<span class="font-mono text-signal">{usedBy.map((d) => `$${d}`).join(', ')}</span></Badge>
							{/if}
						{/if}
						<Button
							variant="ghost"
							size="sm"
							icon="trash"
							class="ml-auto !text-danger hover:bg-danger/10"
							ariaLabel={`Remove variable ${row.name || ''}`.trim()}
							onclick={() => removeRow(row.key)}
						>
							Remove
						</Button>
					</div>

					{#if error}
						<p class="text-sm text-danger">{error}</p>
					{/if}

					{#if row.def.type === 'text'}
						<div>
							<span class="mb-1.5 block {eyebrow}">Value</span>
							<input
								type="text"
								class="input w-full text-base"
								placeholder="value"
								value={row.def.value}
								on:input={(e) => updateTextValue(row.key, e.currentTarget.value)}
								aria-label="Variable value"
							/>
						</div>
					{:else if row.def.type === 'number'}
						<NumberVariableEditor
							name={row.name.trim()}
							def={row.def}
							variables={liveVariables}
							usageCount={usageCounts[row.name.trim()] ?? 0}
							readers={readers[row.name.trim()] ?? []}
							onChange={(next) => updateNumberDef(row.key, next)}
						/>
					{:else}
						{@const whenTarget = isWhenTarget(row.name.trim(), liveVariables)}
						{@const usageCount = usageCounts[row.name.trim()] ?? 0}
						{@const usagesLocked = whenTarget || row.def.mode !== 'shuffle'}
						<div class="grid gap-4 sm:grid-cols-2">
							<div class="space-y-1.5">
								<span class="block {eyebrow}">Value</span>
								<div class="grid grid-cols-3 gap-0.5 rounded border border-line bg-surface-2 p-0.5" role="radiogroup" aria-label={`How ${row.name || 'this variable'} picks a value`}>
									{#each MODE_CHOICES as choice (choice.mode)}
										<button
											type="button"
											role="radio"
											aria-checked={row.def.mode === choice.mode}
											class="{segmentedButton} {row.def.mode === choice.mode ? segmentedOn : segmentedOff}"
											on:click={() => setMode(row.key, choice.mode)}
										>
											{choice.label}
										</button>
									{/each}
								</div>
							</div>
							<div class="space-y-1.5">
								<span class="block {eyebrow}">Usages{usageCount > 0 ? ` · ${usageCount}` : ''}</span>
								<div class="grid grid-cols-2 gap-0.5 rounded border border-line bg-surface-2 p-0.5" role="radiogroup" aria-label={`Whether each usage of ${row.name || 'this variable'} rolls separately`}>
									{#each USAGE_CHOICES as choice (choice.label)}
										{@const on = usagesShareRoll(row.name.trim(), liveVariables, readers) === choice.linked}
										<button
											type="button"
											role="radio"
											aria-checked={on}
											disabled={usagesLocked}
											class="{segmentedButton} disabled:cursor-not-allowed disabled:opacity-60 {on ? segmentedOn : segmentedOff}"
											on:click={() => setLinked(row.key, choice.linked)}
										>
											{choice.label}
										</button>
									{/each}
								</div>
							</div>
							{#if choiceLacksOption(row.def)}
								<p class="text-sm text-danger" data-testid="variable-options-error">Add at least one option.</p>
							{/if}
						</div>
						<p class="text-sm text-fg-subtle">
							{#if whenTarget}
								Other options depend on this variable, so every usage shares one roll.
							{:else if row.def.mode === 'pin'}
								Always uses the pinned option; pick it with the radio beside it.
							{:else if row.def.mode === 'per-image'}
								Rolls again for every image, and every usage rolls on its own, so usages can't be linked in this mode. Check the result card to see what each image got.
							{:else if isVariableLinked(row.name.trim(), liveVariables, readers)}
								Every usage shares one roll per generation.
							{:else}
								Each usage rolls its own value per generation.
							{/if}
						</p>

						<div class="space-y-3">
							<span class="block {eyebrow}">Options · {row.def.options.length}</span>
							{#each row.def.options as option, index (index)}
								<div class="grid grid-cols-[1.75rem_minmax(0,1fr)_2rem] items-start gap-x-2 gap-y-1.5" data-testid="variable-option-row">
									{#if row.def.mode === 'pin'}
										<button
											type="button"
											role="radio"
											aria-checked={(row.def.pinnedIndex ?? 0) === index}
											aria-label={`Pin option ${index + 1}`}
											class="mt-2.5 grid h-4 w-4 place-items-center justify-self-end rounded-full border {(row.def.pinnedIndex ?? 0) === index
												? 'border-signal'
												: 'border-line-hover'}"
											on:click={() => setPinnedIndex(row.key, index)}
										>
											{#if (row.def.pinnedIndex ?? 0) === index}
												<span class="h-2 w-2 rounded-full bg-signal"></span>
											{/if}
										</button>
									{:else}
										<span class="mt-2 text-right font-mono text-sm tabular-nums text-fg-subtle">{index + 1}</span>
									{/if}
									<textarea
										use:autosize={optionText(option)}
										class="input min-w-0 resize-none py-2 text-base leading-5"
										rows="1"
										placeholder={`option ${index + 1}`}
										value={optionText(option)}
										on:input={(e) => updateOptionText(row.key, index, e.currentTarget.value)}
										aria-label={`Option ${index + 1}`}
									></textarea>
									<Tooltip text="Remove option">
										<button
											type="button"
											class="inline-flex h-9 w-8 flex-shrink-0 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-3 hover:text-danger"
											on:click={() => removeOption(row.key, index)}
											aria-label={`Remove option ${index + 1}`}
										>
											<Icon name="trash" className="h-4 w-4" />
										</button>
									</Tooltip>
									<div class="col-start-2 flex min-w-0 items-center gap-2" data-testid="variable-option-when">
										<span class="{eyebrow} flex-shrink-0">When</span>
										<VariableConditionChip
											ownerName={row.name.trim()}
											optionNumber={index + 1}
											when={optionWhen(option)}
											mode={row.def.mode}
											variables={liveVariables}
											onChange={(when) => updateOptionWhen(row.key, index, when)}
										/>
									</div>
								</div>
							{/each}
							<Button variant="ghost" size="sm" icon="plus" onclick={() => addOption(row.key)}>Add option</Button>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>

	<PasteVariablesModal
		isOpen={pasteModalOpen}
		existing={liveVariables}
		on:close={() => (pasteModalOpen = false)}
		on:confirm={handlePasteConfirm}
	/>
</SegmentModal>
