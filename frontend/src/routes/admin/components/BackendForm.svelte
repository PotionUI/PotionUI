<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { EngineField, EngineDescriptor } from '$lib/services/admin-api';
	import { Input } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';

	/**
	 * Shared Identity / Connection / Behavior field set for both the create
	 * modal and the detail-pane edit form on the Backends admin tab.
	 * `mode` only affects the submit-adjacent bits the caller renders around
	 * this (title, footer button); this component is otherwise mode-agnostic —
	 * the caller owns dirty tracking, save/discard, and API calls.
	 *
	 * `layout="panel"` renders each group as a bordered/raised `<section>`
	 * (the wide detail pane); `layout="plain"` renders `border-t`-divided
	 * groups with mono micro-labels (the narrower create modal).
	 *
	 * Engine selection is only ever mutable in create mode — `engineMutable`
	 * drives that, not `mode`, so a caller can't accidentally wire an editable
	 * engine picker into an edit form.
	 */
	let {
		draft = $bindable(),
		layout = 'plain',
		idPrefix,
		engineMutable,
		engineLabel = '',
		creatableEngines = [],
		onDriverChange,
		fieldDescriptors = [],
		enabledPlacement = 'inline',
		fieldHints = {}
	}: {
		draft: Record<string, any>;
		mode: 'create' | 'edit';
		layout?: 'panel' | 'plain';
		idPrefix: string;
		/** True only for the create form — an existing backend's driver can't change. */
		engineMutable: boolean;
		/** Formatted display name shown when the driver is immutable. */
		engineLabel?: string;
		creatableEngines?: EngineDescriptor[];
		/** An engine with more than one driver (native.local/native.remote) shares
		 * one `engine` value across descriptors — the picker keys and reports by
		 * `driver`, the actually-unique identifier, not `engine`. */
		onDriverChange?: (driver: string) => void;
		fieldDescriptors?: EngineField[];
		/** 'inline' renders the Enabled checkbox in Behavior; 'none' omits it
		 * (edit mode surfaces enable/disable via a live Switch elsewhere). */
		enabledPlacement?: 'inline' | 'none';
		/** Extra UI-only guidance rendered under a named connection field, below
		 * its own server-supplied `description` — keyed by field name so a caller
		 * never has to hardcode which field it is inside this generic form. */
		fieldHints?: Record<string, Snippet>;
	} = $props();

	const isPanel = $derived(layout === 'panel');
	// Regular label in the wide pane; mono micro-label (`.label`, app.css) in the modal.
	const labelClass = $derived(isPanel ? 'block text-sm font-medium text-fg-muted mb-1' : 'label');
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
			<label for="{idPrefix}-name" class={labelClass}>Name <span class="text-danger">*</span></label>
			<Input id="{idPrefix}-name" type="text" bind:value={draft.name} required />
		</div>
		<div>
			{#if engineMutable}
				<label for="{idPrefix}-engine" class={labelClass}>Engine <span class="text-danger">*</span></label>
				<select
					id="{idPrefix}-engine"
					class="input"
					value={draft.driver}
					onchange={(e) => onDriverChange?.((e.target as HTMLSelectElement).value)}
					required
				>
					{#each creatableEngines as descriptor (descriptor.driver)}
						<option value={descriptor.driver}>{descriptor.label}</option>
					{/each}
				</select>
			{:else}
				<span class={labelClass}>Engine</span>
				<p class="text-sm font-mono text-fg">{engineLabel}</p>
				<p class="text-xs text-fg-subtle mt-1">Cannot be changed after creation.</p>
			{/if}
		</div>
	</div>
{/snippet}

{#snippet connectionFields()}
	<div class="space-y-4">
		{#each fieldDescriptors as field (field.name)}
			<div>
				{#if field.options}
					<label for="{idPrefix}-{field.name}" class={labelClass}>
						{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
					</label>
					<select id="{idPrefix}-{field.name}" bind:value={draft[field.name]} class="input">
						{#each field.options as option (option)}
							<option value={option}>{option}</option>
						{/each}
					</select>
				{:else if field.type === 'boolean'}
					<div class="flex items-center gap-2">
						<input
							id="{idPrefix}-{field.name}"
							type="checkbox"
							bind:checked={draft[field.name]}
							class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
						/>
						<label for="{idPrefix}-{field.name}" class="text-sm font-medium text-fg-muted">{field.label}</label>
					</div>
				{:else if field.type === 'number'}
					<label for="{idPrefix}-{field.name}" class={labelClass}>
						{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
					</label>
					<input
						id="{idPrefix}-{field.name}"
						type="number"
						bind:value={draft[field.name]}
						class="input font-mono tabular-nums"
						placeholder={field.default != null ? String(field.default) : ''}
						required={field.required}
					/>
				{:else}
					<label for="{idPrefix}-{field.name}" class={labelClass}>
						{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
					</label>
					<Input
						id="{idPrefix}-{field.name}"
						type={field.secret ? 'password' : 'text'}
						bind:value={draft[field.name]}
						placeholder={field.default != null ? String(field.default) : ''}
						required={field.required}
					/>
				{/if}
				{#if field.description}
					<p class="text-xs text-fg-subtle mt-1">{field.description}</p>
				{/if}
				{#if fieldHints[field.name]}
					<div class="mt-2">{@render fieldHints[field.name]()}</div>
				{/if}
			</div>
		{/each}
	</div>
{/snippet}

{#snippet behaviorFields()}
	<div class="space-y-4">
		<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
			<div>
				<label for="{idPrefix}-priority" class={labelClass}>Priority</label>
				<input id="{idPrefix}-priority" type="number" bind:value={draft.priority} class="input font-mono tabular-nums" min="1" />
			</div>
			<div>
				<label for="{idPrefix}-timeout" class={labelClass}>Timeout (seconds)</label>
				<input id="{idPrefix}-timeout" type="number" bind:value={draft.timeout_seconds} class="input font-mono tabular-nums" min="30" />
			</div>
		</div>
		{#if enabledPlacement === 'inline'}
			<div class="flex items-center gap-2">
				<input
					id="{idPrefix}-enabled"
					type="checkbox"
					bind:checked={draft.enabled}
					class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
				/>
				<label for="{idPrefix}-enabled" class="text-sm font-medium text-fg-muted">Enabled</label>
			</div>
		{/if}
	</div>
{/snippet}

<div class="space-y-5">
	{@render section('Identity', true, identityFields)}
	{#if fieldDescriptors.length > 0}
		{@render section('Connection', false, connectionFields)}
	{/if}
	{@render section('Behavior', false, behaviorFields)}
</div>
