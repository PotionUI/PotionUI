<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import Icon from '$lib/components/Icon.svelte';
	import { Alert, Button, Badge, Spinner, EmptyState, Switch } from '$lib/components/ui';
	import FormField from '$lib/components/form-fields/FormField.svelte';
	import { registerBuiltinFieldComponents } from '$lib/fields/builtin';
	import {
		overrideEditorKind,
		pendingOverrideFrom,
		effectiveEditable,
		isOverrideEmpty,
		isOverrideUnchanged,
		buildOverridesPayload,
		buildFieldConfigIndex,
		canUseRichEditor,
		toComponentValue,
		fromComponentValue,
		rawEditorHint,
		groupFieldsByTab,
		type PendingOverride,
		type OverrideFieldGroup
	} from '$lib/utils/presetFormOverrides';
	import type { FieldConfig } from '$lib/form/reactions';
	import type { PresetFormOverrideField, PresetModeInfo } from '$lib/types/api';

	// This tab lets an admin lock a field's value, hide it, or set a different
	// default per mode - distinct from PresetConfigurationTab, which edits the
	// preset's own `configuration:` schema rather than the user-facing form.
	export let presetId: string;

	// `+layout.svelte` registers the builtin field components only inside its
	// auth-gated async init, which can resolve AFTER this tab's own data load on
	// a hard refresh. `canUseRichEditor`'s `hasFieldComponent()` check then reads
	// an empty module-level map — which Svelte's reactivity can't track, so the
	// `false` never re-evaluates and every row sticks on the raw fallback.
	// `registerBuiltinFieldComponents` is synchronous and idempotent, so calling
	// it directly here makes this component correct from its first render.
	registerBuiltinFieldComponents();

	let modes: PresetModeInfo[] = [];
	let mode = '';
	let modesLoading = true;
	let modesError = '';

	let fields: PresetFormOverrideField[] = [];
	let tabs: string[] = [];
	let pending: Record<string, PendingOverride> = {};
	let overridesLoading = false;
	let overridesError = '';
	let saving = false;
	// Which tab group's rows the table shows. Dirty tracking, `pending` and Save
	// stay global across all tabs - this only controls what's visible.
	let selectedGroup = '';
	// Richer per-field config (model_type, slider min/max, select options, ...)
	// for the fields that have it - see buildFieldConfigIndex's doc comment for
	// the two cases where a field has no entry here and falls back to a plain
	// editor instead of its real /generate widget.
	let fieldConfigIndex: Record<string, FieldConfig> = {};

	onMount(() => {
		loadModes();
	});

	function responseError(response: { message?: string } | null | undefined, fallback: string) {
		return response?.message || fallback;
	}

	async function loadModes() {
		modesLoading = true;
		modesError = '';
		try {
			const response = await api.getPresetModes(presetId);
			if (!response.success || !response.data) {
				throw new Error(responseError(response, 'Could not load the preset modes'));
			}
			modes = response.data.modes || [];
			mode = response.data.default_mode || modes[0]?.name || '';
		} catch (error) {
			logger.error('Failed to load preset modes for form overrides:', error);
			modesError = error instanceof Error ? error.message : 'Could not load the preset modes';
		} finally {
			modesLoading = false;
		}
		if (mode) await loadOverrides();
	}

	async function loadOverrides() {
		overridesLoading = true;
		overridesError = '';
		// Fetch the override inventory AND the rich per-field config together and
		// only reveal the table once BOTH have landed: the rich-vs-raw decision
		// must be made with real config in hand, or a saved model override paints
		// its raw fallback (`model:<id>`) and nothing forces a second pass once
		// the config arrives.
		try {
			const [overridesResponse, resolvedFieldConfigIndex] = await Promise.all([
				api.getPresetFormOverrides(presetId, mode),
				loadFieldConfigIndex(mode)
			]);
			if (!overridesResponse.success || !overridesResponse.data) {
				throw new Error(responseError(overridesResponse, 'Could not load form overrides'));
			}
			fields = overridesResponse.data.fields || [];
			tabs = overridesResponse.data.tabs || [];
			pending = Object.fromEntries(fields.map((field) => [field.name, pendingOverrideFrom(field)]));
			fieldConfigIndex = resolvedFieldConfigIndex;
			selectedGroup = groupFieldsByTab(fields, tabs)[0]?.label ?? '';
		} catch (error) {
			logger.error('Failed to load preset form overrides:', error);
			overridesError = error instanceof Error ? error.message : 'Could not load form overrides';
			fields = [];
			tabs = [];
			pending = {};
			fieldConfigIndex = {};
			selectedGroup = '';
		} finally {
			overridesLoading = false;
		}
	}

	/** Best-effort: the rich per-field config comes from the mode's rendered
	 *  form schema, a separate request from the override inventory. Resolves
	 *  to `{}` (never rejects) on any failure, so a schema-fetch problem just
	 *  means every field falls back to its plain editor - not fatal to the
	 *  override inventory `Promise.all` it's raced against in `loadOverrides`. */
	async function loadFieldConfigIndex(modeToLoad: string): Promise<Record<string, FieldConfig>> {
		try {
			const response = await api.getPresetFormSchema(presetId, modeToLoad);
			return response.success && response.data?.form_schema
				? buildFieldConfigIndex(response.data.form_schema)
				: {};
		} catch (error) {
			logger.error('Failed to load preset form schema for form overrides:', error);
			return {};
		}
	}

	function handleModeChange(nextMode: string) {
		if (!nextMode || nextMode === mode) return;
		mode = nextMode;
		loadOverrides();
	}

	function setPending(name: string, patch: Partial<PendingOverride>) {
		const current = pending[name];
		if (!current) return;
		pending = { ...pending, [name]: { ...current, ...patch } };
	}

	function numberOrNull(raw: string): number | null {
		if (raw.trim() === '') return null;
		const parsed = Number(raw);
		return Number.isNaN(parsed) ? null : parsed;
	}

	function handleDefaultInput(field: PresetFormOverrideField, raw: string) {
		const kind = overrideEditorKind(field);
		setPending(field.name, { hasDefault: true, default: kind === 'number' ? numberOrNull(raw) : raw });
	}

	function handleDefaultToggle(field: PresetFormOverrideField, checked: boolean) {
		setPending(field.name, { hasDefault: true, default: checked });
	}

	function handleRichDefaultChange(field: PresetFormOverrideField, componentValue: unknown) {
		setPending(field.name, { hasDefault: true, default: fromComponentValue(field.type, componentValue) });
	}

	function handleEditableToggle(field: PresetFormOverrideField) {
		const row = pending[field.name];
		if (!row || !row.visible) return;
		setPending(field.name, { editable: !row.editable });
	}

	function handleVisibleToggle(field: PresetFormOverrideField) {
		const row = pending[field.name];
		if (!row) return;
		setPending(field.name, { visible: !row.visible });
	}

	function handleResetRow(field: PresetFormOverrideField) {
		pending = {
			...pending,
			[field.name]: { hasDefault: false, default: field.preset_default, editable: true, visible: true }
		};
	}

	$: dirtyFields = fields.filter((field) => pending[field.name] && !isOverrideUnchanged(field, pending[field.name]));
	$: dirtyCount = dirtyFields.length;
	$: dirtyNames = new Set(dirtyFields.map((field) => field.name));

	$: groups = groupFieldsByTab(fields, tabs);
	$: visibleFields = groups.length > 0 ? groups.find((group) => group.label === selectedGroup)?.fields ?? [] : fields;

	function handleTabChange(label: string) {
		selectedGroup = label;
	}

	function dirtyCountFor(group: OverrideFieldGroup): number {
		return group.fields.filter((field) => dirtyNames.has(field.name)).length;
	}

	async function handleSave() {
		const payload = buildOverridesPayload(fields, pending);
		if (Object.keys(payload).length === 0) return;
		saving = true;
		try {
			const response = await api.updatePresetFormOverrides(presetId, mode, payload);
			if (!response.success || !response.data) {
				throw new Error(responseError(response, 'Could not save form overrides'));
			}
			fields = response.data.fields || fields;
			tabs = response.data.tabs || tabs;
			pending = Object.fromEntries(fields.map((field) => [field.name, pendingOverrideFrom(field)]));
			const savedGroups = groupFieldsByTab(fields, tabs);
			if (!savedGroups.some((group) => group.label === selectedGroup)) {
				selectedGroup = savedGroups[0]?.label ?? '';
			}
			toasts.success('Form overrides saved');
		} catch (error) {
			logger.error('Failed to save preset form overrides:', error);
			toasts.error(error instanceof Error ? error.message : 'Could not save form overrides');
		} finally {
			saving = false;
		}
	}

	function formatDefault(value: unknown): string {
		if (value === null || value === undefined || value === '') return '—';
		if (typeof value === 'boolean') return value ? 'On' : 'Off';
		return String(value);
	}
</script>

<div class="space-y-4">
	{#if mode}
		<Alert variant="info" density="compact" icon>
			Set a different default, lock a field so users can't change it, or hide it from the form entirely. Changes only
			apply to the <span class="font-mono">{mode}</span> mode.
		</Alert>
	{/if}

	<div class="flex flex-wrap items-center gap-3">
		<div class="flex items-center gap-2">
			<label for="form-overrides-mode" class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Mode</label>
			{#if modesLoading}
				<Spinner size="sm" />
			{:else}
				<select
					id="form-overrides-mode"
					class="input w-48"
					value={mode}
					on:change={(e) => handleModeChange((e.target as HTMLSelectElement).value)}
					disabled={modes.length === 0}
				>
					{#each modes as modeOption}
						<option value={modeOption.name}>{modeOption.label || modeOption.name}</option>
					{/each}
				</select>
			{/if}
		</div>

		{#if dirtyCount > 0}
			<Badge variant="signal" class="ml-auto">{dirtyCount} unsaved</Badge>
		{/if}
	</div>

	{#if modesError}
		<EmptyState title="Modes unavailable" description={modesError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadModes}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if overridesLoading}
		<div class="rounded-lg border border-line bg-surface-1 py-10 flex flex-col items-center justify-center">
			<Spinner size="md" />
			<p class="text-sm text-fg-muted mt-3">Loading fields for this mode…</p>
		</div>
	{:else if overridesError}
		<EmptyState title="Form overrides unavailable" description={overridesError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadOverrides}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if fields.length === 0}
		<EmptyState title="No fields on this form" description="This mode's form doesn't declare any fields to override." icon="sliders" compact />
	{:else}
		{#if groups.length > 0}
			<nav class="inline-flex flex-wrap items-center gap-1" aria-label="Form tabs">
				{#each groups as group (group.label)}
					{@const groupDirty = dirtyCountFor(group)}
					<button
						type="button"
						class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {selectedGroup === group.label ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
						on:click={() => handleTabChange(group.label)}
						aria-current={selectedGroup === group.label ? 'page' : undefined}
					>
						{group.label}
						{#if groupDirty > 0}<span class="font-mono text-2xs opacity-70">{groupDirty}</span>{/if}
					</button>
				{/each}
			</nav>
		{/if}

		<div class="bg-surface-1 rounded-lg border border-line overflow-hidden">
			<div class="overflow-x-auto">
				<table class="min-w-full divide-y divide-line">
					<thead class="bg-surface-2">
						<tr>
							<th class="px-4 py-3 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Field</th>
							<th class="px-4 py-3 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Type</th>
							<th class="px-4 py-3 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Preset default</th>
							<th class="px-4 py-3 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Default override</th>
							<th class="px-4 py-3 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">User can edit</th>
							<th class="px-4 py-3 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Visible to user</th>
							<th class="px-4 py-3 text-right font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted"></th>
						</tr>
					</thead>
					<tbody class="bg-surface-1 divide-y divide-line">
						{#each visibleFields as field (field.name)}
							{@const row = pending[field.name]}
							{#if row}
								{@const active = !isOverrideEmpty(row)}
								{@const kind = overrideEditorKind(field)}
								<tr class="hover:bg-surface-2/60">
									<td class="px-4 py-3 align-top">
										<div class="flex items-start gap-2">
											{#if active}
												<span class="mt-1.5 w-1.5 h-1.5 rounded-full bg-signal flex-shrink-0" title="Override active"></span>
											{/if}
											<div class="min-w-0">
												<p class="text-sm font-medium text-fg">{field.label}</p>
												<p class="font-mono text-2xs text-fg-subtle">{field.name}</p>
											</div>
										</div>
									</td>
									<td class="px-4 py-3 align-top">
										<Badge variant="neutral" size="sm" class="font-mono">{field.type}</Badge>
									</td>
									<td class="px-4 py-3 align-top text-sm text-fg-muted font-mono tabular-nums">
										{formatDefault(field.preset_default)}
									</td>
									<td class="px-4 py-3 align-top">
										{#if canUseRichEditor(field.type, fieldConfigIndex[field.name])}
											{@const richConfig = { ...fieldConfigIndex[field.name], title: '', description: '' }}
											<div class="min-w-[220px] max-w-sm">
												<FormField
													name={field.name}
													config={richConfig}
													value={toComponentValue(field.type, row.default)}
													onChange={(_, v) => handleRichDefaultChange(field, v)}
												/>
											</div>
										{:else if kind === 'boolean'}
											<label class="inline-flex items-center gap-2 text-sm text-fg">
												<input
													type="checkbox"
													class="w-4 h-4 rounded border-line-strong text-signal-solid focus:ring-signal"
													checked={!!row.default}
													on:change={(e) => handleDefaultToggle(field, (e.target as HTMLInputElement).checked)}
												/>
												{row.default ? 'On' : 'Off'}
											</label>
										{:else if kind === 'number'}
											<input
												type="number"
												class="input w-28 font-mono tabular-nums"
												value={row.default ?? ''}
												on:input={(e) => handleDefaultInput(field, (e.target as HTMLInputElement).value)}
											/>
										{:else if kind === 'select'}
											<select
												class="input w-40"
												value={row.default}
												on:change={(e) => handleDefaultInput(field, (e.target as HTMLSelectElement).value)}
											>
												{#each field.options || [] as option}
													<option value={option.value}>{option.label}</option>
												{/each}
											</select>
										{:else}
											<input
												type="text"
												class="input w-40"
												value={row.default ?? ''}
												on:input={(e) => handleDefaultInput(field, (e.target as HTMLInputElement).value)}
											/>
											{#if rawEditorHint(field.type)}
												<p class="mt-1 max-w-[14rem] text-2xs text-fg-subtle">{rawEditorHint(field.type)}</p>
											{/if}
										{/if}
									</td>
									<td class="px-4 py-3 align-top">
										<Switch
											checked={effectiveEditable(row)}
											onchange={() => handleEditableToggle(field)}
											disabled={!row.visible}
											label="User can edit {field.label}"
										/>
									</td>
									<td class="px-4 py-3 align-top">
										<Switch
											checked={row.visible}
											onchange={() => handleVisibleToggle(field)}
											label="Visible to user: {field.label}"
										/>
									</td>
									<td class="px-4 py-3 align-top text-right">
										{#if active}
											<Button variant="ghost" size="xs" icon="refresh" onclick={() => handleResetRow(field)}>Reset</Button>
										{/if}
									</td>
								</tr>
								{#if !row.visible}
									<tr class="bg-surface-2/40">
										<td colspan="7" class="px-4 py-1.5 text-xs text-fg-subtle">
											<Icon name="info" className="w-3 h-3 inline-block mr-1 align-[-1px]" />
											Users will not see this field{row.hasDefault ? ' — your default is used.' : '.'}
										</td>
									</tr>
								{/if}
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
		</div>

		<!-- Save lives at the bottom of the form, same as System Settings' own
		     trailing save row. -->
		<div class="flex items-center justify-end gap-2">
			<Button variant="primary" size="sm" icon="save" loading={saving} disabled={dirtyCount === 0 || saving} onclick={handleSave}>
				Save changes
			</Button>
		</div>
	{/if}
</div>
