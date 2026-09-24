<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api/index';
	import type {
		RecipeDetail,
		RecipeRun,
		RecipeSummary
	} from '$lib/services/api/recipes';
	import type { ReadinessReport, SetupRun } from '$lib/services/api/setup';
	import { recipeCatalog } from '$lib/stores/recipeCatalog';
	import { logger, getApiErrorMessage } from '$lib/utils/logger';
	import { formatBytes } from '$lib/utils/format';
	import {
		isRunTerminal,
		shouldPollRun,
		runBadgeVariant,
		runStatusLabel,
		RUN_POLL_INTERVAL_MS
	} from '$lib/utils/setupRunDisplay';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailBody, DetailSection, DetailFooter } from '$lib/components/detail';
	import RecipeRunProgress from '$lib/components/recipes/RecipeRunProgress.svelte';
	import { adminRecipeRunActions } from '$lib/components/recipes/runActions';
	import { Alert, Badge, Button, EmptyState, Spinner } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryDensityToggle from '$lib/components/library/LibraryDensityToggle.svelte';
	import { libraryCardDensity } from '$lib/components/library/libraryCardDensity';
	import { deriveRecipeReadiness, type RecipeReadinessBadge } from './recipeReadinessBadge';
	import { runDuration, runStartedLabel, mergeRunHistory } from './recipeRunHistory';
	import { readRecipesUrlState } from './recipesUrlState';
	import {
		RECIPE_SECTIONS,
		recipeSectionCounts,
		recipeSectionFromParam,
		recipesInSection,
		type RecipeSection
	} from './recipes/recipeSections';
	import {
		DEFAULT_RECIPE_FILTERS,
		RECIPE_SORT_OPTIONS,
		applyRecipeFilters,
		clearAllRecipeFilters,
		clearRecipeFilterChip,
		recipeEngineVocabulary,
		recipeFilterActiveCount,
		recipeFilterChips,
		recipeFiltersFromSearchParams,
		recipeFiltersToSearchParams,
		recipeSourceVocabulary,
		type RecipeFilters,
		type RecipeSortBy
	} from './recipes/recipeFilters';
	import RecipeFiltersPopover from './recipes/RecipeFiltersPopover.svelte';
	import RecipeCard from './recipes/RecipeCard.svelte';

	const RUN_HISTORY_LIMIT = 20;
	const READINESS_CONCURRENCY = 3;
	const MANAGED_URL_PARAMS = ['q', 'sort_by', 'source', 'engine', 'section', 'id'];

	let recipes: RecipeSummary[] = $state([]);
	let loading = $state(true);
	let refreshing = $state(false);
	let loadError = $state('');

	let filters: RecipeFilters = $state(DEFAULT_RECIPE_FILTERS);
	let section: RecipeSection = $state('all');

	let selectedRecipeId = $state('');
	let detail = $state<RecipeDetail | null>(null);
	let detailLoading = $state(false);
	let detailError = $state('');
	let detailRequestVersion = 0;

	let readinessById = $state<Record<string, ReadinessReport | null>>({});
	const readinessQueue: string[] = [];
	const readinessQueued = new Set<string>();
	let readinessWorkers = 0;

	let runs = $state<RecipeRun[]>([]);
	let runsLoading = $state(false);
	let runsError = $state('');
	let activeRun = $state<RecipeRun | null>(null);
	let starting = $state(false);
	let startError = $state('');
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let urlRestored = $state(false);

	const sources = $derived(recipeSourceVocabulary(recipes));
	const engines = $derived(recipeEngineVocabulary(recipes));
	const sectionCounts = $derived(recipeSectionCounts(recipes));
	const filteredRecipes = $derived(applyRecipeFilters(recipesInSection(recipes, section), filters));
	const filterCount = $derived(recipeFilterActiveCount(filters));
	const filterChips = $derived(recipeFilterChips(filters));
	const detailOpen = $derived(!!selectedRecipeId);

	const selectedRecipe = $derived(recipes.find((r) => r.id === selectedRecipeId) || null);
	const runHistory = $derived(mergeRunHistory(runs, activeRun));
	const runInFlight = $derived(!!activeRun && !isRunTerminal(activeRun.status));

	function readinessFor(recipeId: string): RecipeReadinessBadge {
		return deriveRecipeReadiness(readinessById[recipeId] ?? null);
	}

	async function loadRecipes(background = false) {
		if (background) refreshing = true;
		else loading = true;
		loadError = '';
		try {
			const result = await api.listRecipes();
			recipes = result.recipes ?? [];
			recipeCatalog.set(recipes);
			for (const recipe of recipes) enqueueReadiness(recipe.id);
		} catch (error) {
			logger.error('Failed to load recipes:', error);
			loadError = getApiErrorMessage(error, 'Could not load the recipe catalog');
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	function enqueueReadiness(recipeId: string) {
		if (readinessQueued.has(recipeId)) return;
		readinessQueued.add(recipeId);
		readinessQueue.push(recipeId);
		while (readinessWorkers < READINESS_CONCURRENCY && readinessQueue.length > 0) {
			readinessWorkers += 1;
			void drainReadiness();
		}
	}

	async function drainReadiness() {
		try {
			while (readinessQueue.length > 0) {
				const recipeId = readinessQueue.shift();
				if (!recipeId) break;
				try {
					const report = await api.getRecipeReadiness(recipeId);
					readinessById = { ...readinessById, [recipeId]: report };
				} catch (error) {
					logger.warn('Recipe readiness probe failed', recipeId, error);
					readinessById = { ...readinessById, [recipeId]: null };
				}
			}
		} finally {
			readinessWorkers -= 1;
		}
	}

	function buildUrl(): URL {
		const nextUrl = new URL($page.url);
		for (const key of MANAGED_URL_PARAMS) nextUrl.searchParams.delete(key);
		const filterParams = recipeFiltersToSearchParams(filters);
		for (const [key, value] of filterParams) nextUrl.searchParams.set(key, value);
		if (section !== 'all') nextUrl.searchParams.set('section', section);
		if (selectedRecipeId) nextUrl.searchParams.set('id', selectedRecipeId);
		return nextUrl;
	}

	function openRecipe(id: string) {
		selectedRecipeId = id;
	}

	function backToList() {
		selectedRecipeId = '';
	}

	function selectSection(next: RecipeSection) {
		section = next;
	}

	function updateFilters(next: RecipeFilters) {
		filters = next;
	}

	function updateQuery(value: string) {
		updateFilters({ ...filters, q: value });
	}

	function updateSort(value: string) {
		updateFilters({ ...filters, sortBy: value as RecipeSortBy });
	}

	function removeChip(key: string) {
		updateFilters(clearRecipeFilterChip(filters, key));
	}

	function clearFilters() {
		updateFilters(clearAllRecipeFilters(filters));
	}

	let lastLoadedRecipeId: string | null = null;
	$effect(() => {
		const id = selectedRecipeId;
		if (id === lastLoadedRecipeId) return;
		lastLoadedRecipeId = id;
		detail = null;
		detailError = '';
		runs = [];
		runsError = '';
		startError = '';
		activeRun = null;
		clearPoll();
		if (id) {
			void loadDetail(id);
			void loadRuns(id);
		}
	});

	async function loadDetail(id: string) {
		const version = ++detailRequestVersion;
		detailLoading = true;
		detailError = '';
		try {
			const fetched = await api.getRecipe(id);
			if (version !== detailRequestVersion) return;
			detail = fetched;
		} catch (error) {
			if (version !== detailRequestVersion) return;
			logger.error('Failed to load recipe detail:', error);
			detailError = getApiErrorMessage(error, 'Could not load this recipe');
		} finally {
			if (version === detailRequestVersion) detailLoading = false;
		}
	}

	async function loadRuns(id: string) {
		runsLoading = true;
		runsError = '';
		try {
			const result = await api.listRecipeRuns({ recipeId: id, limit: RUN_HISTORY_LIMIT });
			if (id !== selectedRecipeId) return;
			runs = result.runs ?? [];
			const live = runs.find((run) => !isRunTerminal(run.status)) ?? null;
			if (live) adoptRun(live);
		} catch (error) {
			if (id !== selectedRecipeId) return;
			logger.error('Failed to load recipe runs:', error);
			runsError = getApiErrorMessage(error, 'Could not load this recipe’s run history');
		} finally {
			if (id === selectedRecipeId) runsLoading = false;
		}
	}

	function clearPoll() {
		if (pollTimer) {
			clearTimeout(pollTimer);
			pollTimer = null;
		}
	}

	function schedulePoll(runId: string) {
		clearPoll();
		pollTimer = setTimeout(() => void refreshRun(runId), RUN_POLL_INTERVAL_MS);
	}

	function adoptRun(updated: SetupRun) {
		activeRun = updated;
		if (shouldPollRun(updated.status)) schedulePoll(updated.id);
		else clearPoll();
	}

	async function refreshRun(runId: string) {
		try {
			const fetched = await api.getRecipeRun(runId);
			adoptRun(fetched);
			if (isRunTerminal(fetched.status)) {
				await loadRecipes(true);
				readinessQueued.delete(fetched.recipe_id);
				enqueueReadiness(fetched.recipe_id);
				if (fetched.recipe_id === selectedRecipeId) await loadRuns(fetched.recipe_id);
			}
		} catch (error) {
			logger.warn('Recipe run poll failed', runId, error);
			schedulePoll(runId);
		}
	}

	async function startRun() {
		if (!selectedRecipe || starting) return;
		starting = true;
		startError = '';
		try {
			adoptRun(await api.createRecipeRun(selectedRecipe.id));
		} catch (error) {
			startError = getApiErrorMessage(error, 'Could not start this recipe');
		} finally {
			starting = false;
		}
	}

	onMount(async () => {
		filters = recipeFiltersFromSearchParams($page.url.searchParams);
		section = recipeSectionFromParam($page.url.searchParams.get('section'));
		const { recipeId } = readRecipesUrlState($page.url.searchParams);
		await loadRecipes();
		if (recipeId && recipes.some((r) => r.id === recipeId)) selectedRecipeId = recipeId;
		urlRestored = true;
	});

	onDestroy(() => clearPoll());

	$effect(() => {
		if (!urlRestored) return;
		const nextUrl = buildUrl();
		if (nextUrl.search !== $page.url.search) {
			void goto(nextUrl, { replaceState: true, keepFocus: true, noScroll: true });
		}
	});
</script>

<div class="flex h-full flex-col">
	<div class="flex min-h-0 flex-1 flex-col">
		<LibraryShell
			title="Recipes"
			persistKey="admin-recipes-library"
			sections={RECIPE_SECTIONS}
			{section}
			onSelectSection={selectSection}
			{sectionCounts}
			count={filteredRecipes.length}
			detailOpen={detailOpen || loading || (!!loadError && recipes.length === 0)}
			{filterChips}
			onRemoveChip={removeChip}
			onClearFilters={clearFilters}
			loadedCount={filteredRecipes.length}
			total={recipes.length}
			heightClass="h-full"
		>
			{#snippet toolbar()}
				<LibraryFilterBar
					q={filters.q}
					onQueryChange={updateQuery}
					searchPlaceholder="Search recipes by name, id, or description…"
					sortBy={filters.sortBy}
					sortOptions={RECIPE_SORT_OPTIONS}
					onSortChange={updateSort}
					{filterCount}
				>
					{#snippet popover(close)}
						<RecipeFiltersPopover {filters} {sources} {engines} onChange={updateFilters} onClose={close} />
					{/snippet}
				</LibraryFilterBar>
			{/snippet}

			{#snippet primary()}
				<LibraryDensityToggle />
				<Button variant="secondary" size="sm" icon="refresh" loading={refreshing} onclick={() => loadRecipes(true)}>
					Refresh
				</Button>
			{/snippet}

			{#if loading}
				<div class="flex h-full flex-col items-center justify-center">
					<Spinner size="lg" />
					<p class="mt-4 text-sm text-fg-muted">Loading recipes…</p>
				</div>
			{:else if loadError && recipes.length === 0}
				<div class="flex h-full items-center justify-center p-5">
					<EmptyState title="Recipes unavailable" description={loadError} icon="warning" compact>
						{#snippet actions()}
							<Button variant="secondary" size="sm" icon="refresh" onclick={() => loadRecipes()}>
								Try again
							</Button>
						{/snippet}
					</EmptyState>
				</div>
			{:else if detailOpen}
					{#if selectedRecipe}
						{@const badge = readinessFor(selectedRecipe.id)}
						<div class="flex h-full min-h-0 flex-col">
							<DetailHeader title={selectedRecipe.name} icon="list-checks" backLabel="Recipes" onBack={backToList}>
								{#snippet chips()}
									<Badge variant="info" size="sm">{selectedRecipe.engine}</Badge>
									<Badge variant="neutral" size="sm" class="font-mono">
										{selectedRecipe.source}{selectedRecipe.plugin_id
											? `: ${selectedRecipe.plugin_id}`
											: ''}
									</Badge>
									<Badge variant={badge.variant} size="sm">{badge.label}</Badge>
								{/snippet}
								{#snippet subtitle()}
									<span class="truncate">{selectedRecipe.id}</span>
								{/snippet}
								{#snippet actions()}
									<Tooltip text={runInFlight ? 'A run is already in progress' : 'Install models'}>
										<button
											type="button"
											aria-label="Install models"
											class="inline-flex items-center justify-center min-w-8 min-h-8 p-1.5 rounded transition-colors duration-100 text-fg-muted hover:text-fg hover:bg-surface-3/50 disabled:opacity-50 disabled:cursor-not-allowed"
											disabled={starting || runInFlight}
											onclick={startRun}
										>
											{#if starting}<Spinner size="sm" />{:else}
												<Icon name="download" className="w-4 h-4" />
											{/if}
										</button>
									</Tooltip>
								{/snippet}
							</DetailHeader>

							<DetailBody>
								{#if detailLoading && !detail}
									<div class="flex justify-center py-10"><Spinner size="md" /></div>
								{:else if detailError}
									<Alert variant="danger" density="compact" title="Couldn't load this recipe">
										{detailError}
									</Alert>
								{/if}

								{#if startError}
									<Alert variant="danger" density="compact" title="Couldn't start this recipe">
										{startError}
									</Alert>
								{/if}

								{#if detail?.load_errors?.length}
									<Alert variant="warning" density="compact" title="This recipe didn't load cleanly">
										<ul class="space-y-1">
											{#each detail.load_errors as loadErrorEntry}
												<li class="font-mono text-xs">{loadErrorEntry}</li>
											{/each}
										</ul>
									</Alert>
								{/if}

								<DetailSection label="Summary">
									<p class="text-sm text-fg">{selectedRecipe.summary}</p>
									{#if selectedRecipe.description}
										<p class="text-sm text-fg-muted mt-2">{selectedRecipe.description}</p>
									{/if}
									<div
										class="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-xs font-mono tabular-nums text-fg-subtle"
									>
										<span>{selectedRecipe.step_count} steps</span>
										<span>{selectedRecipe.artifact_count} artifacts</span>
										{#if selectedRecipe.total_download_bytes != null}
											<span>~{formatBytes(selectedRecipe.total_download_bytes)} to download</span>
										{/if}
									</div>
								</DetailSection>

								{#if activeRun}
									<DetailSection label="Current run" padded={false}>
										<div class="px-4 sm:px-5 py-4">
											<RecipeRunProgress
												run={activeRun}
												title="Installing models"
												actions={adminRecipeRunActions}
												onRunUpdated={adoptRun}
											/>
										</div>
									</DetailSection>
								{/if}

								{#if detail}
									<DetailSection label="Steps">
										<ul class="space-y-1.5">
											{#each detail.steps as step (step.key)}
												<li
													class="flex items-start justify-between gap-3 rounded border border-line bg-surface-1 px-3 py-2"
												>
													<div class="min-w-0">
														<p
															class="text-sm {step.onboarding_only
																? 'text-fg-subtle'
																: 'text-fg'} truncate"
														>
															{step.title}
														</p>
														<p class="font-mono text-xs text-fg-subtle mt-0.5">{step.kind}</p>
														{#if step.onboarding_only}
															<p class="text-xs text-fg-subtle mt-0.5">
																Skipped when run from here
															</p>
														{/if}
													</div>
													{#if step.onboarding_only}
														<Badge variant="neutral" size="sm">first run only</Badge>
													{/if}
												</li>
											{/each}
										</ul>
									</DetailSection>

									<DetailSection label="Artifacts">
										{#if detail.artifacts.length === 0}
											<p class="text-sm text-fg-muted">This recipe downloads nothing.</p>
										{:else}
											<div class="overflow-x-auto">
												<table class="w-full text-sm">
													<thead>
														<tr class="text-left text-fg-subtle">
															<th class="font-normal font-mono text-xs uppercase pb-2">Model</th>
															<th class="font-normal font-mono text-xs uppercase pb-2">Type</th>
															<th class="font-normal font-mono text-xs uppercase pb-2 text-right">
																Size
															</th>
															<th class="font-normal font-mono text-xs uppercase pb-2 text-right">
																Required
															</th>
														</tr>
													</thead>
													<tbody>
														{#each detail.artifacts as artifact (artifact.id)}
															<tr class="border-t border-line">
																<td class="py-1.5 pr-3 text-fg">
																	<span class="flex items-center gap-1.5">
																		<span class="truncate">{artifact.display_name}</span>
																		{#if artifact.gated}
																			<Badge variant="warning" size="sm">gated</Badge>
																			{#if artifact.license_url}
																				<a
																					href={artifact.license_url}
																					target="_blank"
																					rel="noreferrer"
																					class="text-xs text-signal hover:underline shrink-0"
																				>
																					licence
																				</a>
																			{/if}
																		{/if}
																	</span>
																</td>
																<td class="py-1.5 pr-3 font-mono text-xs text-fg-muted">
																	{artifact.model_type}
																</td>
																<td class="py-1.5 pr-3 font-mono tabular-nums text-fg-muted text-right">
																	{artifact.size_bytes != null ? formatBytes(artifact.size_bytes) : '—'}
																</td>
																<td class="py-1.5 text-right">
																	{#if artifact.required}
																		<Badge variant="neutral" size="sm">required</Badge>
																	{:else}
																		<span class="text-xs text-fg-subtle">optional</span>
																	{/if}
																</td>
															</tr>
														{/each}
													</tbody>
												</table>
											</div>
										{/if}
									</DetailSection>

									<DetailSection label="Presets served">
										{#if detail.presets.length === 0}
											<p class="text-sm text-fg-muted">This recipe installs no presets.</p>
										{:else}
											<ul class="space-y-1">
												{#each detail.presets as preset (preset.preset_id)}
													<li class="flex items-center justify-between gap-3 text-sm">
														<a
															class="text-fg hover:text-signal truncate"
															href="/admin?tab=presets&preset={encodeURIComponent(preset.preset_id)}"
														>
															{preset.preset_id}
														</a>
														<span class="font-mono text-xs text-fg-subtle shrink-0 truncate">
															{preset.path_hint}
														</span>
													</li>
												{/each}
											</ul>
										{/if}
									</DetailSection>
								{/if}

								<DetailSection label="Runs">
									{#if runsLoading && runHistory.length === 0}
										<div class="flex justify-center py-6"><Spinner size="sm" /></div>
									{:else if runsError}
										<p class="text-sm text-danger">{runsError}</p>
									{:else if runHistory.length === 0}
										<p class="text-sm text-fg-muted">This recipe hasn't been run yet.</p>
									{:else}
										<ul class="space-y-1 max-h-72 overflow-y-auto">
											{#each runHistory as run (run.id)}
												<li
													class="flex items-center justify-between gap-3 rounded border border-line bg-surface-1 px-3 py-2"
												>
													<div class="flex items-center gap-2 min-w-0">
														<Badge variant={runBadgeVariant(run.status)} size="sm">
															{runStatusLabel(run.status)}
														</Badge>
														<span class="font-mono text-xs tabular-nums text-fg-muted truncate">
															{runStartedLabel(run)}
														</span>
														<Badge variant="neutral" size="sm">{run.mode}</Badge>
													</div>
													<span class="font-mono text-xs tabular-nums text-fg-subtle shrink-0">
														{runDuration(run) ?? '—'}
													</span>
												</li>
											{/each}
										</ul>
									{/if}
								</DetailSection>
							</DetailBody>

							<DetailFooter>
								<Button
									variant="primary"
									size="sm"
									icon="download"
									loading={starting}
									disabled={starting || runInFlight}
									onclick={startRun}
								>
									{selectedRecipe.last_completed_at ? 'Install again' : 'Install models'}
								</Button>
							</DetailFooter>
						</div>
					{:else}
						<div class="flex h-full items-center justify-center p-5">
							<EmptyState
								title="Recipe not found"
								description="This recipe is no longer in the catalog."
								icon="search"
								compact
							>
								{#snippet actions()}
									<Button variant="secondary" size="sm" onclick={backToList}>Back to recipes</Button>
								{/snippet}
							</EmptyState>
						</div>
					{/if}
				{:else if filteredRecipes.length === 0}
					<div class="flex h-full items-center justify-center p-5">
						<EmptyState
							title={recipes.length === 0 ? 'No recipes available' : 'No matching recipes'}
							description={recipes.length === 0
								? 'Recipes ship with the app and with plugins. Enable a plugin that provides one, or add a local recipe, then refresh.'
								: 'Try a different name, source, or engine.'}
							icon={recipes.length === 0 ? 'list-checks' : 'search'}
							compact
						>
							{#snippet actions()}
								{#if recipes.length > 0}
									<Button variant="ghost" size="sm" onclick={clearFilters}>Clear filters</Button>
								{/if}
							{/snippet}
						</EmptyState>
					</div>
				{:else}
					<div class="h-full overflow-y-auto p-4">
						<div
							class="grid gap-3 {$libraryCardDensity === 'dense'
								? 'grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2'
								: 'grid-cols-[repeat(auto-fill,minmax(300px,1fr))]'}"
							role="list"
							aria-label="Recipe catalog"
						>
							{#each filteredRecipes as recipe (recipe.id)}
								<RecipeCard
									{recipe}
									readiness={readinessFor(recipe.id)}
									dense={$libraryCardDensity === 'dense'}
									onOpen={openRecipe}
								/>
							{/each}
						</div>
					</div>
				{/if}
			</LibraryShell>
	</div>
</div>
