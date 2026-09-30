<script lang="ts">
	import { onMount } from 'svelte';
	import { Button, Input, Alert, Switch, Badge } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type {
		ModelLayoutSummary,
		ModelRoot,
		ModelRootDelegatedApp,
		ModelRootDetection,
		ModelRootDetectionSuggestion
	} from '$lib/services/api/models';
	import { detectModelRoot, listModelLayouts, serverPathPlaceholder, type ModelRootsState } from './state.svelte';
	import {
		GENERIC_PROFILE_ID,
		buildBindings,
		detectedLabel,
		groupSuggestionsByType,
		initialTicks,
		initialWriteChoices,
		layoutOptions,
		mergeDetectionSuggestions,
		pendingExtraPaths,
		suggestionKey,
		typesWithChoice
	} from './logic';
	import { modelRootsErrorMessage } from './errors';
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
	let writeChoices = $state<Record<string, string>>({});
	let extraTicked = $state<Record<string, boolean>>({});
	let extraDone = $state<Record<string, boolean>>({});
	let extraErrors = $state<Record<string, string>>({});
	let writeHere = $state(true);
	let submitting = $state(false);
	let createdRoot = $state<ModelRoot | null>(null);
	let catalog = $state<ModelLayoutSummary[]>([]);
	let delegatedApps = $state<ModelRootDelegatedApp[]>([]);

	const suggestions = $derived<ModelRootDetectionSuggestion[]>(mergeDetectionSuggestions(detection));
	const groups = $derived(groupSuggestionsByType(suggestions));
	const tickedCount = $derived(suggestions.filter((s) => ticked[suggestionKey(s)]).length);
	const choiceTypes = $derived(typesWithChoice(suggestions, ticked));
	const extras = $derived(detection?.extra_roots ?? []);
	const outside = $derived(detection?.outside_folders ?? []);
	const profile = $derived(detection?.profile ?? null);
	const options = $derived(
		layoutOptions(
			detection?.alternatives ?? [],
			profile ? { id: profile.id, label: profile.label } : null,
			catalog
		)
	);
	const selectedLayout = $derived(profile?.id ?? GENERIC_PROFILE_ID);
	const pendingExtras = $derived(pendingExtraPaths(extras, extraTicked, extraDone));
	const canSubmit = $derived(
		!!detection &&
			detection.state === 'online' &&
			!submitting &&
			(!!createdRoot ? pendingExtras.length > 0 : suggestions.length === 0 || tickedCount > 0)
	);

	$effect(() => {
		ticked = initialTicks(suggestions);
		writeChoices = initialWriteChoices(suggestions);
	});

	$effect(() => {
		const next: Record<string, boolean> = {};
		for (const extra of extras) next[extra.path] = false;
		extraTicked = next;
		extraDone = {};
		extraErrors = {};
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
		void listModelLayouts().then((layouts) => (catalog = layouts));
		if (initialPath.trim()) void detect();
	});

	const locked = $derived(!!createdRoot);

	async function detect(profileId?: string) {
		if (!path.trim() || createdRoot) return;
		detecting = true;
		detectError = null;
		createError = null;
		createdRoot = null;
		detection = null;
		const result = await detectModelRoot(path.trim(), profileId);
		detection = result.detection;
		detectError = result.error;
		if (result.detection?.delegated?.length) {
			delegatedApps = result.detection.delegated;
		} else if (!delegatedApps.some((app) => app.path === path.trim())) {
			delegatedApps = [];
		}
		detecting = false;
	}

	function useInstallFolder(installPath: string) {
		path = installPath;
		void detect();
	}

	function pickApp(app: ModelRootDelegatedApp) {
		path = app.path;
		void detect();
	}

	async function create() {
		if (!canSubmit || !detection) return;
		submitting = true;
		createError = null;
		try {
			if (!createdRoot) {
				const built = buildBindings(suggestions, ticked, writeChoices, writeHere);
				createdRoot = await rootsState.create({
					path: detection.root_path ?? detection.path,
					profile: detection.profile?.id ?? GENERIC_PROFILE_ID,
					bindings: built.bindings,
					write_types: built.writeTypes
				});
			}
			if (!createdRoot) return;
			for (const extra of extras) {
				if (!extraTicked[extra.path] || extraDone[extra.path]) continue;
				try {
					const created = await rootsState.create({
						path: extra.path,
						profile: extra.profile_id,
						bindings: extra.suggestions.map((s) => ({
							model_type: s.model_type,
							subdir: s.subdir,
							...(s.scan_headers !== undefined ? { scan_headers: s.scan_headers } : {}),
							write: writeHere && !!s.write
						}))
					});
					if (!created) {
						extraErrors = { ...extraErrors, [extra.path]: rootsState.error ?? 'Failed to add this folder.' };
						continue;
					}
					extraDone = { ...extraDone, [extra.path]: true };
					extraErrors = { ...extraErrors, [extra.path]: '' };
				} catch (e) {
					extraErrors = { ...extraErrors, [extra.path]: modelRootsErrorMessage(e, 'Failed to add this folder.') };
				}
			}
			if (pendingExtraPaths(extras, extraTicked, extraDone).length === 0) {
				onCreated(createdRoot);
				path = '';
				detection = null;
				createdRoot = null;
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
				disabled={detecting || submitting || locked}
				onkeydown={(e: KeyboardEvent) => {
					if (e.key === 'Enter') void detect();
				}}
			/>
			<Button variant="secondary" size="sm" onclick={() => detect()} loading={detecting} disabled={detecting || locked || !path.trim()}>
				Detect
			</Button>
		</div>
	</div>

	{#if detectError}
		<Alert variant="danger" icon>{detectError}</Alert>
	{/if}

	{#if detection}
		<div class="space-y-2">
			<div class="flex flex-wrap items-center gap-2">
				{#if profile}
					<Tooltip
						text={profile.evidence.length > 0 ? `Found: ${profile.evidence.join(', ')}` : 'Chosen by hand'}
						position="top"
					>
						<Badge variant={profile.confidence === 'weak' ? 'warning' : 'signal'}>{detectedLabel(profile)}</Badge>
					</Tooltip>
					{#if profile.variant}
						<Badge variant="neutral" size="sm">{profile.variant}</Badge>
					{/if}
				{:else}
					<Badge variant="neutral">{detectedLabel(null)}</Badge>
				{/if}
			</div>
			<div>
				<label for="model-root-layout" class="block text-sm font-medium text-fg mb-1">Layout</label>
				<select
					id="model-root-layout"
					class="input w-full text-sm"
					value={selectedLayout}
					disabled={detecting || submitting || locked}
					onchange={(e) => void detect((e.currentTarget as HTMLSelectElement).value)}
				>
					{#each options as option (option.id)}
						<option value={option.id}>{option.label}</option>
					{/each}
				</select>
			</div>
			{#if !profile}
				<Alert variant="info" icon>
					No known layout was recognised, so the folder names are used (Generic). Pick a layout above if this
					folder belongs to a tool.
				</Alert>
			{/if}
		</div>

		{#each detection.warnings as warning (warning)}
			<Alert variant="warning" icon>{warning}</Alert>
		{/each}

		{#if delegatedApps.length > 0}
			<div class="space-y-1.5">
				<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle">Pinokio apps</span>
				<div class="flex flex-wrap gap-1.5" role="group" aria-label="Pinokio apps">
					{#each delegatedApps as app (app.path)}
						<button
							type="button"
							class="inline-flex items-center gap-1.5 rounded border px-2.5 py-1 text-sm transition-colors {detection.root_path ===
							app.path
								? 'border-signal bg-signal/10 text-signal'
								: 'border-line bg-surface-2 text-fg-muted hover:border-line-hover'}"
							aria-pressed={detection.root_path === app.path}
							disabled={detecting || submitting || locked}
							onclick={() => pickApp(app)}
						>
							{app.label}
							<span class="text-xs text-fg-subtle">{app.profile.label}</span>
						</button>
					{/each}
				</div>
			</div>
		{/if}

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

			{#if outside.length > 0}
				<Alert variant="info" icon>
					<div class="space-y-1.5">
						<p>This tool keeps some folders outside the one you chose:</p>
						<ul class="space-y-0.5">
							{#each outside as folder (folder.path)}
								<li class="truncate font-mono text-xs">
									<Tooltip text={folder.path} position="top">
										<span>{folder.label}: {folder.path}</span>
									</Tooltip>
								</li>
							{/each}
						</ul>
						<Button variant="secondary" size="sm" disabled={locked} onclick={() => useInstallFolder(outside[0].install_path)}>
							Use the install folder instead
						</Button>
					</div>
				</Alert>
			{/if}

			{#if suggestions.length === 0}
				<Alert variant="info" icon>
					No model files found yet - PotionUI can still add this as an empty folder; point model types at
					it later from Admin -> Models -> Folders.
				</Alert>
			{:else}
				<div class="space-y-3">
					<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle">Detected folders</span>
					{#each groups as group (group.model_type)}
						<div class="space-y-1.5">
							<span class="block text-sm font-medium text-fg">{modelTypePresentation(group.model_type).label}</span>
							<ul class="space-y-1.5">
								{#each group.items as suggestion (suggestionKey(suggestion))}
									{@const key = suggestionKey(suggestion)}
									<li class="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded border border-line bg-surface-2 px-3 py-2">
										<input
											type="checkbox"
											id="root-folder-{key}"
											class="h-4 w-4 rounded accent-signal-solid"
											bind:checked={ticked[key]}
											disabled={locked}
										/>
										<label for="root-folder-{key}" class="min-w-0 flex-1 cursor-pointer">
											<span class="block truncate text-sm text-fg">{suggestion.label || suggestion.subdir || '(this folder)'}</span>
											<span class="block truncate font-mono text-xs text-fg-subtle">
												{suggestion.subdir || '(this folder)'}
											</span>
										</label>
										{#if writeHere && choiceTypes.has(group.model_type) && ticked[key]}
											<label class="order-last flex w-full shrink-0 cursor-pointer items-center gap-1.5 pl-[1.625rem] text-xs text-fg-muted sm:order-none sm:w-auto sm:pl-0">
												<input
													type="radio"
													name="root-write-{group.model_type}"
													class="h-3.5 w-3.5 accent-signal-solid"
													checked={writeChoices[group.model_type] === suggestion.subdir}
													onchange={() => (writeChoices = { ...writeChoices, [group.model_type]: suggestion.subdir })}
												/>
												Downloads go here
											</label>
										{/if}
										<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
											{suggestion.file_count}{suggestion.file_count_truncated ? '+' : ''} file{suggestion.file_count === 1 ? '' : 's'}
										</span>
									</li>
								{/each}
							</ul>
						</div>
					{/each}
				</div>

				<div class="flex items-center gap-2">
					<Switch bind:checked={writeHere} label="Download new models here" id="root-write-here" />
					<label for="root-write-here" class="text-sm text-fg cursor-pointer">Download new models here</label>
				</div>
			{/if}

			{#if extras.length > 0}
				<div class="space-y-1.5">
					<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle">Also used by this install</span>
					<ul class="space-y-1.5">
						{#each extras as extra (extra.path)}
							<li class="rounded border border-line bg-surface-2 px-3 py-2">
								<div class="flex items-center gap-2.5">
									<input
										type="checkbox"
										id="root-extra-{extra.path}"
										class="h-4 w-4 rounded accent-signal-solid"
										bind:checked={extraTicked[extra.path]}
										disabled={extraDone[extra.path]}
									/>
									<label for="root-extra-{extra.path}" class="min-w-0 flex-1 cursor-pointer">
										<span class="block text-sm text-fg">
											{extraDone[extra.path] ? 'Added' : 'Add as its own folder'}
										</span>
										<Tooltip text={extra.path} position="top">
											<span class="block truncate font-mono text-xs text-fg-subtle">{extra.path}</span>
										</Tooltip>
										<span class="block truncate text-xs text-fg-subtle">{extra.source}</span>
									</label>
									<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
										{extra.suggestions.length} folder{extra.suggestions.length === 1 ? '' : 's'}
									</span>
								</div>
								{#if extraErrors[extra.path]}
									<p class="mt-1 text-xs text-danger">{extraErrors[extra.path]}</p>
								{/if}
							</li>
						{/each}
					</ul>
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
