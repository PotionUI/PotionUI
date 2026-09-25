<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { invalidatePresets } from '$lib/stores/presetsCatalog';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Alert, Button, Badge, Spinner, EmptyState, Switch, SegmentedControl } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
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

	let {
		presetId,
		dirtyCount = $bindable(0),
		saving = $bindable(false)
	}: {
		presetId: string;
		dirtyCount?: number;
		saving?: boolean;
	} = $props();

	registerBuiltinFieldComponents();

	let modes = $state<PresetModeInfo[]>([]);
	let mode = $state('');
	let modesLoading = $state(true);
	let modesError = $state('');

	let fields = $state<PresetFormOverrideField[]>([]);
	let tabs = $state<string[]>([]);
	let pending = $state<Record<string, PendingOverride>>({});
	let overridesLoading = $state(false);
	let overridesError = $state('');
	let selectedGroup = $state('');
	let fieldConfigIndex = $state<Record<string, FieldConfig>>({});

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
		try {
			const overridesResponse = await api.getPresetFormOverrides(presetId, mode);
			if (!overridesResponse.success || !overridesResponse.data) {
				throw new Error(responseError(overridesResponse, 'Could not load form overrides'));
			}
			fields = overridesResponse.data.fields || [];
			tabs = overridesResponse.data.tabs || [];
			pending = Object.fromEntries(fields.map((field) => [field.name, pendingOverrideFrom(field)]));
			fieldConfigIndex = buildFieldConfigIndex(overridesResponse.data.form_schemas || []);
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

	const dirtyFields = $derived(fields.filter((field) => pending[field.name] && !isOverrideUnchanged(field, pending[field.name])));
	const dirtyNames = $derived(new Set(dirtyFields.map((field) => field.name)));

	$effect(() => {
		dirtyCount = dirtyFields.length;
	});

	const groups = $derived(groupFieldsByTab(fields, tabs));
	const visibleFields = $derived(groups.length > 0 ? groups.find((group) => group.label === selectedGroup)?.fields ?? [] : fields);
	const groupItems = $derived(
		groups.map((group) => ({ id: group.label, label: group.label, count: dirtyCountFor(group) || undefined }))
	);

	function handleTabChange(label: string) {
		selectedGroup = label;
	}

	function dirtyCountFor(group: OverrideFieldGroup): number {
		return group.fields.filter((field) => dirtyNames.has(field.name)).length;
	}

	export async function save() {
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
			invalidatePresets();
			toasts.success('Form overrides saved');
		} catch (error) {
			logger.error('Failed to save preset form overrides:', error);
			toasts.error(error instanceof Error ? error.message : 'Could not save form overrides');
		} finally {
			saving = false;
		}
	}

	export function discard() {
		pending = Object.fromEntries(fields.map((field) => [field.name, pendingOverrideFrom(field)]));
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
					onchange={(e) => handleModeChange((e.target as HTMLSelectElement).value)}
					disabled={modes.length === 0}
				>
					{#each modes as modeOption}
						<option value={modeOption.name}>{modeOption.label || modeOption.name}</option>
					{/each}
				</select>
			{/if}
		</div>
	</div>

	{#if modesError}
		<EmptyState title="Modes unavailable" description={modesError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadModes}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if overridesLoading}
		<div class="flex items-center justify-center py-10">
			<Spinner size="md" />
		</div>
	{:else if overridesError}
		<EmptyState title="Form overrides unavailable" description={overridesError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadOverrides}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if fields.length === 0}
		<EmptyState title="No fields on this form" description="This mode's form doesn't declare any fields to override." icon="sliders" compact />
	{:else}
		<DetailSection label="Fields" padded={false}>
			{#snippet headerExtra()}
				{#if groups.length > 0}
					<SegmentedControl items={groupItems} selected={selectedGroup} onSelect={handleTabChange} ariaLabel="Form tabs" />
				{/if}
			{/snippet}
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
												<Tooltip text="Override active" wrapperClass="mt-1.5">
													<span class="block w-1.5 h-1.5 rounded-full bg-signal flex-shrink-0"></span>
												</Tooltip>
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
													onchange={(e) => handleDefaultToggle(field, (e.target as HTMLInputElement).checked)}
												/>
												{row.default ? 'On' : 'Off'}
											</label>
										{:else if kind === 'number'}
											<input
												type="number"
												class="input w-28 font-mono tabular-nums"
												value={row.default ?? ''}
												oninput={(e) => handleDefaultInput(field, (e.target as HTMLInputElement).value)}
											/>
										{:else if kind === 'select'}
											<select
												class="input w-40"
												value={row.default}
												onchange={(e) => handleDefaultInput(field, (e.target as HTMLSelectElement).value)}
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
												oninput={(e) => handleDefaultInput(field, (e.target as HTMLInputElement).value)}
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
		</DetailSection>
	{/if}
</div>
