<!--
	Shared create/edit fields for an attribute definition - the same idiom as
	BackendForm.svelte: `layout="panel"` renders bordered/raised sections (wide
	detail pane), `layout="plain"` renders border-t divided groups with mono
	micro-labels (the narrower create modal). The caller owns dirty tracking,
	save/discard and the API call; this component only edits `draft`.

	Key auto-slugs from the label while creating a new definition, until the
	admin edits the key field directly - editing an already-saved definition
	never re-slugs the key out from under a saved value. A `system` definition
	locks key + field_type entirely (`locked`), per the attribute contract.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Input } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailSection } from '$lib/components/detail';
	import TagsChipInput from '$lib/components/form-fields/TagsChipInput.svelte';
	import type { AttributeFieldType } from '$lib/types/models';
	import {
		addSelectOption,
		removeSelectOption,
		resetDraftForFieldType,
		slugifyAttributeKey,
		type AttributeDraft
	} from './attributeDefinitionForm';

	let {
		draft = $bindable(),
		layout = 'plain',
		idPrefix,
		locked = false,
		modelTypeOptions = []
	}: {
		draft: AttributeDraft;
		layout?: 'panel' | 'plain';
		idPrefix: string;
		/** True only for a system definition - key and field_type can't change. */
		locked?: boolean;
		modelTypeOptions?: string[];
	} = $props();

	const FIELD_TYPES: { value: AttributeFieldType; label: string }[] = [
		{ value: 'text', label: 'Text' },
		{ value: 'number', label: 'Number' },
		{ value: 'slider', label: 'Slider (number with min/max/step)' },
		{ value: 'checkbox', label: 'Checkbox' },
		{ value: 'select', label: 'Select' },
		{ value: 'tags', label: 'Tags' }
	];

	// Only while creating: label edits re-slug the key, until the admin edits
	// the key field directly.
	let keyManuallyEdited = $state(!!draft.id);

	const isPanel = $derived(layout === 'panel');
	const labelClass = $derived(isPanel ? 'block text-sm font-medium text-fg-muted mb-1' : 'label');
	const isNumericType = $derived(draft.field_type === 'slider' || draft.field_type === 'number');

	// `bind:value` above has already written the new label into `draft.label`
	// by the time this fires - see attributeDefinitionForm.ts for the pure slug fn.
	function handleLabelInput() {
		if (!locked && !draft.id && !keyManuallyEdited) {
			draft.key = slugifyAttributeKey(draft.label);
		}
	}

	function handleFieldTypeChange(fieldType: AttributeFieldType) {
		draft = resetDraftForFieldType(draft, fieldType);
	}

	function toggleModelType(type: string) {
		draft.model_types = draft.model_types.includes(type)
			? draft.model_types.filter((t) => t !== type)
			: [...draft.model_types, type];
	}
</script>

{#snippet section(title: string, isFirst: boolean, body: Snippet)}
	{#if isPanel}
		<DetailSection label={title}>
			{@render body()}
		</DetailSection>
	{:else}
		<div class={isFirst ? '' : 'border-t border-line pt-5'}>
			<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-2.5">{title}</h3>
			{@render body()}
		</div>
	{/if}
{/snippet}

{#snippet identityFields()}
	<div class="space-y-4">
		<div>
			<label for="{idPrefix}-label" class={labelClass}>Label <span class="text-danger">*</span></label>
			<input
				id="{idPrefix}-label"
				type="text"
				class="input"
				bind:value={draft.label}
				oninput={() => handleLabelInput()}
				required
			/>
		</div>
		<div>
			<div class="flex items-center gap-1.5 mb-1">
				<label for="{idPrefix}-key" class={labelClass + ' !mb-0'}>Key <span class="text-danger">*</span></label>
				{#if locked}
					<Tooltip text="A system definition's key can't be changed.">
						<Icon name="shield" className="w-3 h-3 text-fg-subtle" />
					</Tooltip>
				{/if}
			</div>
			<input
				id="{idPrefix}-key"
				type="text"
				class="input font-mono"
				bind:value={draft.key}
				oninput={() => (keyManuallyEdited = true)}
				disabled={locked}
				required
			/>
			<p class="text-xs text-fg-subtle mt-1">The `model_metadata` key values are stored under.</p>
		</div>
		<div>
			<div class="flex items-center gap-1.5 mb-1">
				<span class={labelClass + ' !mb-0'}>Field type <span class="text-danger">*</span></span>
				{#if locked}
					<Tooltip text="A system definition's field type can't be changed.">
						<Icon name="shield" className="w-3 h-3 text-fg-subtle" />
					</Tooltip>
				{/if}
			</div>
			<select
				id="{idPrefix}-field-type"
				class="input"
				value={draft.field_type}
				onchange={(e) => handleFieldTypeChange((e.target as HTMLSelectElement).value as AttributeFieldType)}
				disabled={locked}
			>
				{#each FIELD_TYPES as option (option.value)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
		</div>
		<div>
			<label for="{idPrefix}-description" class={labelClass}>Description</label>
			<textarea
				id="{idPrefix}-description"
				class="input"
				rows="2"
				bind:value={draft.description}
				placeholder="Shown to admins under the value..."
			></textarea>
		</div>
	</div>
{/snippet}

{#snippet scopeFields()}
	<div class="space-y-4">
		<div>
			<span class={labelClass}>Applies to</span>
			<div class="flex flex-wrap gap-1.5">
				{#each modelTypeOptions as type (type)}
					<button
						type="button"
						class="px-2 py-0.5 text-xs rounded border transition-colors {draft.model_types.includes(type)
							? 'bg-signal/10 text-signal border-signal/25'
							: 'text-fg-muted border-line-strong hover:text-fg hover:border-line-hover'}"
						aria-pressed={draft.model_types.includes(type)}
						onclick={() => toggleModelType(type)}
					>
						{type}
					</button>
				{/each}
			</div>
			<p class="text-xs text-fg-subtle mt-1">
				{draft.model_types.length === 0
					? 'No types selected — applies to every model type.'
					: `Applies only to: ${draft.model_types.join(', ')}`}
			</p>
		</div>
		<div class="flex items-center gap-2">
			<input
				id="{idPrefix}-per-user"
				type="checkbox"
				bind:checked={draft.per_user}
				class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
			/>
			<label for="{idPrefix}-per-user" class="text-sm font-medium text-fg-muted">
				Per-user
				<span class="block text-xs font-normal text-fg-subtle">Each user may set their own value, on top of the shared one.</span>
			</label>
		</div>
		<div class="flex items-center gap-2">
			<input
				id="{idPrefix}-admin-only"
				type="checkbox"
				bind:checked={draft.admin_only}
				class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
			/>
			<label for="{idPrefix}-admin-only" class="text-sm font-medium text-fg-muted">
				Admin-only
				<span class="block text-xs font-normal text-fg-subtle">Hidden from non-admins entirely.</span>
			</label>
		</div>
	</div>
{/snippet}

{#snippet configurationFields()}
	<div class="space-y-4">
		{#if isNumericType}
			<div class="grid grid-cols-3 gap-3">
				<div>
					<label for="{idPrefix}-min" class={labelClass}>Min</label>
					<input
						id="{idPrefix}-min"
						type="number"
						class="input font-mono tabular-nums"
						value={draft.config.min ?? ''}
						oninput={(e) => (draft.config.min = (e.target as HTMLInputElement).value === '' ? undefined : Number((e.target as HTMLInputElement).value))}
					/>
				</div>
				<div>
					<label for="{idPrefix}-max" class={labelClass}>Max</label>
					<input
						id="{idPrefix}-max"
						type="number"
						class="input font-mono tabular-nums"
						value={draft.config.max ?? ''}
						oninput={(e) => (draft.config.max = (e.target as HTMLInputElement).value === '' ? undefined : Number((e.target as HTMLInputElement).value))}
					/>
				</div>
				<div>
					<label for="{idPrefix}-step" class={labelClass}>Step</label>
					<input
						id="{idPrefix}-step"
						type="number"
						class="input font-mono tabular-nums"
						value={draft.config.step ?? ''}
						oninput={(e) => (draft.config.step = (e.target as HTMLInputElement).value === '' ? undefined : Number((e.target as HTMLInputElement).value))}
					/>
				</div>
			</div>
		{:else if draft.field_type === 'select'}
			<div class="space-y-2">
				<span class={labelClass}>Options</span>
				{#each draft.config.options as option, index (index)}
					<div class="flex items-center gap-2">
						<input
							type="text"
							class="input text-sm flex-1"
							placeholder="value"
							bind:value={option.value}
						/>
						<input
							type="text"
							class="input text-sm flex-1"
							placeholder="label"
							bind:value={option.label}
						/>
						<button
							type="button"
							class="text-fg-subtle hover:text-danger p-1"
							aria-label="Remove option"
							onclick={() => (draft.config.options = removeSelectOption(draft.config.options, index))}
						>
							<Icon name="close" className="w-4 h-4" />
						</button>
					</div>
				{/each}
				<button
					type="button"
					class="text-xs text-fg-muted hover:text-fg"
					onclick={() => (draft.config.options = addSelectOption(draft.config.options))}
				>
					+ Add option
				</button>
			</div>
		{:else}
			<p class="text-xs text-fg-subtle">This field type has no extra configuration.</p>
		{/if}
	</div>
{/snippet}

{#snippet defaultValueField()}
	<div>
		<label for="{idPrefix}-default" class={labelClass}>Default value</label>
		{#if draft.field_type === 'checkbox'}
			<input
				id="{idPrefix}-default"
				type="checkbox"
				checked={!!draft.default_value}
				onchange={(e) => (draft.default_value = (e.target as HTMLInputElement).checked)}
			/>
		{:else if draft.field_type === 'select'}
			<select id="{idPrefix}-default" class="input" bind:value={draft.default_value}>
				<option value="">—</option>
				{#each draft.config.options as option (option.value)}
					<option value={option.value}>{option.label || option.value}</option>
				{/each}
			</select>
		{:else if draft.field_type === 'tags'}
			<TagsChipInput
				value={draft.default_value as string[]}
				onChange={(next) => (draft.default_value = next)}
			/>
		{:else if isNumericType}
			<input
				id="{idPrefix}-default"
				type="number"
				min={draft.config.min}
				max={draft.config.max}
				step={draft.config.step}
				class="input font-mono tabular-nums"
				value={draft.default_value}
				oninput={(e) => (draft.default_value = (e.target as HTMLInputElement).value)}
			/>
		{:else}
			<Input
				id="{idPrefix}-default"
				type="text"
				value={draft.default_value as string}
				oninput={(e: Event) => (draft.default_value = (e.target as HTMLInputElement).value)}
			/>
		{/if}
	</div>
{/snippet}

<div class="space-y-5">
	{@render section('Identity', true, identityFields)}
	{@render section('Configuration', false, configurationFields)}
	{@render section('Default value', false, defaultValueField)}
	{@render section('Scope', false, scopeFields)}
</div>
