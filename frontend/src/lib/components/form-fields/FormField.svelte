<script lang="ts">
	import { getContext } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import { resolveFieldComponent } from '$lib/fields/registry';
	import { FORM_FIELD_ERRORS_CONTEXT_KEY } from '$lib/form/fieldErrorsContext';
	import {
		FORM_FIELD_ERROR_ACTIONS_CONTEXT_KEY,
		applyFieldErrorFix,
		type FormFieldErrorActions
	} from '$lib/form/fieldErrorActionsContext';
	import {
		deriveFieldErrorFix,
		formatFieldErrorFixLabel,
		type FieldErrorFix
	} from '$lib/utils/formValidationErrors';

	export let name: string | null;
	export let config: any;
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	// Passed through to every field type uniformly, like `onChange` - only
	// MediaLoaderField uses them today.
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	// Structural identity of this field within the schema tree (row/section/…
	// segments joined by "/"), used by SectionField.svelte to key its
	// persisted fold state. `undefined` for leaf fields, which ignore it.
	export let fieldPath: string | undefined = undefined;

	const fieldType = config.type;

	// "tab" entries are rendered by their parent TabsField (which iterates
	// `config.children` directly) - FormField never dispatches for them.
	$: isTabEntry = fieldType === 'tab';

	// Plugin-resolved components additionally receive the `window.__potionui`
	// bridge object so they can reach host-provided primitives/registries.
	$: host = typeof window !== 'undefined' ? (window as any).__potionui : undefined;

	// Check if field should be hidden
	$: isVisible = config.visible !== false;

	$: componentPromise = isTabEntry ? Promise.resolve(null) : resolveFieldComponent(fieldType);

	// Falls back to an empty, never-updated store when rendered outside a
	// DynamicForm (e.g. in isolation/tests) so `$fieldErrorsStore` is always safe.
	const fieldErrorsStore =
		getContext<Writable<Record<string, string[]>>>(FORM_FIELD_ERRORS_CONTEXT_KEY) ??
		writable<Record<string, string[]>>({});
	$: fieldErrorMessages = name ? ($fieldErrorsStore[name] ?? []) : [];

	// Quick-fixes need a form to write into; absent the actions context (field
	// rendered in isolation) the messages stay read-only.
	const errorActions = getContext<FormFieldErrorActions | undefined>(
		FORM_FIELD_ERROR_ACTIONS_CONTEXT_KEY
	);
	$: errorFix = name && errorActions ? deriveFieldErrorFix(name, fieldErrorMessages) : null;

	function applyFix(fix: FieldErrorFix | null) {
		if (fix && errorActions) applyFieldErrorFix(fix, errorActions);
	}
</script>

{#if isVisible && !isTabEntry}
	<div data-field-name={name || undefined}>
		{#await componentPromise then Component}
			{#if Component}
				<svelte:component this={Component} {name} {config} {value} {onChange} {onOriginChange} {onMaskChange} {host} {fieldPath} />
			{:else}
				<div class="p-2 bg-danger/10 rounded-md text-xs text-danger">
					Unsupported field type: {fieldType}
				</div>
			{/if}
		{/await}
		{#if fieldErrorMessages.length > 0}
			<div class="mt-1 space-y-1.5">
				<div class="space-y-0.5" role="alert">
					{#each fieldErrorMessages as msg}
						<p class="text-xs text-danger">{msg}</p>
					{/each}
				</div>
				{#if errorFix}
					<button
						type="button"
						aria-label={formatFieldErrorFixLabel(errorFix)}
						class="inline-flex items-center gap-1.5 rounded border border-signal/40 bg-signal/10 px-2.5 py-1 text-xs font-medium text-signal transition-colors hover:bg-signal/15 focus:outline-none focus-visible:ring-1 focus-visible:ring-signal"
						on:click={() => applyFix(errorFix)}
					>
						{errorFix.verb}
						<span class="font-mono tabular-nums">{errorFix.valueLabel}</span>
					</button>
				{/if}
			</div>
		{/if}
	</div>
{/if}
