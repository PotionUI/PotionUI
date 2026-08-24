<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from './modals/BaseModal.svelte';
	import { Button } from '$lib/components/ui';
	import Icon from './Icon.svelte';
	import { isValidVariableName } from '$lib/utils/promptVariables';
	import {
		normalizeVariableDef,
		createTextVariable,
		createChoiceVariable,
		type ChoiceVariableMode,
		type VariableDef,
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

	const dispatch = createEventDispatcher<{ close: void; change: VariablesMap }>();

	interface Row {
		key: string; // stable local key so bindings don't jump around as `name` is edited
		name: string;
		def: VariableDef;
	}

	let rows: Row[] = [];
	let previousOpen = false;
	let nextKey = 0;

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) initialize();
	}

	function initialize() {
		rows = Object.entries(variables).map(([name, stored]) => ({
			key: `v${nextKey++}`,
			name,
			def: normalizeVariableDef(stored)
		}));
		if (rows.length === 0) rows = [{ key: `v${nextKey++}`, name: '', def: createTextVariable() }];
	}

	// Per-row name-validation error, keyed by `row.key` (the `{#each}` below is keyed
	// by `row.key`). Renaming one row can flip the error state of a DIFFERENT row
	// (the one it now collides with) whose own object reference didn't change — this
	// is a `$:` statement (not a plain function called from `{@const}`) so Svelte's
	// dependency scan sees `rows` directly and recomputes every row's error whenever
	// ANY row's name changes; a function call would only recompute the row whose own
	// reference changed, leaving the collision partner's error stale.
	$: nameErrorByKey = new Map(
		rows.map((row) => {
			if (!row.name) return [row.key, null]; // an empty draft row isn't an error yet
			if (!isValidVariableName(row.name)) {
				return [row.key, 'Letters, numbers, underscore; must not start with a number.'];
			}
			const dupes = rows.filter((r) => r.name === row.name);
			if (dupes.length > 1) return [row.key, 'Duplicate name — only the last one would be used.'];
			return [row.key, null];
		})
	);

	function commit() {
		const next: VariablesMap = {};
		for (const row of rows) {
			const name = row.name.trim();
			if (!name || !isValidVariableName(name)) continue;
			next[name] = row.def;
		}
		dispatch('change', next);
	}

	function addRow() {
		rows = [...rows, { key: `v${nextKey++}`, name: '', def: createTextVariable() }];
	}

	function removeRow(key: string) {
		rows = rows.filter((r) => r.key !== key);
		commit();
	}

	function updateName(key: string, name: string) {
		rows = rows.map((r) => (r.key === key ? { ...r, name } : r));
		commit();
	}

	function setType(key: string, type: 'text' | 'choice') {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type === type) return r;
			return { ...r, def: type === 'text' ? createTextVariable() : createChoiceVariable() };
		});
		commit();
	}

	function updateTextValue(key: string, value: string) {
		rows = rows.map((r) => (r.key === key && r.def.type === 'text' ? { ...r, def: { ...r.def, value } } : r));
		commit();
	}

	function updateOptionText(key: string, index: number, text: string) {
		rows = rows.map((r) => {
			if (r.key !== key || r.def.type !== 'choice') return r;
			return { ...r, def: { ...r.def, options: r.def.options.map((o, i) => (i === index ? text : o)) } };
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

	function handleModeChange(key: string, raw: string) {
		setMode(key, raw as ChoiceVariableMode);
	}

	function setPinnedIndex(key: string, pinnedIndex: number) {
		rows = rows.map((r) => (r.key === key && r.def.type === 'choice' ? { ...r, def: { ...r.def, pinnedIndex } } : r));
		commit();
	}

	function handlePinnedIndexChange(key: string, raw: string) {
		setPinnedIndex(key, parseInt(raw, 10));
	}
</script>

<BaseModal {isOpen} title="Prompt variables" sizeClass="md:max-w-2xl md:w-full" on:close={() => dispatch('close')}>
	<svelte:fragment slot="headerIcon"><Icon name="braces" className="h-5 w-5 text-fg-muted" /></svelte:fragment>
	<div class="space-y-3 p-4 sm:p-6">
		<p class="text-xs text-fg-subtle">
			Defined here, used in any segment as <code class="rounded bg-surface-2 px-1 py-0.5 font-mono text-2xs text-fg">${'{'}name{'}'}</code>.
			A <span class="text-fg">Choice</span> variable picks a new option each time you click Generate — same idea as an
			phrasebook chip's shuffle. Shared by every segment of this tab, for the life of the session.
		</p>

		<div class="space-y-3">
			{#each rows as row (row.key)}
				{@const error = nameErrorByKey.get(row.key) ?? null}
				<div class="rounded-lg border border-line-strong/60 bg-surface-2/30 p-3">
					<div class="flex items-start gap-2">
						<div class="min-w-0 flex-1 space-y-2.5">
							<div class="flex flex-wrap items-center gap-2">
								<input
									type="text"
									class="input w-40 font-mono text-sm {error ? 'border-danger' : ''}"
									placeholder="name"
									value={row.name}
									on:input={(e) => updateName(row.key, e.currentTarget.value)}
									aria-invalid={!!error}
									aria-label="Variable name"
								/>

								<div class="inline-flex rounded bg-surface-2 p-0.5" role="tablist" aria-label="Variable type">
									<button
										type="button"
										role="tab"
										aria-selected={row.def.type === 'text'}
										class="rounded px-2.5 py-1 text-xs font-medium transition-colors {row.def.type === 'text'
											? 'bg-surface-1 text-fg shadow-raised'
											: 'text-fg-muted hover:text-fg'}"
										on:click={() => setType(row.key, 'text')}
									>
										Text
									</button>
									<button
										type="button"
										role="tab"
										aria-selected={row.def.type === 'choice'}
										class="rounded px-2.5 py-1 text-xs font-medium transition-colors {row.def.type === 'choice'
											? 'bg-signal/15 text-signal'
											: 'text-fg-muted hover:text-fg'}"
										on:click={() => setType(row.key, 'choice')}
									>
										Choice
									</button>
								</div>
							</div>

							{#if error}
								<p class="text-2xs text-danger">{error}</p>
							{/if}

							{#if row.def.type === 'text'}
								<input
									type="text"
									class="input w-full text-sm"
									placeholder="value"
									value={row.def.value}
									on:input={(e) => updateTextValue(row.key, e.currentTarget.value)}
									aria-label="Variable value"
								/>
							{:else}
								<div class="space-y-1.5">
									{#each row.def.options as option, index (index)}
										<div class="flex items-center gap-1.5">
											<input
												type="text"
												class="input min-w-0 flex-1 py-1 text-sm"
												placeholder={`option ${index + 1}`}
												value={option}
												on:input={(e) => updateOptionText(row.key, index, e.currentTarget.value)}
												aria-label={`Option ${index + 1}`}
											/>
											<button
												type="button"
												class="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-3 hover:text-danger"
												on:click={() => removeOption(row.key, index)}
												aria-label={`Remove option ${index + 1}`}
											>
												<Icon name="trash" className="h-3.5 w-3.5" />
											</button>
										</div>
									{/each}
								</div>

								<button
									type="button"
									class="inline-flex items-center gap-1 rounded px-1.5 py-1 text-2xs font-medium text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg"
									on:click={() => addOption(row.key)}
								>
									<Icon name="plus" className="h-3.5 w-3.5" />
									Add option
								</button>

								<div class="flex flex-wrap items-center gap-2">
									<label class="flex flex-1 min-w-0 items-center gap-1.5 text-2xs text-fg-muted">
										<span class="flex-shrink-0 font-mono uppercase tracking-[0.06em]">Value</span>
										<select
											class="input min-w-0 flex-1 py-1 text-xs"
											value={row.def.mode}
											on:change={(e) => handleModeChange(row.key, e.currentTarget.value)}
											aria-label={`How ${row.name || 'this variable'} picks a value`}
										>
											<option value="shuffle">Shuffle (new pick each generation)</option>
											<option value="pin">Use one specific choice</option>
											<option value="per-image">Re-roll for every image (advanced — see the result card for what rolled)</option>
										</select>
									</label>

									{#if row.def.mode === 'pin'}
										<select
											class="input py-1 text-xs"
											value={row.def.pinnedIndex ?? 0}
											on:change={(e) => handlePinnedIndexChange(row.key, e.currentTarget.value)}
											aria-label={`Which option to use for ${row.name || 'this variable'}`}
										>
											{#each row.def.options as option, index (index)}
												<option value={index}>{option || `Option ${index + 1}`}</option>
											{/each}
										</select>
									{/if}
								</div>
							{/if}
						</div>

						<button
							type="button"
							class="mt-0.5 inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-2 hover:text-danger"
							on:click={() => removeRow(row.key)}
							aria-label={`Remove variable ${row.name || ''}`.trim()}
							title="Remove"
						>
							<Icon name="trash" className="h-4 w-4" />
						</button>
					</div>
				</div>
			{/each}
		</div>

		<Button variant="secondary" size="xs" icon="plus" onclick={addRow}>Add variable</Button>
	</div>
	<svelte:fragment slot="footer">
		<div class="flex justify-end gap-2 px-4 py-3 sm:px-6">
			<Button variant="primary" onclick={() => dispatch('close')}>Done</Button>
		</div>
	</svelte:fragment>
</BaseModal>
