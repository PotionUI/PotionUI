<script lang="ts">
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Input, Switch, IconButton } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { effectiveKind, isListOperator } from '$lib/organize/draft';
	import { mediaKindIcon } from '$lib/organize/icons';
	import type { OrganizeFactSpec, OrganizeOption, OrganizeSubject } from '$lib/types/organize';
	import FactModelPicker from './FactModelPicker.svelte';
	import TagNamesInput from './TagNamesInput.svelte';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';

	let {
		spec,
		operator,
		value,
		subject,
		onchange
	}: {
		spec: OrganizeFactSpec;
		operator: string;
		value: unknown;
		subject: OrganizeSubject;
		onchange: (next: unknown) => void;
	} = $props();

	const kind = $derived(effectiveKind(spec));
	const list = $derived(isListOperator(operator));
	const CUSTOM = '__custom__';

	let remote = $state<OrganizeOption[]>([]);
	let remoteFor = $state('');

	$effect(() => {
		if (kind !== 'enum' || !spec.has_options_endpoint) return;
		const key = `${subject}:${spec.key}`;
		if (remoteFor === key) return;
		remoteFor = key;
		void api
			.getOrganizeFactOptions(spec.key, { subject, limit: 100 })
			.then((response) => {
				remote = response.success && Array.isArray(response.data) ? response.data : [];
			})
			.catch(() => {
				remote = [];
			});
	});

	const enumOptions = $derived.by(() => {
		const seen = new Set<string>();
		const out: OrganizeOption[] = [];
		for (const option of [...(spec.options ?? []), ...remote]) {
			if (seen.has(option.value)) continue;
			seen.add(option.value);
			out.push(option);
		}
		return out;
	});

	const selectedList = $derived(Array.isArray(value) ? (value as string[]) : []);
	const presets = $derived(spec.picker?.presets ?? []);
	const sizeValue = $derived((value ?? {}) as { width?: number; height?: number });
	const presetMatch = $derived(
		presets.find((p) => p.width === sizeValue.width && p.height === sizeValue.height)
	);
	let customSize = $state(false);
	const showCustomSize = $derived(customSize || (!presetMatch && presets.length > 0 && !!sizeValue.width) || presets.length === 0);

	function toggleChip(optionValue: string) {
		if (list) {
			onchange(
				selectedList.includes(optionValue)
					? selectedList.filter((v) => v !== optionValue)
					: [...selectedList, optionValue]
			);
		} else {
			onchange(optionValue);
		}
	}

	function chipOn(optionValue: string): boolean {
		return list ? selectedList.includes(optionValue) : value === optionValue;
	}

	function handleSizePreset(next: string) {
		if (next === CUSTOM) {
			customSize = true;
			return;
		}
		customSize = false;
		const preset = presets.find((p) => `${p.width}x${p.height}` === next);
		if (preset) onchange({ width: preset.width, height: preset.height });
	}

	function setDimension(which: 'width' | 'height', raw: string) {
		const n = Math.max(0, Math.round(Number(raw) || 0));
		onchange({ ...sizeValue, [which]: n });
	}

	function loadTagOptions(query: string): Promise<OrganizeOption[]> {
		return api
			.getOrganizeFactOptions(spec.key, { subject, q: query, limit: 20 })
			.then((response) => (response.success && Array.isArray(response.data) ? response.data : []));
	}

	function labelFor(optionValue: string): string {
		return enumOptions.find((o) => o.value === optionValue)?.label ?? (spec.key === 'mode' ? modeLabel(optionValue) : optionValue);
	}
</script>

<div class="min-w-0 flex-1" data-kind={kind} data-fact={spec.key}>
	{#if kind === 'model_ref'}
		<FactModelPicker
			{value}
			multi={list}
			modelTypes={spec.picker?.model_types ?? []}
			onchange={(next) => onchange(next)}
		/>
	{:else if kind === 'enum'}
		{#if enumOptions.length > 0 && enumOptions.length <= 6}
			<div class="flex w-full flex-wrap gap-1.5" role="group" aria-label={spec.label}>
				{#each enumOptions as option (option.value)}
					<button
						type="button"
						aria-pressed={chipOn(option.value)}
						class="inline-flex h-7 flex-shrink-0 items-center gap-1.5 rounded border px-2 text-xs transition-colors {chipOn(option.value)
							? 'border-signal/30 bg-signal/10 text-signal'
							: 'border-line-strong bg-surface-2 text-fg-muted hover:text-fg'}"
						onclick={() => toggleChip(option.value)}
					>
						{#if spec.key === 'media_kind' && mediaKindIcon(option.value)}
							<Icon name={mediaKindIcon(option.value) ?? ''} className="w-3.5 h-3.5" />
						{/if}
						{option.label}
					</button>
				{/each}
			</div>
		{:else if list}
			<div class="flex flex-wrap items-center gap-1.5">
				{#each selectedList as picked (picked)}
					<span class="inline-flex h-7 items-center gap-1 rounded border border-line-strong bg-surface-2 pl-2 pr-1 text-xs text-fg">
						{labelFor(picked)}
						<IconButton
							icon="close"
							label="Remove {labelFor(picked)}"
							size="xs"
							onclick={() => onchange(selectedList.filter((v) => v !== picked))}
						/>
					</span>
				{/each}
				<div class="min-w-[12rem]">
					<CustomSelect
						value=""
						searchable
						size="sm"
						placeholder="Add {spec.label.toLowerCase()}"
						options={enumOptions.filter((o) => !selectedList.includes(o.value))}
						on:change={(event) => event.detail && onchange([...selectedList, event.detail])}
					/>
				</div>
			</div>
		{:else}
			<CustomSelect
				value={typeof value === 'string' ? value : ''}
				searchable
				placeholder="Choose {spec.label.toLowerCase()}"
				options={enumOptions}
				on:change={(event) => onchange(event.detail)}
			/>
		{/if}
	{:else if kind === 'size'}
		<div class="flex flex-wrap items-center gap-2">
			{#if presets.length > 0}
				<div class="min-w-[12rem]">
					<CustomSelect
						value={showCustomSize ? CUSTOM : presetMatch ? `${presetMatch.width}x${presetMatch.height}` : ''}
						placeholder="Choose a size"
						options={[
							...presets.map((p) => ({ value: `${p.width}x${p.height}`, label: `${p.label}  ${p.width} x ${p.height}` })),
							{ value: CUSTOM, label: 'Custom size' }
						]}
						on:change={(event) => handleSizePreset(event.detail)}
					/>
				</div>
			{/if}
			{#if showCustomSize}
				<div class="flex items-center gap-1.5 font-mono tabular-nums">
					<input
						type="number"
						min="1"
						class="input w-24"
						aria-label="Width"
						value={sizeValue.width ?? ''}
						oninput={(event) => setDimension('width', event.currentTarget.value)}
					/>
					<span class="text-fg-subtle">x</span>
					<input
						type="number"
						min="1"
						class="input w-24"
						aria-label="Height"
						value={sizeValue.height ?? ''}
						oninput={(event) => setDimension('height', event.currentTarget.value)}
					/>
				</div>
			{/if}
		</div>
	{:else if kind === 'number'}
		<div class="flex items-center gap-2">
			<input
				type="number"
				class="input w-32 font-mono tabular-nums"
				aria-label={spec.label}
				min={spec.picker?.min}
				max={spec.picker?.max}
				step={spec.picker?.step ?? 1}
				value={typeof value === 'number' ? value : ''}
				oninput={(event) => onchange(event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
			/>
			{#if spec.picker?.unit}<span class="text-xs text-fg-subtle">{spec.picker.unit}</span>{/if}
		</div>
	{:else if kind === 'tag_list'}
		<TagNamesInput
			value={Array.isArray(value) ? (value as string[]) : []}
			loadOptions={spec.has_options_endpoint ? loadTagOptions : undefined}
			onchange={(next) => onchange(next)}
		/>
	{:else if kind === 'attribute'}
		<span class="sr-only">{spec.label}</span>
	{:else if kind === 'bool'}
		<Switch label={spec.label} checked={value === true} onchange={(next) => onchange(next)} />
	{:else}
		<Input
			value={typeof value === 'string' ? value : ''}
			placeholder={spec.picker?.placeholder ?? ''}
			aria-label={spec.label}
			oninput={(event: Event) => onchange((event.currentTarget as HTMLInputElement).value)}
		/>
	{/if}
</div>
