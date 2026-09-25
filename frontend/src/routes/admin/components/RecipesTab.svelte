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
	import { parseActiveRecipeRunConflict, type ActiveRecipeRunConflict } from '$lib/utils/recipeRunConflict';
	import {
		isRunTerminal,
		shouldPollRun,
		runStatusLabel,
		RUN_POLL_INTERVAL_MS
	} from '$lib/utils/setupRunDisplay';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import RecipeRunProgress from '$lib/components/recipes/RecipeRunProgress.svelte';
	import { adminRecipeRunActions } from '$lib/components/recipes/runActions';
	import { Alert, Badge, Button, EmptyState, Spinner } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryDensityToggle from '$lib/components/library/LibraryDensityToggle.svelte';
	import { libraryCardDensity } from '$lib/components/library/libraryCardDensity';
	import { deriveRecipeReadiness, type RecipeReadinessBadge } from './recipeReadinessBadge';
	import { mergeRunHistory } from './recipeRunHistory';
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
	import RecipeArtifactCards from './recipes/RecipeArtifactCards.svelte';
	import RecipeRunsList from './recipes/RecipeRunsList.svelte';
	import RecipeStepsList from './recipes/RecipeStepsList.svelte';
	import PresetCoverTile from './presets/PresetCoverTile.svelte';

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
	const finishedRunsHandled = new Set<string>();

	let runs = $state<RecipeRun[]>([]);
	let runsLoading = $state(false);
	let runsError = $state('');
	let activeRun = $state<RecipeRun | null>(null);
	let starting = $state(false);
	let startError = $state('');
	let startConflict = $state<ActiveRecipeRunConflict | null>(null);
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
		startConflict = null;
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
		if (shouldPollRun(updated.status)) {
			schedulePoll(updated.id);
			return;
		}
		clearPoll();
		if (isRunTerminal(updated.status)) void handleRunFinished(updated);
	}

	async function handleRunFinished(run: SetupRun) {
		if (finishedRunsHandled.has(run.id)) return;
		finishedRunsHandled.add(run.id);
		await loadRecipes(true);
		readinessQueued.delete(run.recipe_id);
		enqueueReadiness(run.recipe_id);
		if (run.recipe_id === selectedRecipeId) await loadRuns(run.recipe_id);
	}

	async function refreshRun(runId: string) {
		try {
			adoptRun(await api.getRecipeRun(runId));
		} catch (error) {
			logger.warn('Recipe run poll failed', runId, error);
			schedulePoll(runId);
		}
	}

	async function startRun() {
		if (!selectedRecipe || starting) return;
		starting = true;
		startError = '';
		startConflict = null;
		try {
			adoptRun(await api.createRecipeRun(selectedRecipe.id));
		} catch (error) {
			const conflict = parseActiveRecipeRunConflict(error);
			if (conflict && conflict.activeRun.recipeId === selectedRecipe.id) {
				try {
					adoptRun(await api.getRecipeRun(conflict.activeRun.id));
				} catch (attachError) {
					startError = getApiErrorMessage(attachError, 'Could not attach to the running recipe');
				}
			} else if (conflict) {
				startConflict = conflict;
			} else {
				startError = getApiErrorMessage(error, 'Could not start this recipe');
			}
		} finally {
			starting = false;
		}
	}

	async function cancelStartConflict() {
		if (!startConflict) return;
		try {
			await api.applyRecipeRunAction(startConflict.activeRun.id, 'cancel');
			startConflict = null;
		} catch (error) {
			startError = getApiErrorMessage(error, 'Could not cancel the other run');
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
								{/snippet}
							</DetailHeader>

							<DetailBody>
								<DetailLayout>
									{#snippet lead()}
										<div class="space-y-4">
											{#if detailLoading && !detail}
												<div class="flex justify-center py-10"><Spinner size="md" /></div>
											{:else if detailError}
												<Alert variant="danger" density="compact" title="Couldn't load this recipe">
													{detailError}
												</Alert>
											{/if}

											{#if startConflict}
												<div data-recipe-run-conflict>
													<Alert variant="warning" density="compact" title="Another recipe is running">
														{startConflict.activeRun.recipeName} is still running ({runStatusLabel(
															startConflict.activeRun.status
														).toLowerCase()}).
														{#snippet actions()}
															<div class="flex items-center gap-2">
																<Button
																	variant="secondary"
																	size="sm"
																	onclick={() => openRecipe(startConflict!.activeRun.recipeId)}
																>
																	Open it
																</Button>
																<Button variant="secondary" size="sm" onclick={cancelStartConflict}>Cancel it</Button>
															</div>
														{/snippet}
													</Alert>
												</div>
											{:else if startError}
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
												{#if (detail?.presets ?? []).length > 0}
													<div class="mt-3 border-t border-line pt-3">
														<p class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">
															Sets up
														</p>
														<div class="flex flex-wrap gap-1.5" data-recipe-sets-up>
															{#each detail?.presets ?? [] as linkedPreset (linkedPreset.id)}
																<a
																	class="flex items-center gap-1.5 py-1 pl-1 pr-2 hover:border-line-hover {DETAIL_INSET_CLASS}"
																	href="/admin?tab=presets&id={encodeURIComponent(linkedPreset.id)}"
																>
																	<PresetCoverTile
																		presetId={linkedPreset.id}
																		presetName={linkedPreset.name}
																		cover={linkedPreset.cover_url ? `${api.getBaseURL()}${linkedPreset.cover_url}` : null}
																		class="h-5 w-5 rounded"
																	/>
																	<span class="max-w-[10rem] truncate text-xs text-fg">{linkedPreset.name}</span>
																	{#if linkedPreset.installed}
																		<Badge variant="success" size="sm" dot>installed</Badge>
																	{/if}
																</a>
															{/each}
														</div>
													</div>
												{/if}
											</DetailSection>
										</div>
									{/snippet}

									{#snippet main()}
										{#if detail}
											<DetailSection label="Run">
												{#if activeRun}
													<RecipeRunProgress
														run={activeRun}
														title="Installing models"
														actions={adminRecipeRunActions}
														onRunUpdated={adoptRun}
													/>
												{:else}
													<div class="space-y-3">
														<p class="text-sm text-fg-muted">
															{selectedRecipe.total_download_bytes != null
																? `This recipe downloads ~${formatBytes(selectedRecipe.total_download_bytes)} before it can generate.`
																: 'This recipe is ready to install.'}
														</p>
														<RecipeStepsList steps={detail.steps} />
													</div>
												{/if}
											</DetailSection>

											<DetailSection label="Runs">
												{#if runsLoading && runHistory.length === 0}
													<div class="flex justify-center py-6"><Spinner size="sm" /></div>
												{:else if runsError}
													<p class="text-sm text-danger">{runsError}</p>
												{:else}
													<RecipeRunsList runs={runHistory} />
												{/if}
											</DetailSection>
										{/if}
									{/snippet}

									{#snippet aside()}
										{#if detail}
											<DetailSection label="Artifacts">
												{#if detail.artifacts.length === 0}
													<p class="text-sm text-fg-muted">This recipe downloads nothing.</p>
												{:else}
													<RecipeArtifactCards artifacts={detail.artifacts} />
												{/if}
											</DetailSection>
										{/if}
									{/snippet}
								</DetailLayout>
							</DetailBody>

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
							class="grid gap-3 {$libraryCardDensity === 'compact'
								? 'grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2'
								: 'grid-cols-[repeat(auto-fill,minmax(300px,1fr))]'}"
							role="list"
							aria-label="Recipe catalog"
						>
							{#each filteredRecipes as recipe (recipe.id)}
								<RecipeCard
									{recipe}
									readiness={readinessFor(recipe.id)}
									dense={$libraryCardDensity === 'compact'}
									onOpen={openRecipe}
								/>
							{/each}
						</div>
					</div>
				{/if}
			</LibraryShell>
	</div>
</div>
