<script lang="ts">
	import { Button } from '$lib/components/ui';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import FavoriteButton from '$lib/components/FavoriteButton.svelte';
	import InlineChip from '$lib/components/InlineChip.svelte';
	import StarRating from '$lib/components/StarRating.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PresetPicker from '$lib/components/preset/PresetPicker.svelte';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import GenerationLayoutPicker from '$lib/components/layout/GenerationLayoutPicker.svelte';
	import GenerationPanel from '$lib/components/GenerationPanel.svelte';
	import type { Session } from '$lib/types/api';
	import type { GenerationLayoutMode } from '$lib/stores/generationLayout';
	import type { GenerationState } from '$lib/types/tabs';
	import type { ChipData } from '$lib/types/segments';
	import ComponentExample from './ComponentExample.svelte';

	let rating = 3;
	let favorite = false;
	let selectedEngine = 'native';
	let selectedPreset = 'potion-xl';
	let demoLayout: GenerationLayoutMode = 'two';
	let demoSessionId = 'session-1';
	let demoSessionDirty = true;
	let demoAutoSave = false;
	let demoAutoSaveInterval = 10000;
	let demoLastSaved = new Date();
	let demoGenerating = true;
	let demoGeneration: GenerationState = {
		isGenerating: true,
		currentGeneration: { id: 'kit-generation', status: 'running', progress: 0.64 },
		currentProgress: {
			step: 'sampling',
			current_step: 'Sampling latent image',
			message: '[PIPE:KSampler] [STEP:19/30]',
			progress: 0.64
		},
		pipeTimers: {},
		startedAt: Date.now() - 18000,
		totalTime: null,
		lastDurationMs: 12400,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [
			{ generation_id: 'gen_demo_running', queue_position: null, status: 'running' },
			{ generation_id: 'gen_demo_next', queue_position: 2, status: 'pending' }
		],
		submittedPromptTemplate: null
	};

	function startDemoGeneration() {
		demoGenerating = true;
		demoGeneration = {
			...demoGeneration,
			isGenerating: true,
			currentProgress: { step: 'sampling', current_step: 'Sampling latent image', message: '[PIPE:KSampler] [STEP:19/30]', progress: 0.64 }
		};
	}

	function cancelDemoGeneration() {
		demoGenerating = false;
		demoGeneration = { ...demoGeneration, isGenerating: false, currentProgress: null, queue: [] };
	}
	const demoSessions: Session[] = [
		{ id: 'session-1', preset_id: 'potion-xl', name: 'Amber product study', data: {}, created_at: '2026-07-10T12:00:00Z', updated_at: '2026-07-11T08:30:00Z' },
		{ id: 'session-2', preset_id: 'potion-xl', name: 'Blue glass variations', data: {}, created_at: '2026-07-09T12:00:00Z', updated_at: '2026-07-10T15:15:00Z' }
	];
	$: demoSession = demoSessions.find((session) => session.id === demoSessionId) ?? null;
	const demoPresets = [
		{
			id: 'potion-xl',
			name: 'Potion Lab XL',
			version: '1.2.0',
			description: 'Controlled product imagery with precise glass, liquid, and metallic material rendering.',
			tags: ['product', 'photorealistic', 'studio'],
			category: 'image',
			engine: 'native',
			source: 'built-in',
			media: { cover: '/frontend-kit/potion-lab.png' }
		},
		{
			id: 'architecture',
			name: 'Architectural Study',
			version: '2.0.1',
			description: 'Atmospheric architectural compositions with controlled perspective and material detail.',
			tags: ['architecture', 'cinematic'],
			category: 'image',
			engine: 'comfyui',
			media: { cover: '/frontend-kit/product-study.png' }
		},
		{
			id: 'botanical',
			name: 'Botanical Archive',
			version: '1.0.0',
			description: 'Macro botanical studies and museum-style specimen photography.',
			tags: ['macro', 'botanical', 'editorial'],
			category: 'image',
			engine: 'native',
			media: { cover: '/frontend-kit/portrait-study.png' }
		},
		{
			id: 'motion',
			name: 'Cinematic Motion',
			version: '0.9.4',
			description: 'Short cinematic video generation with camera-motion controls.',
			tags: ['video', 'motion'],
			category: 'video',
			engine: 'comfyui'
		}
	];
	let chip: ChipData = {
		id: 'kit-chip',
		categoryPath: 'camera/angle',
		valueId: 'eye-level',
		label: 'Eye level',
		value: 'eye-level shot',
		allValues: [
			{ id: 'eye-level', label: 'Eye level', value: 'eye-level shot' },
			{ id: 'low-angle', label: 'Low angle', value: 'low-angle shot' },
			{ id: 'overhead', label: 'Overhead', value: 'overhead shot' }
		],
		shuffle: false,
		autoRegen: false
	};
</script>

<div class="space-y-8">
	<ComponentExample
		title="PresetPicker"
		description="The production master-detail preset browser: searchable, filterable, responsive, and explicit about selection."
		code={`<PresetPicker\n  {presets}\n  {selectedPreset}\n  on:select={(event) => selectedPreset = event.detail}\n/>`}
	>
		<div class="w-full max-w-md">
			<div class="label">Preset</div>
			<PresetPicker
				presets={demoPresets}
				{selectedPreset}
				on:select={(event) => (selectedPreset = event.detail)}
			/>
		</div>
	</ComponentExample>

	<ComponentExample
		title="Rating and favorite controls"
		description="State controls used by generation and model cards. This preview is fully interactive and API-free."
		code={`<StarRating value={rating} size="md" onChange={(value) => rating = value} />\n<FavoriteButton active={favorite} size="md" onToggle={() => favorite = !favorite} />`}
	>
		<div class="flex items-center gap-6">
			<div>
				<span class="label">Rating</span>
				<StarRating value={rating} size="md" onChange={(value) => (rating = value)} />
			</div>
			<div>
				<span class="label">Favorite</span>
				<div class="h-5 flex items-center">
					<FavoriteButton active={favorite} size="md" onToggle={() => (favorite = !favorite)} />
				</div>
			</div>
			<span class="font-mono text-xs text-fg-subtle">{rating}/5 · {favorite ? 'saved' : 'not saved'}</span>
		</div>
	</ComponentExample>

	<ComponentExample
		title="GenerationPanel"
		description="The production generation transport with running status, progress, queue inspection, mode selection, and drawer actions. Cancel it to inspect the ready state."
		code={`<GenerationPanel\n  generation={generationState}\n  isGenerating\n  onGenerate={startGeneration}\n  onCancel={cancelGeneration}\n  canGenerate\n/>`}
	>
		<div class="w-full overflow-visible rounded-lg border border-line">
			<GenerationPanel
				generation={demoGeneration}
				isGenerating={demoGenerating}
				onGenerate={startDemoGeneration}
				onCancel={cancelDemoGeneration}
				canGenerate
				onClearQueue={() => (demoGeneration = { ...demoGeneration, queue: [] })}
			/>
		</div>
	</ComponentExample>

	<ComponentExample
		title="SessionControl"
		description="A compound session trigger with explicit save state, session switching, auto-save configuration, and management actions in one place."
		code={`<SessionControl\n  enabled\n  {sessions}\n  {currentSession}\n  dirty={hasUnsavedChanges}\n  onSave={saveSession}\n  onSelect={selectSession}\n/>`}
	>
		<div class="flex w-full flex-wrap items-center gap-3">
			<SessionControl
				enabled
				sessions={demoSessions}
				currentSession={demoSession}
				selectedSessionId={demoSessionId}
				dirty={demoSessionDirty}
				lastSavedTime={demoLastSaved}
				autoSaveEnabled={demoAutoSave}
				autoSaveInterval={demoAutoSaveInterval}
				onSelect={(id) => { demoSessionId = id; demoSessionDirty = false; }}
				onSave={() => { demoSessionDirty = false; demoLastSaved = new Date(); }}
				onSaveAs={() => undefined}
				onRename={() => undefined}
				onDelete={() => undefined}
				onToggleAutoSave={() => (demoAutoSave = !demoAutoSave)}
				onIntervalChange={(interval) => (demoAutoSaveInterval = interval)}
				onOpenHistory={() => undefined}
				onCloseHistory={() => undefined}
				onRestoreVersion={() => undefined}
			/>
			<Button variant="secondary" size="xs" onclick={() => (demoSessionDirty = true)}>Simulate edit</Button>
		</div>
	</ComponentExample>

	<ComponentExample
		title="GenerationLayoutPicker"
		description="A visual two- or three-pane workspace selector. Its compact trigger fits the generation bar while the menu explains the tradeoff."
		code={`<GenerationLayoutPicker value={layoutMode} onChange={(mode) => layoutMode = mode} />`}
	>
		<div class="flex w-full items-center justify-end">
			<GenerationLayoutPicker value={demoLayout} onChange={(mode) => (demoLayout = mode)} />
		</div>
	</ComponentExample>

	<ComponentExample
		title="CustomSelect"
		description="Searchable, portal-based select used when native select behavior is not sufficient."
		code={`<CustomSelect\n  bind:value={engine}\n  searchable\n  options={[{ value: "native", label: "Native" }]}\n/>`}
	>
		<div class="w-full max-w-sm">
			<label class="label" for="engine-preview">Generation engine</label>
			<CustomSelect
				bind:value={selectedEngine}
				searchable
				options={[
					{ value: 'native', label: 'Native', description: 'Run locally on the configured GPU' },
					{ value: 'comfyui', label: 'ComfyUI', description: 'Send a workflow to a ComfyUI backend' },
					{ value: 'remote', label: 'Remote provider', description: 'Use a configured API provider' }
				]}
			/>
		</div>
	</ComponentExample>

	<ComponentExample
		title="InlineChip"
		description="Prompt token with alternative values, shuffle behavior, deactivation, and removal affordances."
		code={`<InlineChip data={chip} onchange={(next) => chip = next} colorIndex={8} />`}
	>
		<div class="w-full rounded border border-line bg-surface-1 p-4 text-sm text-fg-muted">
			Cinematic product photograph from an
			<InlineChip data={chip} onchange={(next) => (chip = next)} colorIndex={8} />
			with controlled studio lighting.
		</div>
	</ComponentExample>

	<ComponentExample
		title="Tooltip"
		description="Viewport-aware contextual text for unfamiliar icon actions."
		code={`<Tooltip text="Refresh available models" position="bottom">\n  <Button>Hover me</Button>\n</Tooltip>`}
	>
		<Tooltip text="Refresh available models" position="bottom" delay={50}>
			<Button variant="secondary" size="sm" icon="refresh">Hover me</Button>
		</Tooltip>
	</ComponentExample>
</div>
