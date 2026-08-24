<!--
	Definition-driven model attributes: renders every AttributeDefinition that
	targets this model's type (GET /api/models/attributes, fetched once at
	module scope - the schema is global, not model-scoped). Two independent
	value layers:
	  - SHARED values (`model_metadata`), admin-only, edited as a batch behind
	    the pencil/Save-Cancel toggle below - same UX the old metadata-field
	    card used.
	  - a per-definition "yours" overlay (`user_model_metadata`), only for
	    definitions with `per_user: true`, editable by ANY signed-in user
	    independently of `editable`/edit-mode, saved immediately per field.
	Trigger words are just the built-in `triggers` tags attribute now - there
	is no separate trigger-words card.
-->
<script module lang="ts">
	import { api } from '$lib/services/api/index';
	import type { AttributeDefinition } from '$lib/types/models';

	let definitionsPromise: Promise<AttributeDefinition[]> | null = null;

	function loadAttributeDefinitions(): Promise<AttributeDefinition[]> {
		if (!definitionsPromise) {
			definitionsPromise = api.getAttributeDefinitions().then((response) => {
				if (response.success && response.data) return response.data.definitions;
				throw new Error(response.error || response.message || 'Failed to load attribute definitions');
			});
		}
		return definitionsPromise;
	}
</script>

<script lang="ts">
	import { onMount } from 'svelte';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import TagsChipInput from '$lib/components/form-fields/TagsChipInput.svelte';
	import { copyToClipboard } from './formatters';
	import {
		coerceAttributeInput,
		definitionsForModelType,
		extractUpdatedSharedMetadata,
		extractUpdatedUserMetadata,
		formatAttributeValue,
		inputConfigForAttribute,
		resolveEffectiveAttributeValue
	} from './ModelAttributesCard';

	export let model:
		| {
				id: string;
				model_type: string;
				model_metadata?: Record<string, unknown> | null;
				user_model_metadata?: Record<string, unknown> | null;
		  }
		| null = null;
	/** Only the admin modal can edit SHARED values; the user modal renders them
	 * read-only. The per-user "yours" overlay below is independent of this -
	 * any signed-in user may set their own regardless of `editable`. */
	export let editable: boolean = false;

	type DraftValue = string | boolean | string[];

	let definitions: AttributeDefinition[] = [];

	onMount(async () => {
		try {
			definitions = await loadAttributeDefinitions();
		} catch (error) {
			logger.error('Failed to load attribute definitions:', getErrorMessage(error));
		}
	});

	$: fields = model ? definitionsForModelType(definitions, model.model_type) : [];

	function currentMetadata(): Record<string, unknown> | null | undefined {
		return model?.model_metadata;
	}

	function currentUserMetadata(): Record<string, unknown> | null | undefined {
		return model?.user_model_metadata;
	}

	function toDraftValue(field: AttributeDefinition, value: unknown): DraftValue {
		const config = inputConfigForAttribute(field);
		if (config.type === 'checkbox') return !!value;
		if (config.type === 'tags') return Array.isArray(value) ? (value as string[]) : [];
		return value === undefined || value === null ? '' : String(value);
	}

	function extractApiErrorMessage(err: unknown): string {
		const data = (err as { response?: { data?: { error?: string; message?: string } } })?.response?.data;
		return data?.error || data?.message || getErrorMessage(err, 'Failed to save attributes');
	}

	// --- SHARED values: batch edit behind the pencil toggle (admin only) ---

	let isEditing = false;
	let editValues: Record<string, DraftValue> = {};
	let saving = false;
	let error: string | null = null;

	function startEdit() {
		editValues = Object.fromEntries(
			fields.map((field) => [
				field.key,
				toDraftValue(field, resolveEffectiveAttributeValue(field, currentMetadata(), currentUserMetadata()))
			])
		);
		error = null;
		isEditing = true;
	}

	function cancelEdit() {
		isEditing = false;
		error = null;
	}

	async function save() {
		if (!model) return;
		const values: Record<string, unknown> = {};
		for (const field of fields) {
			values[field.key] = coerceAttributeInput(field, editValues[field.key]);
		}
		saving = true;
		error = null;
		try {
			const response = await api.updateModelMetadata(model.id, values);
			if (response.success && response.data) {
				model = { ...model, model_metadata: extractUpdatedSharedMetadata(response.data, values) };
				isEditing = false;
			} else {
				error = response.error || response.message || 'Failed to save attributes';
			}
		} catch (err) {
			logger.error('Failed to save model attributes:', err);
			error = extractApiErrorMessage(err);
		} finally {
			saving = false;
		}
	}

	// --- Per-user overlay: independent, saved immediately per field ---

	let userDrafts: Record<string, DraftValue> = {};
	let userSaving: Record<string, boolean> = {};
	let userError: string | null = null;

	function userDraftFor(field: AttributeDefinition): DraftValue {
		if (field.key in userDrafts) return userDrafts[field.key];
		const stored = currentUserMetadata()?.[field.key];
		if (stored !== undefined) return toDraftValue(field, stored);
		// Seed from the effective value so the control starts at something
		// sensible (shared value or definition default) before the user touches it.
		return toDraftValue(field, resolveEffectiveAttributeValue(field, currentMetadata(), currentUserMetadata()));
	}

	async function commitUserValue(field: AttributeDefinition, raw: DraftValue) {
		if (!model) return;
		userDrafts = { ...userDrafts, [field.key]: raw };
		const coerced = coerceAttributeInput(field, raw);
		userSaving = { ...userSaving, [field.key]: true };
		userError = null;
		try {
			const response = await api.updateModelUserAttributes(model.id, { [field.key]: coerced });
			if (response.success && response.data) {
				const updated = extractUpdatedUserMetadata(response.data, { [field.key]: coerced });
				model = { ...model, user_model_metadata: { ...(model.user_model_metadata || {}), ...updated } };
				const { [field.key]: _dropped, ...rest } = userDrafts;
				userDrafts = rest;
			} else {
				userError = response.error || response.message || 'Failed to save your value';
			}
		} catch (err) {
			logger.error('Failed to save your attribute override:', err);
			userError = extractApiErrorMessage(err);
		} finally {
			userSaving = { ...userSaving, [field.key]: false };
		}
	}
</script>

{#snippet yoursControl(field: AttributeDefinition)}
	{@const userConfig = inputConfigForAttribute(field)}
	{@const draft = userDraftFor(field)}
	<div class="mt-1 pl-2 border-l-2 border-line-strong flex items-center gap-2">
		<span class="text-2xs uppercase tracking-wide text-fg-subtle flex-shrink-0">Yours</span>
		{#if userConfig.type === 'checkbox'}
			<input
				type="checkbox"
				checked={!!draft}
				on:change={(e) => commitUserValue(field, (e.target as HTMLInputElement).checked)}
			/>
		{:else if userConfig.type === 'select'}
			<select
				class="input text-xs"
				value={draft}
				on:change={(e) => commitUserValue(field, (e.target as HTMLSelectElement).value)}
			>
				{#each userConfig.options ?? [] as option (option.value)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
		{:else if userConfig.type === 'tags'}
			<div class="flex-1">
				<TagsChipInput value={draft as string[]} onChange={(next) => commitUserValue(field, next)} />
			</div>
		{:else if userConfig.type === 'number'}
			<input
				type="number"
				min={userConfig.min}
				max={userConfig.max}
				step={userConfig.step}
				value={draft}
				class="input text-xs font-mono tabular-nums w-24"
				on:blur={(e) => commitUserValue(field, (e.target as HTMLInputElement).value)}
			/>
		{:else}
			<input
				type="text"
				value={draft}
				class="input text-xs"
				on:blur={(e) => commitUserValue(field, (e.target as HTMLInputElement).value)}
			/>
		{/if}
		{#if userSaving[field.key]}<Spinner size="sm" />{/if}
	</div>
{/snippet}

{#if fields.length > 0}
	<div class="bg-surface-2 rounded-lg p-4">
		<div class="flex items-center justify-between mb-3">
			<div class="flex items-center gap-2">
				<Icon name="settings" className="w-5 h-5 text-fg-muted" />
				<h3 class="text-base font-semibold text-fg">Attributes</h3>
			</div>
			{#if editable}
				<button
					class="text-fg-subtle hover:text-fg-muted p-2"
					on:click={() => (isEditing ? cancelEdit() : startEdit())}
					aria-label={isEditing ? 'Cancel editing' : 'Edit attributes'}
				>
					<Icon name={isEditing ? 'close' : 'edit'} className="w-4 h-4" />
				</button>
			{/if}
		</div>

		{#if editable && isEditing}
			<div class="space-y-3">
				{#each fields as field (field.key)}
					{@const inputConfig = inputConfigForAttribute(field)}
					<div>
						<label class="block text-xs text-fg-muted mb-1" for={`attr-${field.key}`}>
							{field.label}
						</label>
						{#if inputConfig.type === 'checkbox'}
							<input
								id={`attr-${field.key}`}
								type="checkbox"
								checked={!!editValues[field.key]}
								on:change={(e) => (editValues[field.key] = (e.target as HTMLInputElement).checked)}
							/>
						{:else if inputConfig.type === 'select'}
							<select
								id={`attr-${field.key}`}
								class="input text-sm"
								bind:value={editValues[field.key]}
							>
								{#each inputConfig.options ?? [] as option (option.value)}
									<option value={option.value}>{option.label}</option>
								{/each}
							</select>
						{:else if inputConfig.type === 'tags'}
							<TagsChipInput
								value={editValues[field.key] as string[]}
								onChange={(next) => (editValues[field.key] = next)}
							/>
						{:else if inputConfig.type === 'number'}
							<input
								id={`attr-${field.key}`}
								type="number"
								min={inputConfig.min}
								max={inputConfig.max}
								step={inputConfig.step}
								value={editValues[field.key]}
								on:input={(e) => (editValues[field.key] = (e.target as HTMLInputElement).value)}
								class="input text-sm font-mono tabular-nums"
							/>
						{:else}
							<input
								id={`attr-${field.key}`}
								type="text"
								value={editValues[field.key]}
								on:input={(e) => (editValues[field.key] = (e.target as HTMLInputElement).value)}
								class="input text-sm"
							/>
						{/if}
						{#if field.description}
							<p class="text-2xs text-fg-subtle mt-1">{field.description}</p>
						{/if}
					</div>
					{#if field.per_user}{@render yoursControl(field)}{/if}
				{/each}
				{#if error}
					<p class="text-xs text-danger">{error}</p>
				{/if}
				<div class="flex gap-2 justify-end">
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:bg-surface-3 rounded transition-colors"
						on:click={cancelEdit}
					>
						Cancel
					</button>
					<button
						class="px-3 py-1.5 text-sm bg-accent text-accent-contrast rounded hover:bg-accent-hover transition-colors disabled:opacity-50"
						on:click={save}
						disabled={saving}
					>
						{saving ? 'Saving...' : 'Save'}
					</button>
				</div>
			</div>
		{:else}
			<div class="space-y-2 text-xs">
				{#each fields as field (field.key)}
					{@const value = resolveEffectiveAttributeValue(field, currentMetadata(), currentUserMetadata())}
					{#if field.field_type === 'tags'}
						<div>
							<span class="text-fg-muted block mb-1">{field.label}</span>
							<TagsChipInput
								value={Array.isArray(value) ? (value as string[]) : []}
								editable={false}
								emptyText="None set"
								onChipClick={copyToClipboard}
							/>
						</div>
					{:else}
						<div class="flex items-center justify-between py-1 gap-2">
							<span class="text-fg-muted flex-shrink-0">{field.label}</span>
							<span class="font-medium font-mono tabular-nums text-fg">
								{formatAttributeValue(field, value)}
							</span>
						</div>
					{/if}
					{#if field.per_user}{@render yoursControl(field)}{/if}
				{/each}
				{#if userError}
					<p class="text-xs text-danger">{userError}</p>
				{/if}
			</div>
		{/if}
	</div>
{/if}
