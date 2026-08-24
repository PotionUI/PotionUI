<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import { api } from '$lib/services/api/index';

	export let name: string;
	export let config: any;
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	const label = config.title || name || '';
	const description = config.description || '';
	const configuration = config.configuration || {};
	const tooltip = config.tooltip || undefined;
	const allowEmpty = configuration.allow_empty !== false; // Default to true

	// State for loaded LLM configurations
	let llmConfigs: any[] = [];
	let isLoadingLLMs = true;

	// Initialize value structure
	$: currentValue = value || {
		llm_id: config.default_llm_id || '',
		prompt: ''
	};

	// Format LLM options with optional "-- None --" at the beginning
	$: llmOptions = (() => {
		const opts = llmConfigs.map(cfg => ({
			value: cfg.id,
			label: `${cfg.name} (${cfg.type})`,
			supports_vision: cfg.supports_vision
		}));

		if (allowEmpty) {
			return [
				{ value: '', label: '-- None --', supports_vision: false },
				...opts
			];
		}
		return opts;
	})();

	// Load user's LLM configurations on mount
	onMount(async () => {
		try {
			const response = await api.getMyLLMConfigurations();
			if (response.success) {
				const allConfigs = response.data?.llm_configs || [];
				llmConfigs = allConfigs.filter((c: any) => c.enabled);
			}
		} catch (err) {
			logger.error('Failed to load LLM configurations:', err);
		} finally {
			isLoadingLLMs = false;
		}
	});

	function handleChange(field: string, fieldValue: any) {
		const newValue = { ...currentValue, [field]: fieldValue };
		onChange(name, newValue);
	}
</script>

<div class="field-card space-y-3">
	{#if label}
		<div class="flex items-center gap-2">
			<label class="label !mb-0">
				{label}
			</label>
			{#if tooltip}
				<Tooltip text={tooltip} position="top">
					<span class="text-fg-subtle cursor-help inline-flex items-center">
						<Icon name="info" className="w-3.5 h-3.5" />
					</span>
				</Tooltip>
			{/if}
		</div>
	{/if}

	{#if description}
		<p class="text-xs text-fg-muted mb-2">{description}</p>
	{/if}

	<div class="space-y-2 p-2.5 border border-line-strong rounded-lg bg-surface-2">
		{#if configuration.show_llm_select !== false}
			<div>
				<label id="{name}-llm-label" class="block text-xs font-medium text-fg-muted mb-1">LLM Configuration</label>
				<div aria-labelledby="{name}-llm-label">
					<CustomSelect
						bind:value={currentValue.llm_id}
						options={llmOptions}
						placeholder="Select LLM configuration..."
						on:change={(e) => handleChange('llm_id', e.detail)}
					/>
				</div>
			</div>
		{/if}

		{#if configuration.show_prompt !== false}
			<div>
				<label for="{name}-prompt" class="block text-xs font-medium text-fg-muted mb-1">Expansion Instruction</label>
				<textarea
					id="{name}-prompt"
					bind:value={currentValue.prompt}
					on:input={(e) => handleChange('prompt', e.currentTarget.value)}
					placeholder="Enter expansion instruction..."
					class="input resize-none bg-canvas"
					rows="3"
				></textarea>
			</div>
		{/if}
	</div>
</div>
