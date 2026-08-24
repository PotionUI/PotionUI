<script lang="ts">
	import { previewGenerationStore } from '$lib/stores/previewGeneration';
	import { phrasebookStore, selectedCount, activeCount } from '$lib/stores/phrasebook';
	import HelpButton from '$lib/components/HelpButton.svelte';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import { Badge, Button, Alert, Switch } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { TutorialSection } from '$lib/types/tutorial';
	import type { Segment } from '$lib/types/segments';

	// Self-contained: reads/writes previewGenerationStore + phrasebookStore
	// directly. Extracted verbatim from phrasebook/+page.svelte (the
	// "Generate Preview Images" section). The store's WebSocket connection is
	// owned by the page (connect/disconnect in onMount/onDestroy) so it
	// survives this panel mounting/unmounting as selection changes.
	export let categoryId: string;

	$: gen = $previewGenerationStore;
	$: current = $phrasebookStore;

	const previewTutorialContent: TutorialSection[] = [
		{
			title: 'What is Preview Generation?',
			content: 'Preview generation automatically creates images for your phrasebook values. Each value will get a preview image that helps users understand what selecting that option will produce.',
			icon: 'info'
		},
		{
			title: 'Step 1: Create a Session',
			content: 'Before generating previews, you need to **save a session** on the Generation page. Sessions store your generation settings (model, steps, CFG, etc.). Go to the Generation page, configure your settings, and click "Save Session".',
			variant: 'tip'
		},
		{
			title: 'Step 2: Select Values',
			content: 'Use the **checkboxes** in the values list to select which values should get preview images. You can use "Select All" or "Deselect All" to quickly manage selections. Only *active* values can be selected.',
			icon: 'check'
		},
		{
			title: 'Step 3: Configure the Template',
			content: 'The prompt template uses `<< value >>` as a placeholder. For example, if your template is "A photo of << value >>" and you have a value "sunset", the actual prompt becomes "A photo of sunset".',
			variant: 'tip'
		},
		{
			title: 'Understanding Generation',
			content: 'Previews are generated **sequentially** - one at a time. The process can take several minutes depending on how many values you selected. You can continue using other parts of the app while generation runs in the background.',
			variant: 'warning'
		}
	];

	function handleGeneratePreviews() {
		previewGenerationStore.handleGeneratePreviews(categoryId, current.selectedValueIds);
	}

	function handlePromptSegmentsChange(event: CustomEvent<Segment[]>) {
		previewGenerationStore.setPromptSegments(event.detail);
	}
</script>

<div class="mt-6 space-y-4">
	<div class="flex items-center justify-between">
		<div class="flex items-center gap-2">
			<Icon name="sparkles" className="w-4 h-4 text-signal" />
			<h3 class="text-sm font-medium text-fg">Generate Preview Images</h3>
		</div>
		<HelpButton title="Preview Generation Help" tutorialContent={previewTutorialContent} />
	</div>

	<div
		class="flex items-center justify-between gap-3"
		title="{$selectedCount} of {$activeCount} active values selected for preview generation"
	>
		<span class="font-mono text-xs tabular-nums text-fg-muted">
			{$selectedCount} selected &middot; {$activeCount} active
		</span>
		{#if $selectedCount === 0}
			<Badge variant="warning" size="sm">Select values to generate</Badge>
		{/if}
	</div>

	<!-- Target -->
	<div class="space-y-2">
		<span class="font-mono text-2xs font-semibold uppercase tracking-[0.13em] text-fg-subtle">Target</span>
		<div class="grid grid-cols-2 gap-3">
			<div>
				<label for="preview-preset" class="block text-xs text-fg-muted mb-1.5">Preset</label>
				<select
					id="preview-preset"
					class="input text-sm"
					value={gen.selectedPresetId}
					on:change={(e) => {
						previewGenerationStore.setSelectedPresetId(e.currentTarget.value);
						previewGenerationStore.handlePresetChange();
					}}
				>
					{#if gen.presets.length === 0}
						<option value="">No presets available</option>
					{:else}
						{#each gen.presets as preset}
							<option value={preset.id}>{preset.name}</option>
						{/each}
					{/if}
				</select>
			</div>
			<div>
				<label for="preview-session" class="block text-xs text-fg-muted mb-1.5">Session</label>
				<select
					id="preview-session"
					class="input text-sm"
					value={gen.selectedSessionId}
					on:change={(e) => previewGenerationStore.setSelectedSessionId(e.currentTarget.value)}
					disabled={!gen.selectedPresetId}
				>
					{#if gen.sessions.length === 0}
						<option value="">No sessions for this preset</option>
					{:else}
						{#each gen.sessions as session}
							<option value={session.id}>{session.name}</option>
						{/each}
					{/if}
				</select>
			</div>
		</div>
		<div>
			<label for="preview-mode" class="block text-xs text-fg-muted mb-1.5">Mode</label>
			<select
				id="preview-mode"
				class="input text-sm"
				value={gen.selectedMode}
				on:change={(e) => previewGenerationStore.setSelectedMode(e.currentTarget.value)}
				disabled={!gen.selectedPresetId || gen.modes.length === 0}
			>
				{#if gen.modes.length === 0}
					<option value="">No modes available</option>
				{:else}
					{#each gen.modes as mode}
						<option value={mode.name}>{mode.label}</option>
					{/each}
				{/if}
			</select>
		</div>
	</div>

	<!-- Prompt -->
	<div class="space-y-1.5">
		<span class="font-mono text-2xs font-semibold uppercase tracking-[0.13em] text-fg-subtle">Prompt</span>
		<SegmentedPromptEditor
			segments={gen.promptSegments}
			label="Prompt template"
			compact
			on:segmentsChange={handlePromptSegmentsChange}
		/>
		<div class="flex items-center gap-2">
			<code class="text-xs px-1.5 py-0.5 bg-signal/10 text-signal rounded font-mono">{'<< value >>'}</code>
			<span class="text-xs text-fg-subtle">= placeholder for phrasebook value</span>
		</div>
	</div>

	<!-- Advanced options (collapsible) -->
	<details class="group rounded-lg border border-line">
		<summary class="flex items-center gap-2 cursor-pointer px-3 py-2 text-xs text-fg-muted hover:text-fg transition-colors">
			<Icon name="chevron-right" className="w-4 h-4 transition-transform group-open:rotate-90" />
			Advanced options
		</summary>
		<div class="space-y-4 border-t border-line px-3 py-3">
			<!-- Negative prompt -->
			<div>
				<label for="preview-negative" class="block text-xs text-fg-muted mb-1.5">Negative Prompt</label>
				<textarea
					id="preview-negative"
					class="input font-mono text-sm"
					rows="2"
					placeholder="Leave empty to use session's negative prompt"
					value={gen.negativePrompt}
					on:input={(e) => previewGenerationStore.setNegativePrompt(e.currentTarget.value)}
				></textarea>
			</div>

			<!-- Seed options -->
			<div>
				<div class="flex items-center gap-2">
					<span class="text-sm text-fg-muted">Use fixed seed</span>
					<Switch
						checked={gen.useFixedSeed}
						onchange={(checked) => previewGenerationStore.setUseFixedSeed(checked)}
						label="Use fixed seed"
						size="sm"
					/>
				</div>
				{#if gen.useFixedSeed}
					<input
						type="number"
						class="input text-sm mt-2 tabular-nums"
						placeholder="Seed value"
						value={gen.fixedSeed}
						on:input={(e) => previewGenerationStore.setFixedSeed(parseInt(e.currentTarget.value, 10))}
					/>
				{/if}
				<p class="text-xs text-fg-subtle mt-1.5">
					{gen.useFixedSeed ? 'All previews will use the same seed' : 'Each preview gets a random seed'}
				</p>
			</div>
		</div>
	</details>

	<!-- Action Area -->
	<div class="pt-1">
		{#if gen.previewGenerationStatus}
			<Alert
				variant={gen.previewGenerationStatus.includes('Error') ? 'danger' : 'signal'}
				density="compact"
				class="mb-3"
			>
				{gen.previewGenerationStatus}
			</Alert>
		{/if}

		<Button
			variant="primary"
			icon="sparkles"
			class="w-full"
			loading={gen.isGeneratingPreviews}
			disabled={!gen.selectedSessionId || !gen.selectedMode || gen.sessions.length === 0 || $selectedCount === 0}
			onclick={handleGeneratePreviews}
		>
			{#if gen.isGeneratingPreviews}
				Generating&hellip;
			{:else}
				Generate <span class="tabular-nums">{$selectedCount}</span> preview{$selectedCount !== 1 ? 's' : ''}
			{/if}
		</Button>
	</div>
</div>
