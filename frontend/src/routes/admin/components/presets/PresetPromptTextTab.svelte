<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import { Alert, Button, EmptyState, Spinner, Switch } from '$lib/components/ui';
	import { DetailSection, DetailField } from '$lib/components/detail';
	import CheckboxGroupField from '$lib/components/form-fields/CheckboxGroupField.svelte';
	import {
		EMPTY_FIXED_PROMPT,
		FIXED_PROMPT_FIELDS,
		FIXED_PROMPT_MAX,
		buildFixedPromptPayload,
		directorModeNames,
		fixedPromptDirtyFields,
		fixedPromptDraftFrom,
		normalizeFixedPrompt,
		validateFixedPromptDraft,
		type FixedPromptDraft,
		type FixedPromptField
	} from '$lib/utils/presetFixedPrompt';
	import { invalidatePresetFixedPromptCache } from '$lib/utils/presetFixedPromptCache';
	import { assignFieldErrors, presetApiFailure, presetResponseFailure } from './presetApiFailure';
	import type { PresetFixedPrompt, PresetModeInfo } from '$lib/types/api';

	type TextField = 'prefix' | 'suffix' | 'negative';

	let {
		presetId,
		vars = null,
		dirtyCount = $bindable(0),
		saving = $bindable(false)
	}: {
		presetId: string;
		vars?: Record<string, unknown> | null;
		dirtyCount?: number;
		saving?: boolean;
	} = $props();

	let saved = $state<PresetFixedPrompt>(EMPTY_FIXED_PROMPT);
	let draft = $state<FixedPromptDraft>(fixedPromptDraftFrom(EMPTY_FIXED_PROMPT));
	let modes = $state<PresetModeInfo[]>([]);
	let loading = $state(true);
	let loadError = $state('');
	let attempted = $state(false);
	let serverErrors = $state<Partial<Record<FixedPromptField, string>>>({});
	let generalErrors = $state<string[]>([]);

	const TEXT_FIELDS: ReadonlyArray<{ key: TextField; label: string; help: string; rows: number }> = [
		{ key: 'prefix', label: 'Before the prompt', help: 'Added in front of what the user writes.', rows: 3 },
		{ key: 'suffix', label: 'After the prompt', help: 'Added after what the user writes.', rows: 3 },
		{ key: 'negative', label: 'Added to the negative prompt', help: 'Joined to the user’s negative prompt.', rows: 2 }
	];

	const modeNames = $derived(modes.map((mode) => mode.name));
	const modeOptions = $derived(modes.map((mode) => ({ value: mode.name, label: modeLabel(mode.name, mode.label) })));
	const directorLabels = $derived(
		directorModeNames(vars, modeNames).map((name) => modeLabel(name, modes.find((mode) => mode.name === name)?.label))
	);
	const dirtyFields = $derived(fixedPromptDirtyFields(draft, saved));
	const clientErrors = $derived(validateFixedPromptDraft(draft));

	$effect(() => {
		dirtyCount = dirtyFields.length;
	});

	onMount(() => {
		load();
	});

	async function load() {
		loading = true;
		loadError = '';
		try {
			const [fixedResponse, modesResponse] = await Promise.all([
				api.getPresetFixedPrompt(presetId),
				api.getPresetModes(presetId)
			]);
			if (!fixedResponse.success || !fixedResponse.data) {
				throw new Error(presetResponseFailure(fixedResponse, 'Could not load the prompt text').message);
			}
			saved = normalizeFixedPrompt(fixedResponse.data);
			draft = fixedPromptDraftFrom(saved);
			modes = modesResponse.success && modesResponse.data ? modesResponse.data.modes || [] : [];
		} catch (error) {
			logger.error('Failed to load preset prompt text:', error);
			loadError = presetApiFailure(error, 'Could not load the prompt text').message;
		} finally {
			loading = false;
		}
	}

	function errorFor(field: FixedPromptField): string | undefined {
		if (serverErrors[field]) return serverErrors[field];
		if (field === 'modes' && !attempted && !dirtyFields.includes('modes')) return undefined;
		return clientErrors[field];
	}

	function clearServerError(field: FixedPromptField) {
		if (serverErrors[field]) serverErrors = { ...serverErrors, [field]: undefined };
	}

	function setText(field: TextField, value: string) {
		draft = { ...draft, [field]: value };
		clearServerError(field);
	}

	function setLimitModes(limited: boolean) {
		draft = { ...draft, limitModes: limited, modes: limited && draft.modes.length === 0 ? [...modeNames] : draft.modes };
		clearServerError('modes');
	}

	function setModes(_name: string, value: unknown) {
		draft = { ...draft, modes: Array.isArray(value) ? value.map(String) : [] };
		clearServerError('modes');
	}

	export async function save() {
		attempted = true;
		if (Object.keys(clientErrors).length > 0 || dirtyFields.length === 0) return;
		saving = true;
		serverErrors = {};
		generalErrors = [];
		try {
			const response = await api.updatePresetFixedPrompt(presetId, buildFixedPromptPayload(draft, modeNames));
			if (!response.success || !response.data) {
				const failure = presetResponseFailure(response, 'Could not save the prompt text');
				throw Object.assign(new Error(failure.message), { presetFailure: failure });
			}
			saved = normalizeFixedPrompt(response.data);
			draft = fixedPromptDraftFrom(saved);
			attempted = false;
			invalidatePresetFixedPromptCache(presetId);
			toasts.success('Prompt text saved');
		} catch (error) {
			logger.error('Failed to save preset prompt text:', error);
			const failure =
				(error as { presetFailure?: ReturnType<typeof presetApiFailure> } | null)?.presetFailure ??
				presetApiFailure(error, 'Could not save the prompt text');
			const assigned = assignFieldErrors(failure.errors, FIXED_PROMPT_FIELDS);
			serverErrors = assigned.byField;
			generalErrors = assigned.general;
			if (failure.errors.length === 0) toasts.error(failure.message);
		} finally {
			saving = false;
		}
	}

	export function discard() {
		draft = fixedPromptDraftFrom(saved);
		attempted = false;
		serverErrors = {};
		generalErrors = [];
	}
</script>

<div class="space-y-4" data-preset-prompt-text-tab>
	{#if loading}
		<div class="flex items-center justify-center py-10">
			<Spinner size="md" />
		</div>
	{:else if loadError}
		<EmptyState title="Prompt text unavailable" description={loadError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={load}>Try again</Button>{/snippet}
		</EmptyState>
	{:else}
		<Alert variant="info" density="compact" icon>
			Added to every prompt made with this preset. Users don’t see it and can’t change it. Wildcards and variables work here too.
		</Alert>

		{#if directorLabels.length}
			<div data-director-note>
				<Alert variant="warning" density="compact" icon>
					Not applied in Video Director and Music modes: {directorLabels.join(', ')}.
				</Alert>
			</div>
		{/if}

		{#if generalErrors.length}
			<Alert variant="danger" density="compact" icon>
				{generalErrors.join(' ')}
			</Alert>
		{/if}

		<DetailSection label="Prompt text">
			<div class="space-y-4">
				{#each TEXT_FIELDS as field (field.key)}
					{@const error = errorFor(field.key)}
					<DetailField label={field.label} id="fixed-prompt-{field.key}" wide {error} help={field.help}>
						<textarea
							id="fixed-prompt-{field.key}"
							class="input w-full font-mono {error ? 'border-danger focus:ring-danger' : ''}"
							rows={field.rows}
							value={draft[field.key]}
							spellcheck="false"
							aria-invalid={error ? true : undefined}
							data-fixed-prompt-input={field.key}
							oninput={(event) => setText(field.key, (event.target as HTMLTextAreaElement).value)}
						></textarea>
						<p class="mt-1 text-right font-mono text-xs tabular-nums {draft[field.key].length > FIXED_PROMPT_MAX ? 'text-danger' : 'text-fg-subtle'}">
							{draft[field.key].length} / {FIXED_PROMPT_MAX}
						</p>
					</DetailField>
				{/each}
			</div>
		</DetailSection>

		{#if modes.length > 1}
			<DetailSection label="Modes">
				{#snippet headerExtra()}
					<label class="flex items-center gap-2 text-xs text-fg-muted" for="fixed-prompt-every-mode">
						Every mode
						<Switch id="fixed-prompt-every-mode" checked={!draft.limitModes} onchange={(checked) => setLimitModes(!checked)} label="Apply to every mode" size="sm" />
					</label>
				{/snippet}
				{#if draft.limitModes}
					<CheckboxGroupField
						name="fixed_prompt_modes"
						config={{ title: 'Apply only in these modes', options: modeOptions, layout: 'horizontal' }}
						value={draft.modes}
						onChange={setModes}
					/>
				{:else}
					<p class="text-sm text-fg-muted">Applied in every mode that takes a prompt.</p>
				{/if}
				<p class="mt-2 min-h-5 text-sm {errorFor('modes') ? 'text-danger' : 'text-fg-subtle'}" data-fixed-prompt-modes-error>
					{errorFor('modes') ?? ''}
				</p>
			</DetailSection>
		{/if}
	{/if}
</div>
