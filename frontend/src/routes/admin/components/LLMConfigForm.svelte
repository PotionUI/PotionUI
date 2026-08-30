<script module lang="ts">
	export type LLMConfigFormData = {
		name: string;
		type: string;
		model: string;
		api_key: string;
		base_url: string;
		enabled: boolean;
		supports_vision: boolean;
		disable_system_prompt: boolean;
		memory_reflection: boolean;
		system_message: string;
		temperature: number;
		max_tokens: number;
		timeout: number;
		provider_options: Record<string, any>;
	};
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { PreChatAction } from '$lib/types/llm';
	import { Badge, Input } from '$lib/components/ui';
	import { coerceProviderOptionText } from './llmProviderOptions';

	/**
	 * Shared Model / Prompting / Sampling / Capabilities / Ollama Options /
	 * Pre-Chat Actions field set for both the create modal and the detail-pane
	 * edit form on the LLM Configurations admin tab. `mode` only affects the
	 * submit-adjacent bits the caller renders around this (title, footer
	 * button); this component is otherwise mode-agnostic — the caller owns
	 * dirty tracking, save/discard, and API calls.
	 *
	 * `layout="panel"` renders each group as a bordered/raised `<section>`
	 * (the wide detail pane); `layout="plain"` renders `border-t`-divided
	 * groups with mono micro-labels (the narrower create modal) — same
	 * vocabulary the two call sites already used before this was one component.
	 */
	let {
		draft = $bindable(),
		mode,
		layout = 'plain',
		idPrefix,
		apiKeySet = false,
		preChatActions = []
	}: {
		draft: LLMConfigFormData;
		mode: 'create' | 'edit';
		layout?: 'panel' | 'plain';
		idPrefix: string;
		apiKeySet?: boolean;
		preChatActions?: PreChatAction[];
	} = $props();

	const llmTypes = [
		{ value: 'openai', label: 'OpenAI' },
		{ value: 'anthropic', label: 'Anthropic' },
		{ value: 'ollama', label: 'Ollama' },
		{ value: 'local', label: 'Local' }
	];

	// Ollama-specific options with defaults and descriptions
	const ollamaOptions = {
		keep_alive: { default: '0', type: 'text', label: 'Keep Alive', description: 'How long to keep model in memory (e.g., "5m", "1h", -1 for indefinite, 0 to unload)' },
		num_gpu: { default: null, type: 'number', label: 'GPU Layers', description: 'Number of GPU layers to use (leave empty for auto)' },
		num_thread: { default: null, type: 'number', label: 'CPU Threads', description: 'Number of CPU threads (leave empty for auto)' },
		num_ctx: { default: 2048, type: 'number', label: 'Context Size', description: 'Context window size in tokens' },
		num_batch: { default: 512, type: 'number', label: 'Batch Size', description: 'Batch size for prompt processing' },
		seed: { default: null, type: 'number', label: 'Seed', description: 'Random seed for reproducibility (leave empty for random)' },
		top_k: { default: 40, type: 'number', label: 'Top-K', description: 'Limits token selection to top K options' },
		top_p: { default: 0.9, type: 'number', label: 'Top-P', description: 'Nucleus sampling threshold (0-1)' },
		repeat_penalty: { default: 1.1, type: 'number', label: 'Repeat Penalty', description: 'Penalty for repeating tokens (1.0 = no penalty)' },
		repeat_last_n: { default: 64, type: 'number', label: 'Repeat Window', description: 'Tokens to look back for repeat penalty' },
		mirostat: { default: 0, type: 'select', label: 'Mirostat', description: 'Mirostat sampling mode', options: [
			{ value: 0, label: 'Disabled' },
			{ value: 1, label: 'Mirostat v1' },
			{ value: 2, label: 'Mirostat v2' }
		]},
		mirostat_tau: { default: 5.0, type: 'number', label: 'Mirostat Tau', description: 'Target entropy for Mirostat' },
		mirostat_eta: { default: 0.1, type: 'number', label: 'Mirostat Eta', description: 'Learning rate for Mirostat' },
		think: { default: true, type: 'select', label: 'Thinking Mode', description: 'Enable/disable reasoning/thinking (disable for faster responses)', options: [
			{ value: true, label: 'Enabled' },
			{ value: false, label: 'Disabled' }
		]},
		force_prompt_tools: { default: false, type: 'select', label: 'Force Prompt Tools', description: 'Inject tool definitions into system prompt instead of native tool calling. Enable this for models where native tools don\'t work (e.g. Gemma 4).', options: [
			{ value: false, label: 'Disabled (native)' },
			{ value: true, label: 'Enabled (prompt injection)' }
		]}
	};

	const isPanel = $derived(layout === 'panel');
	// Regular label in the wide pane; mono micro-label (`.label`, app.css) in the modal.
	const labelClass = $derived(isPanel ? 'block text-sm font-medium text-fg-muted mb-1' : 'label');

	function setProviderOption(key: string, value: any, isDefault: boolean) {
		if (isDefault) {
			delete draft.provider_options[key];
		} else {
			draft.provider_options[key] = value;
		}
		draft.provider_options = { ...draft.provider_options };
	}

	function togglePreChatAction(actionId: string, checked: boolean) {
		if (!draft.provider_options.pre_chat_actions) {
			draft.provider_options.pre_chat_actions = {};
		}
		draft.provider_options.pre_chat_actions[actionId] = checked;
		draft.provider_options = { ...draft.provider_options };
	}
</script>

{#snippet section(title: string, isFirst: boolean, body: Snippet, headerExtra?: Snippet)}
	{#if isPanel}
		<section class="rounded-lg border border-line bg-surface-1 shadow-raised">
			<div class="px-4 sm:px-5 py-3 border-b border-line flex items-center justify-between gap-3">
				<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">{title}</h3>
				{#if headerExtra}{@render headerExtra()}{/if}
			</div>
			<div class="px-4 sm:px-5 py-4">
				{@render body()}
			</div>
		</section>
	{:else}
		<div class={isFirst ? '' : 'border-t border-line pt-5'}>
			<div class="flex items-center justify-between gap-3 mb-2.5">
				<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{title}</h3>
				{#if headerExtra}{@render headerExtra()}{/if}
			</div>
			{@render body()}
		</div>
	{/if}
{/snippet}

{#snippet modelFields()}
	<div class="space-y-4">
		<div>
			<label for="{idPrefix}-name" class={labelClass}>Name <span class="text-danger">*</span></label>
			<Input id="{idPrefix}-name" type="text" bind:value={draft.name} placeholder="e.g. GPT-4o, Claude Sonnet" />
		</div>
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			<div>
				<label for="{idPrefix}-type" class={labelClass}>Type</label>
				<select id="{idPrefix}-type" class="input" bind:value={draft.type}>
					{#each llmTypes as type}
						<option value={type.value}>{type.label}</option>
					{/each}
				</select>
			</div>
			<div>
				<label for="{idPrefix}-model" class={labelClass}>Model <span class="text-danger">*</span></label>
				<Input id="{idPrefix}-model" type="text" bind:value={draft.model} placeholder="e.g. gpt-4o, claude-sonnet-4-20250514" />
			</div>
		</div>
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			<div>
				<label for="{idPrefix}-api-key" class={labelClass}>API Key</label>
				<Input
					id="{idPrefix}-api-key"
					type="password"
					bind:value={draft.api_key}
					placeholder={apiKeySet ? 'Stored — leave blank to keep' : 'Optional'}
				/>
			</div>
			<div>
				<label for="{idPrefix}-base-url" class={labelClass}>Base URL</label>
				<Input id="{idPrefix}-base-url" type="text" bind:value={draft.base_url} placeholder="e.g. http://localhost:11434" />
			</div>
		</div>
	</div>
{/snippet}

{#snippet promptingHeaderExtra()}
	<label class="flex items-center gap-2 cursor-pointer">
		<input
			type="checkbox"
			class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
			bind:checked={draft.disable_system_prompt}
		/>
		<span class="text-xs text-fg-muted">Disable system prompt</span>
	</label>
{/snippet}

{#snippet promptingFields()}
	<textarea
		class="input font-mono text-sm transition-opacity"
		class:opacity-40={draft.disable_system_prompt}
		bind:value={draft.system_message}
		rows="6"
		disabled={draft.disable_system_prompt}
	></textarea>
	{#if draft.disable_system_prompt}
		<p class="text-xs text-warning/70 mt-1">System prompt will not be sent to the model. Tool-provided system messages will still be used.</p>
	{/if}
{/snippet}

{#snippet samplingFields()}
	<div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
		<div>
			<label for="{idPrefix}-temperature" class={labelClass}>Temperature</label>
			<input id="{idPrefix}-temperature" type="number" class="input font-mono tabular-nums" bind:value={draft.temperature} min="0" max="2" step="0.1" />
		</div>
		<div>
			<label for="{idPrefix}-max-tokens" class={labelClass}>Max Tokens</label>
			<input id="{idPrefix}-max-tokens" type="number" class="input font-mono tabular-nums" bind:value={draft.max_tokens} min="1" max="4096" />
		</div>
		<div>
			<label for="{idPrefix}-timeout" class={labelClass}>Timeout (s)</label>
			<input id="{idPrefix}-timeout" type="number" class="input font-mono tabular-nums" bind:value={draft.timeout} min="1" max="300" />
		</div>
	</div>
{/snippet}

{#snippet capabilityFields()}
	<div class="flex flex-wrap items-center gap-x-6 gap-y-2">
		<div class="flex items-center gap-2">
			<input
				id="{idPrefix}-enabled"
				type="checkbox"
				class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
				bind:checked={draft.enabled}
			/>
			<label for="{idPrefix}-enabled" class="text-sm font-medium text-fg-muted">Enabled</label>
		</div>
		<div class="flex items-center gap-2">
			<input
				id="{idPrefix}-vision"
				type="checkbox"
				class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
				bind:checked={draft.supports_vision}
			/>
			<label for="{idPrefix}-vision" class="text-sm font-medium text-fg-muted">Supports Vision</label>
		</div>
		<div class="flex items-center gap-2">
			<input
				id="{idPrefix}-memory-reflection"
				type="checkbox"
				class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
				bind:checked={draft.memory_reflection}
			/>
			<label for="{idPrefix}-memory-reflection" class="text-sm font-medium text-fg-muted">Memory Reflection</label>
		</div>
	</div>
	<p class="text-xs text-fg-subtle mt-1">
		Memory reflection extracts durable facts from the conversation into memory notes — costs one extra LLM call per conversation.
	</p>
{/snippet}

{#snippet ollamaFields()}
	<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
		{#each Object.entries(ollamaOptions) as [key, opt]}
			<div>
				<label for="{idPrefix}-ollama-{key}" class={labelClass}>{opt.label}</label>
				{#if opt.type === 'select' && 'options' in opt}
					<select
						id="{idPrefix}-ollama-{key}"
						class="input text-sm"
						value={draft.provider_options[key] ?? opt.default}
						onchange={(e) => {
							const target = e.target as HTMLSelectElement;
							let value: any = target.value;
							if (value === 'true') value = true;
							else if (value === 'false') value = false;
							else if (!isNaN(Number(value))) value = Number(value);
							setProviderOption(key, value, value === opt.default);
						}}
					>
						{#each opt.options as option}
							<option value={option.value}>{option.label}</option>
						{/each}
					</select>
				{:else if opt.type === 'number'}
					<input
						id="{idPrefix}-ollama-{key}"
						type="number"
						class="input text-sm"
						value={draft.provider_options[key] ?? ''}
						placeholder={opt.default !== null ? String(opt.default) : 'auto'}
						step={key.includes('penalty') || key === 'top_p' || key === 'mirostat_eta' || key === 'mirostat_tau' ? '0.1' : '1'}
						oninput={(e) => {
							const value = (e.target as HTMLInputElement).value;
							if (value === '') {
								setProviderOption(key, undefined, true);
							} else {
								setProviderOption(key, parseFloat(value), false);
							}
						}}
					/>
				{:else}
					<input
						id="{idPrefix}-ollama-{key}"
						type="text"
						class="input text-sm"
						value={draft.provider_options[key] ?? ''}
						placeholder={opt.default !== null ? String(opt.default) : ''}
						oninput={(e) => {
							const value = (e.target as HTMLInputElement).value;
							if (value === '') {
								setProviderOption(key, undefined, true);
							} else {
								setProviderOption(key, coerceProviderOptionText(value), false);
							}
						}}
					/>
				{/if}
				<p class="text-xs text-fg-subtle mt-1">{opt.description}</p>
			</div>
		{/each}
	</div>
{/snippet}

{#snippet preChatFields()}
	<p class="text-xs text-fg-subtle mb-3">Actions that run before each LLM call (e.g., freeing GPU memory)</p>
	<div class="space-y-3">
		{#each preChatActions as action (action.id)}
			<label class="flex items-start gap-3 p-3 {isPanel ? 'bg-surface-2/60' : 'bg-surface-1'} rounded-lg cursor-pointer hover:bg-surface-3/50 transition-colors">
				<input
					type="checkbox"
					class="mt-0.5 h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal"
					checked={draft.provider_options.pre_chat_actions?.[action.id] ?? action.default_enabled}
					onchange={(e) => togglePreChatAction(action.id, (e.target as HTMLInputElement).checked)}
				/>
				<div class="flex-1 min-w-0">
					<div class="flex items-center gap-2">
						<span class="text-sm font-medium text-fg">{action.name}</span>
						{#if action.blocking}
							<Badge variant="danger" size="sm" class="uppercase font-mono">Blocking</Badge>
						{/if}
						<Badge variant="neutral" size="sm" class="uppercase font-mono">{action.category}</Badge>
					</div>
					<p class="text-xs text-fg-subtle mt-0.5">{action.description}</p>
				</div>
			</label>
		{/each}
	</div>
{/snippet}

<div class="space-y-5">
	{@render section('Model', true, modelFields)}
	{@render section('Prompting', false, promptingFields, promptingHeaderExtra)}
	{@render section('Sampling', false, samplingFields)}
	{@render section('Capabilities', false, capabilityFields)}
	{#if draft.type === 'ollama'}
		{@render section('Ollama Options', false, ollamaFields)}
	{/if}
	{#if preChatActions.length > 0}
		{@render section('Pre-Chat Actions', false, preChatFields)}
	{/if}
</div>
