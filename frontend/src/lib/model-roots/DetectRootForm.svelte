<script lang="ts">
	import { onMount } from 'svelte';
	import { Button, Input, Alert, Switch } from '$lib/components/ui';
	import type { ModelRoot, ModelRootDetection, ModelRootDetectionSuggestion } from '$lib/services/api/models';
	import { detectModelRoot, serverPathPlaceholder, type ModelRootsState } from './state.svelte';
	import { mergeDetectionSuggestions } from './logic';
	import { modelTypePresentation } from '$lib/utils/modelPresentation';

	let {
		rootsState,
		pathStyle,
		onCreated,
		initialPath = '',
		showActions = true,
		secondaryLabel,
		onSecondary,
		submitRef = $bindable<(() => void) | null>(null),
		canSubmitRef = $bindable(false),
		busyRef = $bindable(false)
	}: {
		rootsState: ModelRootsState;
		pathStyle: 'windows' | 'posix' | undefined;
		onCreated: (root: ModelRoot) => void;
		initialPath?: string;
		showActions?: boolean;
		secondaryLabel?: string;
		onSecondary?: () => void;
		submitRef?: (() => void) | null;
		canSubmitRef?: boolean;
		busyRef?: boolean;
	} = $props();

	let path = $state(initialPath);
	let detecting = $state(false);
	let detection = $state<ModelRootDetection | null>(null);
	let detectError = $state<string | null>(null);
	let createError = $state<string | null>(null);
	let ticked = $state<Record<string, boolean>>({});
	let writeHere = $state(true);
	let submitting = $state(false);

	const suggestions = $derived<ModelRootDetectionSuggestion[]>(mergeDetectionSuggestions(detection));
	const tickedCount = $derived(suggestions.filter((s) => ticked[s.model_type]).length);
	const canSubmit = $derived(
		!!detection && detection.state === 'online' && (suggestions.length === 0 || tickedCount > 0)
	);

	$effect(() => {
		const next: Record<string, boolean> = {};
		for (const s of suggestions) next[s.model_type] = true;
		ticked = next;
	});

	$effect(() => {
		canSubmitRef = canSubmit;
	});

	$effect(() => {
		busyRef = submitting;
	});

	$effect(() => {
		submitRef = () => void create();
	});

	onMount(() => {
		if (initialPath.trim()) void detect();
	});

	async function detect() {
		if (!path.trim()) return;
		detecting = true;
		detectError = null;
		detection = null;
		const result = await detectModelRoot(path.trim());
		detection = result.detection;
		detectError = result.error;
		detecting = false;
	}

	async function create() {
		if (!canSubmit || !detection) return;
		const tickedTypes = suggestions.filter((s) => ticked[s.model_type]);
		submitting = true;
		createError = null;
		try {
			const root = await rootsState.create({
				path: detection.path,
				bindings: tickedTypes.map((s) => ({ model_type: s.model_type, subdir: s.subdir })),
				write_types: writeHere ? tickedTypes.map((s) => s.model_type) : []
			});
			if (root) {
				onCreated(root);
				path = '';
				detection = null;
			}
		} catch (e) {
			createError = rootsState.error ?? 'Failed to add the folder.';
		} finally {
			submitting = false;
		}
	}
</script>

<div class="space-y-3">
	<div>
		<label for="model-root-path" class="block text-sm font-medium text-fg mb-1">Folder path</label>
		<div class="flex items-center gap-2">
			<Input
				id="model-root-path"
				bind:value={path}
				placeholder={serverPathPlaceholder(pathStyle)}
				disabled={detecting || submitting}
				onkeydown={(e: KeyboardEvent) => {
					if (e.key === 'Enter') void detect();
				}}
			/>
			<Button variant="secondary" size="sm" onclick={detect} loading={detecting} disabled={detecting || !path.trim()}>
				Detect
			</Button>
		</div>
	</div>

	{#if detectError}
		<Alert variant="danger" icon>{detectError}</Alert>
	{/if}

	{#if detection}
		{#each detection.warnings as warning (warning)}
			<Alert variant="warning" icon>{warning}</Alert>
		{/each}

		{#if detection.state !== 'online'}
			<Alert variant="danger" icon>
				{detection.state === 'unreadable' ? "PotionUI can't read this folder." : "This path isn't reachable from the server."}
			</Alert>
		{:else}
			{#if detection.conflicts.length > 0}
				<Alert variant="warning" icon>
					Overlaps a folder that's already a model root ({detection.conflicts.map((c) => c.reason).join('; ')}).
				</Alert>
			{/if}

			{#if suggestions.length === 0}
				<Alert variant="info" icon>
					No model files found yet - PotionUI can still add this as an empty folder; point model types at
					it later from Admin -> Models -> Folders.
				</Alert>
			{:else}
				<div class="space-y-1.5">
					<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle">Detected types</span>
					<ul class="space-y-1.5">
						{#each suggestions as suggestion (suggestion.model_type)}
							{@const presentation = modelTypePresentation(suggestion.model_type)}
							<li class="flex items-center gap-2.5 rounded border border-line bg-surface-2 px-3 py-2">
								<input
									type="checkbox"
									id="root-type-{suggestion.model_type}"
									class="h-4 w-4 rounded accent-signal-solid"
									bind:checked={ticked[suggestion.model_type]}
								/>
								<label for="root-type-{suggestion.model_type}" class="min-w-0 flex-1 cursor-pointer">
									<span class="block text-sm text-fg">{presentation.label}</span>
									<span class="block truncate font-mono text-xs text-fg-subtle">
										{suggestion.subdir || '(this folder)'}
									</span>
								</label>
								<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
									{suggestion.file_count}{suggestion.file_count_truncated ? '+' : ''} file{suggestion.file_count === 1 ? '' : 's'}
								</span>
							</li>
						{/each}
					</ul>
				</div>

				<div class="flex items-center gap-2">
					<Switch bind:checked={writeHere} label="Download new models here" id="root-write-here" />
					<label for="root-write-here" class="text-sm text-fg cursor-pointer">Download new models here</label>
				</div>
			{/if}
		{/if}
	{/if}

	{#if createError}
		<Alert variant="danger" icon>{createError}</Alert>
	{/if}

	{#if showActions}
		<div class="flex items-center gap-3">
			{#if canSubmit}
				<Button variant="primary" size="sm" onclick={create} loading={submitting} disabled={submitting}>
					Add folder
				</Button>
			{/if}
			{#if secondaryLabel && onSecondary}
				<Button variant="secondary" size="sm" onclick={onSecondary} disabled={submitting}>
					{secondaryLabel}
				</Button>
			{/if}
		</div>
	{/if}
</div>
