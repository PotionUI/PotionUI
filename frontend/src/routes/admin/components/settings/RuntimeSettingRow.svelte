<script lang="ts">
	import { Badge, Input, SegmentedControl, Switch } from '$lib/components/ui';
	import {
		appliesLabel,
		parseRuntimeNumber,
		runtimeSettingValue,
		type RuntimeSettingDescriptor
	} from './runtimeSettings';

	let {
		descriptor,
		settings,
		onSettingChange,
		disabled = false,
		busy = false,
		locked = false
	}: {
		descriptor: RuntimeSettingDescriptor;
		settings: Record<string, any>;
		onSettingChange: (key: string, value: unknown) => void;
		disabled?: boolean;
		busy?: boolean;
		locked?: boolean;
	} = $props();

	let id = $derived(`runtime-${descriptor.key.replaceAll('_', '-')}`);
	let value = $derived(runtimeSettingValue(descriptor, settings));
	let applies = $derived(appliesLabel(descriptor.applies));

	let isDisabled = $derived(disabled || busy || locked);

	function handleNumberChange(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const parsed = parseRuntimeNumber(descriptor, input.value);
		if (parsed === null) {
			input.value = String(value);
			return;
		}
		if (parsed !== value) onSettingChange(descriptor.key, parsed);
	}
</script>

<div
	class="py-4 flex items-start justify-between gap-6 {descriptor.parent ? 'pl-6' : ''}"
	data-testid="runtime-setting-{descriptor.key}"
>
	<div class="min-w-0 {disabled ? 'opacity-60' : ''}">
		{#if descriptor.kind === 'choice' && descriptor.control !== 'select'}
			<p class="text-sm font-medium text-fg mb-1">{descriptor.label}</p>
		{:else}
			<label for={id} class="block text-sm font-medium text-fg mb-1">{descriptor.label}</label>
		{/if}
		<p class="text-sm text-fg-muted">{descriptor.description}</p>
		{#if descriptor.details}
			<p class="text-sm text-fg-muted mt-2">{descriptor.details}</p>
		{/if}
		<p class="text-sm text-fg-muted mt-2">{descriptor.guidance}</p>
		<div class="mt-2 flex flex-wrap items-center gap-2">
			<span class="text-2xs text-fg-subtle">Default: {descriptor.defaultLabel}</span>
			{#if applies}
				<Badge variant={descriptor.applies === 'restart' ? 'warning' : 'neutral'} size="sm">{applies}</Badge>
			{/if}
		</div>
	</div>
	{#if descriptor.kind === 'bool'}
		<Switch
			{id}
			label={descriptor.label}
			checked={value === true}
			disabled={disabled || locked}
			{busy}
			onchange={(checked) => onSettingChange(descriptor.key, checked)}
		/>
	{:else if descriptor.kind === 'choice' && descriptor.control === 'select'}
		<select
			{id}
			class="input text-sm font-mono py-1 flex-shrink-0"
			value={String(value)}
			disabled={isDisabled}
			onchange={(e) => onSettingChange(descriptor.key, (e.currentTarget as HTMLSelectElement).value)}
		>
			{#each descriptor.choices ?? [] as choice (choice.value)}
				<option value={choice.value}>{choice.label}</option>
			{/each}
		</select>
	{:else if descriptor.kind === 'choice'}
		<div class="flex-shrink-0">
			<SegmentedControl
				variant="toggle"
				ariaLabel={descriptor.label}
				items={(descriptor.choices ?? []).map((c) => ({ id: c.value, label: c.label, disabled: isDisabled }))}
				selected={String(value)}
				onSelect={(choice) => onSettingChange(descriptor.key, choice)}
			/>
		</div>
	{:else}
		<div class="flex items-center gap-2 flex-shrink-0">
			<Input
				{id}
				type="number"
				min={descriptor.min}
				max={descriptor.max}
				step={descriptor.step}
				disabled={isDisabled}
				class="w-24 font-mono tabular-nums"
				value={String(value)}
				onchange={handleNumberChange}
			/>
			{#if descriptor.unit}
				<span class="font-mono tabular-nums text-sm text-fg-muted">{descriptor.unit}</span>
			{/if}
		</div>
	{/if}
</div>
