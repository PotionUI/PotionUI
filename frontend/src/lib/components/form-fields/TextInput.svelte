<script lang="ts">
	import { getContext } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import FieldShell from './FieldShell.svelte';
	import { FORM_FIELD_ERRORS_CONTEXT_KEY } from '$lib/form/fieldErrorsContext';
	import { patternViolation } from '$lib/form/textPattern';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	const serverErrors =
		getContext<Writable<Record<string, string[]>>>(FORM_FIELD_ERRORS_CONTEXT_KEY) ??
		writable<Record<string, string[]>>({});

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: disabled = config.disabled ?? false;
	$: tooltip = config.tooltip || config.configuration?.tooltip;
	$: multiline = (config.input_type ?? config.configuration?.input_type) === 'textarea';
	$: rows = Number(config.rows ?? config.configuration?.rows) || 4;
	$: placeholder = config.placeholder ?? config.configuration?.placeholder ?? '';
	$: mono = Boolean(config.mono ?? config.configuration?.mono);
	$: pattern = config.pattern ?? config.configuration?.pattern;
	$: patternMessage = config.pattern_message ?? config.configuration?.pattern_message;
	$: hasServerError = !!name && ($serverErrors[name]?.length ?? 0) > 0;
	$: patternError = hasServerError ? null : patternViolation(value, pattern, patternMessage);
	const monoClass = 'font-mono text-[13px] tabular-nums whitespace-pre overflow-x-auto';
	$: errorId = name ? `${name}-pattern-error` : undefined;

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement | HTMLTextAreaElement;
		if (name) {
			onChange(name, target.value);
		}
	}

	function describedBy(descriptionId: string | undefined): string | undefined {
		const ids = [descriptionId, patternError ? errorId : undefined].filter(Boolean);
		return ids.length ? ids.join(' ') : undefined;
	}
</script>

<FieldShell {name} {label} {description} {tooltip} labelFor={name || undefined} let:descriptionId>
	{#if multiline}
		<textarea
			id={name || undefined}
			{value}
			on:input={handleInput}
			{disabled}
			{rows}
			{placeholder}
			spellcheck={mono ? false : undefined}
			class="input textarea {mono ? monoClass : ''}"
			aria-invalid={patternError ? true : undefined}
			aria-describedby={describedBy(descriptionId)}
		></textarea>
	{:else}
		<input
			type="text"
			id={name || undefined}
			{value}
			on:input={handleInput}
			{disabled}
			{placeholder}
			spellcheck={mono ? false : undefined}
			class="input {mono ? monoClass : ''}"
			aria-invalid={patternError ? true : undefined}
			aria-describedby={describedBy(descriptionId)}
		/>
	{/if}
	{#if patternError}
		<p id={errorId} class="mt-1 text-xs text-danger" data-pattern-error>{patternError}</p>
	{/if}
</FieldShell>

<style>
	.textarea {
		resize: vertical;
		min-height: 4.5rem;
		height: auto;
		line-height: 1.5;
	}
</style>
