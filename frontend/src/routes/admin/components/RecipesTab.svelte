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
	import { MasterDetailLayout, DetailEmptyState } from '$lib/components/master-detail';
	import { Pane, PaneRow, PaneGroupHeader } from '$lib/components/pane';
	import { DetailHeader, DetailBody, DetailSection, DetailFooter } from '$lib/components/detail';
	import RecipeRunProgress from '$lib/components/recipes/RecipeRunProgress.svelte';
	import { adminRecipeRunActions } from '$lib/components/recipes/runActions';
	import { Alert, Badge, Button, EmptyState, Input, Spinner } from '$lib/components/ui';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import { deriveRecipeReadiness, type RecipeReadinessBadge } from './recipeReadinessBadge';
	import { runDuration, runStartedLabel, mergeRunHistory } from './recipeRunHistory';
	import { readRecipesUrlState, writeRecipesUrlState } from './recipesUrlState';

	const RUN_HISTORY_LIMIT = 20;
	/** How many per-row readiness probes may be in flight at once — the list can
	 * be long and each probe is a real backend round trip. */
	const READINESS_CONCURRENCY = 3;

	let recipes: RecipeSummary[] = $state([]);
	let loading = $state(true);
	let refreshing = $state(false);
	let loadError = $state('');

	let query = $state('');
	let selectedEngine = $state<string | null>(null);
	let selectedCategory = $state<string | null>(null);
	let selectedSource = $state<string | null>(null);

	let selectedRecipeId = $state('');
	let detail = $state<RecipeDetail | null>(null);
	let detailLoading = $state(false);
	let detailError = $state('');
	let detailRequestVersion = 0;

	/** Readiness per recipe id, filled in lazily by the bounded worker below.
	 * A key present with `null` means "probed and failed" — still unknown, but
	 * never re-queued. */
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

	const engines = $derived(
		[...new Set(recipes.map((r) => r.engine).filter(Boolean))].sort((a, b) => a.localeCompare(b))
	);
	const categories = $derived(
		[...new Set(recipes.map((r) => r.category).filter(Boolean))].sort((a, b) => a.localeCompare(b))
	);
	const sources = $derived(
		[...new Set(recipes.map((r) => r.source).filter(Boolean))].sort((a, b) => a.localeCompare(b))
	);

	const filteredRecipes = $derived(
		recipes.filter((recipe) => {
			const needle = query.trim().toLowerCase();
			if (needle) {
				const haystack = [recipe.name, recipe.summary, recipe.engine, recipe.id, recipe.preset_name]
					.filter(Boolean)
					.join(' ')
					.toLowerCase();
				if (!haystack.includes(needle)) return false;
			}
			if (selectedEngine && recipe.engine !== selectedEngine) return false;
			if (selectedCategory && recipe.category !== selectedCategory) return false;
			if (selectedSource && recipe.source !== selectedSource) return false;
			return true;
		})
	);

	const groupedRecipes = $derived.by(() => {
		const groups = new Map<string, RecipeSummary[]>();
		for (const recipe of filteredRecipes) {
			const key = recipe.category || 'other';
			groups.set(key, [...(groups.get(key) || []), recipe]);
		}
		return [...groups.entries()]
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([category, items]) => ({ category, recipes: items }));
	});

	const activeFilterCount = $derived(
		Number(!!query.trim()) +
			Number(!!selectedEngine) +
			Number(!!selectedCategory) +
			Number(!!selectedSource)
	);

	const selectedRecipe = $derived(recipes.find((r) => r.id === selectedRecipeId) || null);
	const installedCount = $derived(recipes.filter((r) => r.last_completed_at).length);
	const runHistory = $derived(mergeRunHistory(runs, activeRun));
	const runInFlight = $derived(!!activeRun && !isRunTerminal(activeRun.status));

	function readinessFor(recipeId: string): RecipeReadinessBadge {
		return deriveRecipeReadiness(readinessById[recipeId] ?? null);
	}

	function categoryLabel(category: string): string {
		if (category === '3d') return '3D';
		return category.charAt(0).toUpperCase() + category.slice(1);
	}

	function categoryIcon(category: string): string {
		if (category === 'image') return 'photo';
		if (category === 'video') return 'film';
		if (category === 'audio') return 'audio';
		if (category === '3d') return 'cube';
		if (category === 'utility') return 'wand';
		return 'list-checks';
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
					// A probe that fails leaves the row "unknown" rather than
					// blocking the rest of the list.
					logger.warn('Recipe readiness probe failed', recipeId, error);
					readinessById = { ...readinessById, [recipeId]: null };
				}
			}
		} finally {
			readinessWorkers -= 1;
		}
	}

	function selectRecipe(id: string) {
		if (id === selectedRecipeId) return;
		selectedRecipeId = id;
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
	}

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

	/** Adopt a run and keep the poll in step with whatever status it carries.
	 * Also the `onRunUpdated` handler `RecipeRunProgress` calls. */
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
				// A finished run changes both the catalog's "installed" marker and
				// what readiness reports, so re-read them once rather than leaving
				// stale badges behind.
				await loadRecipes(true);
				readinessQueued.delete(fetched.recipe_id);
				enqueueReadiness(fetched.recipe_id);
				if (fetched.recipe_id === selectedRecipeId) await loadRuns(fetched.recipe_id);
			}
		} catch (error) {
			// Fail soft: keep the last-known run on screen and try again shortly.
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

	function clearFilters() {
		query = '';
		selectedEngine = null;
		selectedCategory = null;
		selectedSource = null;
	}

	onMount(async () => {
		await loadRecipes();
		const { recipeId } = readRecipesUrlState($page.url.searchParams);
		if (recipeId && recipes.some((r) => r.id === recipeId)) selectRecipe(recipeId);
		else if (recipes.length > 0) selectRecipe(recipes[0].id);
		urlRestored = true;
	});

	onDestroy(() => clearPoll());

	// Mirror the selection into `?recipe=`, but only once the mount-time
	// restore has run — otherwise this would strip the param off a freshly
	// deep-linked URL before it was ever read.
	$effect(() => {
		if (!urlRestored) return;
		const nextUrl = writeRecipesUrlState($page.url, { recipeId: selectedRecipeId || null });
		if (nextUrl.search !== $page.url.search) {
			void goto(nextUrl, { replaceState: true, keepFocus: true, noScroll: true });
		}
	});
</script>

<div
	class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]"
>
	<AdminTabShell
		title="Recipes"
		icon="list-checks"
		counts={[
			{ label: 'recipes', value: recipes.length },
			{ label: 'installed', value: installedCount, tone: 'success' }
		]}
	>
		{#snippet actions()}
			<Button
				variant="secondary"
				size="sm"
				icon="refresh"
				loading={refreshing}
				onclick={() => loadRecipes(true)}
			>
				Refresh
			</Button>
		{/snippet}
	</AdminTabShell>

	{#snippet recipeSearch()}
		<div class="relative">
			<Icon
				name="search"
				className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
			/>
			<Input
				bind:value={query}
				type="search"
				class="pl-9"
				placeholder="Search recipes by name, engine, or preset…"
				aria-label="Search recipes"
			/>
		</div>
	{/snippet}

	{#snippet recipeFilters()}
		{#if engines.length}
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase text-fg-subtle">Engine</span>
				<select class="input w-40" bind:value={selectedEngine} aria-label="Filter by engine">
					<option value={null}>All engines</option>
					{#each engines as engine}<option value={engine}>{engine}</option>{/each}
				</select>
			</div>
		{/if}
		{#if categories.length}
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase text-fg-subtle">Type</span>
				<select class="input w-40" bind:value={selectedCategory} aria-label="Filter by type">
					<option value={null}>All types</option>
					{#each categories as category}
						<option value={category}>{categoryLabel(category)}</option>
					{/each}
				</select>
			</div>
		{/if}
		{#if sources.length}
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase text-fg-subtle">Source</span>
				<select class="input w-40" bind:value={selectedSource} aria-label="Filter by source">
					<option value={null}>All sources</option>
					{#each sources as source}<option value={source}>{categoryLabel(source)}</option>{/each}
				</select>
			</div>
		{/if}
	{/snippet}

	{#snippet recipeFiltersTrailing()}
		<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">
			{filteredRecipes.length}
			{filteredRecipes.length === 1 ? 'recipe' : 'recipes'}
		</span>
	{/snippet}

	<AdminFilterBar
		search={recipeSearch}
		filters={recipeFilters}
		trailing={recipeFiltersTrailing}
		activeCount={activeFilterCount}
		onClear={clearFilters}
	/>

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if loading}
			<div class="h-full flex flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading recipes…</p>
			</div>
		{:else if loadError && recipes.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState title="Recipes unavailable" description={loadError} icon="warning" compact>
					{#snippet actions()}
						<Button variant="secondary" size="sm" icon="refresh" onclick={() => loadRecipes()}>
							Try again
						</Button>
					{/snippet}
				</EmptyState>
			</div>
		{:else if recipes.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState
					title="No recipes available"
					description="Recipes ship with the app and with plugins. Enable a plugin that provides one, or add a local recipe, then refresh."
					icon="list-checks"
					compact
				/>
			</div>
		{:else}
			<MasterDetailLayout
				leftWidth={360}
				minWidth={300}
				maxWidth={480}
				storageKey="admin-recipes-width"
			>
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Browse recipes"
						count={filteredRecipes.length}
						isEmpty={filteredRecipes.length === 0}
						bodyRole="listbox"
						ariaLabel="Recipe catalog"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState
									title="No matching recipes"
									description="Try a different name, engine, type, or source."
									icon="search"
									compact
								>
									{#snippet actions()}
										<Button variant="ghost" size="sm" onclick={clearFilters}>Clear filters</Button>
									{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#each groupedRecipes as group (group.category)}
								<PaneGroupHeader
									icon={categoryIcon(group.category)}
									label={categoryLabel(group.category)}
									count={group.recipes.length}
								/>
								{#each group.recipes as recipe (recipe.id)}
									{@const badge = readinessFor(recipe.id)}
									{#snippet recipeTrailing()}
										<div class="flex flex-col items-end gap-1">
											<Badge variant={badge.variant} size="sm">{badge.label}</Badge>
											{#if recipe.last_completed_at}
												<Badge variant="success" size="sm" dot>installed</Badge>
											{/if}
										</div>
									{/snippet}
									<PaneRow
										selected={selectedRecipeId === recipe.id}
										onclick={() => selectRecipe(recipe.id)}
										title={recipe.name}
										subtitle="{recipe.engine || 'unknown engine'} · {recipe.step_count} step{recipe.step_count ===
										1
											? ''
											: 's'}"
										subtitleMono
										trailing={recipeTrailing}
									/>
								{/each}
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if selectedRecipe}
						{@const badge = readinessFor(selectedRecipe.id)}
						<DetailHeader title={selectedRecipe.name} icon="list-checks">
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
										{#each detail.load_errors as loadError}
											<li class="font-mono text-2xs">{loadError}</li>
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
													<p class="font-mono text-2xs text-fg-subtle mt-0.5">{step.kind}</p>
													{#if step.onboarding_only}
														<p class="text-2xs text-fg-subtle mt-0.5">
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
														<th class="font-normal font-mono text-2xs uppercase pb-2">Model</th>
														<th class="font-normal font-mono text-2xs uppercase pb-2">Type</th>
														<th class="font-normal font-mono text-2xs uppercase pb-2 text-right">
															Size
														</th>
														<th class="font-normal font-mono text-2xs uppercase pb-2 text-right">
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
																				class="text-2xs text-signal hover:underline shrink-0"
																			>
																				licence
																			</a>
																		{/if}
																	{/if}
																</span>
															</td>
															<td class="py-1.5 pr-3 font-mono text-2xs text-fg-muted">
																{artifact.model_type}
															</td>
															<td class="py-1.5 pr-3 font-mono tabular-nums text-fg-muted text-right">
																{artifact.size_bytes != null ? formatBytes(artifact.size_bytes) : '—'}
															</td>
															<td class="py-1.5 text-right">
																{#if artifact.required}
																	<Badge variant="neutral" size="sm">required</Badge>
																{:else}
																	<span class="text-2xs text-fg-subtle">optional</span>
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
													<span class="font-mono text-2xs text-fg-subtle shrink-0 truncate">
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
													<span class="font-mono text-2xs tabular-nums text-fg-muted truncate">
														{runStartedLabel(run)}
													</span>
													<Badge variant="neutral" size="sm">{run.mode}</Badge>
												</div>
												<span class="font-mono text-2xs tabular-nums text-fg-subtle shrink-0">
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
					{:else}
						<DetailEmptyState message="Select a recipe to view details" />
					{/if}
				</div>
			</MasterDetailLayout>
		{/if}
	</section>
</div>
